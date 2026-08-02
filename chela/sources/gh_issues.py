from __future__ import annotations
import hashlib
import json
import logging
import subprocess

from chela.sources import Task
from chela.workflow import WorkflowDef

log = logging.getLogger(__name__)

# Done semantics (mirrors the markdown source): the dispatcher needs no change.
# A closed issue drops out of `list_open_tasks`, so reconcile transitions the
# run exactly like a struck TODO line. NOTE for workflow authors: a gh_issues
# workflow's prompt body should instruct the agent to open a PR with
# `Closes #<n>` (so merging the PR closes the issue) rather than "strike the
# TODO line" — there is no tracker file to edit.


# Distinguishes "the key is absent" (fail closed) from an explicit `require_label: false`
# (a deliberate, recorded opt-out). A plain `default=None` cannot tell those apart.
_UNSET = object()

# One ERROR per (repo, reason) per process. A misconfigured tracker stops the queue
# on EVERY tick, and re-logging that 1440 times a day would bury it; `doctor` carries
# the standing signal (see chela.runtime_truth's dispatch.workflows fact).
_reported: set[tuple[str, str]] = set()


def _report_once(key: tuple[str, str], message: str) -> None:
    if key not in _reported:
        _reported.add(key)
        log.error("%s", message)


class GhIssuesSource:
    """Pull open work from `gh issue list` — sibling to the markdown source.

    Duck-typed identically to MarkdownSource: __init__(wf) + list_open_tasks().
    Config (workflow front matter, `tracker:` block):
        kind:           gh_issues
        repo:           owner/name   (optional — defaults to the repo the
                        WORKFLOW.md lives in, resolved at first use)
        require_label:  ready-for-agent   (REQUIRED — see below)
        blocked_label:  blocked      (optional — issues carrying this label are
                        skipped, mirroring the markdown source's <!-- blocked
                        marker)
        trusted_authors: [login, ...] (optional — defence in depth)

    ⛔ **`require_label` is a SECURITY control, not a convenience.** Every task this
    source yields becomes an autonomously dispatched agent running in a git worktree
    on the operator's machine, with the operator's `gh` credentials. Before it existed
    this source returned EVERY open issue, so on a public repo anyone who could open
    an issue could run code on the operator's box.

    ⭐ A label is the right gate because **applying one requires write/triage
    permission on the repo** — an outsider can open an issue but cannot label it. So
    the check is enforced by GitHub, not by convention.

    Unconfigured fails CLOSED and LOUD: no tasks, an ERROR in the log, and an ERROR
    finding from `chela doctor`. It is never a silent empty queue — "no work" and
    "refusing to look" must not be indistinguishable. An operator who genuinely wants
    the old behaviour writes `require_label: false`, which is a recorded choice.
    """

    def __init__(self, wf: WorkflowDef):
        self.workflow_path = wf.path
        self._repo_cfg = wf.get("tracker", "repo")
        self.blocked_label = wf.get("tracker", "blocked_label", default="blocked")
        self._repo: str | None = None
        self.config_error: str | None = None

        raw = wf.get("tracker", "require_label", default=_UNSET)
        self.require_label: str | None = None
        if raw is _UNSET:
            self.config_error = (
                f"{wf.path}: tracker.require_label is not set. A gh_issues tracker turns "
                "open issues into autonomously dispatched agents on this machine, so "
                "without a required label anyone who can open an issue on this repo can "
                "run code here. Set `require_label: <label>` under `tracker:` (applying a "
                "label needs write access, which is what makes it a gate), or set "
                "`require_label: false` to accept that risk deliberately. Claiming NO "
                "work until then."
            )
        elif raw is False or raw is None:
            log.warning(
                "%s: gh_issues tracker.require_label is disabled — EVERY open issue on "
                "%s is dispatchable by anyone who can open one.",
                wf.path, self._repo_cfg or "this repo",
            )
        else:
            self.require_label = str(raw).strip() or None
            if self.require_label is None:
                self.config_error = (
                    f"{wf.path}: tracker.require_label is empty. Give it a label name, or "
                    "`false` to disable the gate deliberately. Claiming NO work until then."
                )

        authors = wf.get("tracker", "trusted_authors", default=None)
        self.trusted_authors: frozenset[str] = frozenset(
            str(a).strip() for a in authors if str(a).strip()
        ) if isinstance(authors, (list, tuple)) else frozenset()

    def _resolve_repo(self) -> str | None:
        """Determine owner/name. Explicit config wins; else infer from the repo
        the WORKFLOW.md lives in via `gh repo view`, falling back to parsing the
        git `origin` remote. Cached after first resolution."""
        if self._repo is not None:
            return self._repo
        if self._repo_cfg:
            self._repo = str(self._repo_cfg)
            return self._repo
        repo_dir = str(self.workflow_path.parent)
        # Preferred: gh knows the canonical nameWithOwner.
        try:
            out = subprocess.run(
                ["gh", "repo", "view", "--json", "nameWithOwner"],
                cwd=repo_dir, capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0:
                data = json.loads(out.stdout)
                nwo = data.get("nameWithOwner")
                if isinstance(nwo, str) and nwo:
                    self._repo = nwo
                    return self._repo
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
            pass
        # Fallback: parse owner/name out of the origin remote URL.
        try:
            remote = subprocess.run(
                ["git", "-C", repo_dir, "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=15,
            )
            if remote.returncode == 0:
                nwo = _parse_remote(remote.stdout.strip())
                if nwo:
                    self._repo = nwo
                    return self._repo
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        log.warning("gh_issues: could not resolve repo for %s", self.workflow_path)
        return None

    def list_open_tasks(self) -> list[Task]:
        repo = self._resolve_repo()
        if not repo:
            return []
        if self.config_error:
            # ⛔ Fail CLOSED. Returning [] alone would be indistinguishable from "no
            # open issues" — the defect this whole change exists to avoid — so the
            # refusal is also stated in the log and by `chela doctor`.
            _report_once((repo, "require_label"), self.config_error)
            return []
        try:
            out = subprocess.run(
                [
                    "gh", "issue", "list", "--repo", repo, "--state", "open",
                    # `author` powers the optional trusted_authors gate. NOTE: `gh issue
                    # list --json` has no `authorAssociation` field (checked), and the
                    # REST endpoint that does (`repos/{o}/{r}/issues`) also returns PULL
                    # REQUESTS — so switching there to get it would make the dispatcher
                    # claim its own PRs as tasks. `gh issue list` excludes them; stay here.
                    "--json", "number,title,url,labels,author", "--limit", "200",
                ],
                capture_output=True, text=True, timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            # Transient API/auth/CLI error degrades to "no open tasks" rather
            # than crashing the dispatcher tick.
            log.warning("gh_issues: `gh issue list` failed for %s: %s", repo, e)
            return []
        if out.returncode != 0:
            log.warning(
                "gh_issues: `gh issue list` exited %d for %s: %s",
                out.returncode, repo, (out.stderr or "").strip(),
            )
            return []
        try:
            issues = json.loads(out.stdout)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("gh_issues: bad JSON from `gh issue list` for %s: %s", repo, e)
            return []

        tasks: list[Task] = []
        for issue in issues:
            number = issue.get("number")
            if number is None:
                continue
            labels = {
                (lbl.get("name") if isinstance(lbl, dict) else lbl)
                for lbl in issue.get("labels") or []
            }
            if self.blocked_label and self.blocked_label in labels:
                continue
            # ⛔ THE GATE. Only someone with write/triage permission can apply a label,
            # so this is an authorization check GitHub enforces — not a convention.
            if self.require_label and self.require_label not in labels:
                continue
            # Defence in depth: a labelled issue from an unexpected account. Off unless
            # configured, because the label alone is already the authorization check.
            if self.trusted_authors:
                author = issue.get("author")
                login = author.get("login") if isinstance(author, dict) else None
                if login not in self.trusted_authors:
                    continue
            title = (issue.get("title") or "").strip()
            tasks.append(Task(
                id=_task_id(repo, number),
                # Clean title — the issue number lives in line_number, not here.
                title=title,
                # Non-filesystem source: no source file. There is nothing for
                # the dispatcher to strike either — a merged PR closes the
                # issue, so the task leaves list_open_tasks on its own (see
                # dispatcher._strike_merged_tasks).
                file="",
                line_number=int(number),
                raw=issue.get("url") or "",
            ))
        return tasks


def _task_id(repo: str, number: int) -> str:
    # Keyed on the stable issue number, NOT the title. Unlike the markdown
    # source (where editing the line text mints a new task_id), renaming an
    # issue keeps the same task_id → same branch → same worktree.
    h = hashlib.sha1(f"gh:{repo}#{number}".encode()).hexdigest()
    return h[:12]


def _parse_remote(url: str) -> str | None:
    """Extract `owner/name` from a git remote URL (ssh or https), if possible."""
    if not url:
        return None
    # Strip a trailing .git
    if url.endswith(".git"):
        url = url[:-4]
    # git@github.com:owner/name
    if ":" in url and "@" in url and not url.startswith("http"):
        path = url.split(":", 1)[1]
    elif "://" in url:
        # https://github.com/owner/name
        path = url.split("://", 1)[1]
        path = path.split("/", 1)[1] if "/" in path else ""
    else:
        path = url
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return None
