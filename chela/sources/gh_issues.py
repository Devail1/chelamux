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


class GhIssuesSource:
    """Pull open work from `gh issue list` — sibling to the markdown source.

    Duck-typed identically to MarkdownSource: __init__(wf) + list_open_tasks().
    Config (workflow front matter, `tracker:` block):
        kind:                gh_issues
        repo:                owner/name   (optional — defaults to the repo the
                             WORKFLOW.md lives in, resolved at first use)
        blocked_label:       blocked      (optional — issues carrying this
                             label are skipped, mirroring the markdown
                             source's <!-- blocked marker)
        allowed_associations: [OWNER, MEMBER, COLLABORATOR]  (optional —
                             overrides the default trust gate below)

    Trust gate (load-bearing — do not remove): on a public repo, anyone can
    open an issue, and this class turns every issue it returns into a
    dispatchable agent run. Without a gate, an anonymous issue is remote code
    execution against the operator's machine. So by default only issues
    authored by someone GitHub reports as OWNER, MEMBER, or COLLABORATOR
    (i.e. someone with at least write access to the repo) are eligible;
    everyone else's issues (CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, NONE, ...)
    are silently dropped, same as a blocked-label issue.
    """

    DEFAULT_ALLOWED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

    def __init__(self, wf: WorkflowDef):
        self.workflow_path = wf.path
        self._repo_cfg = wf.get("tracker", "repo")
        self.blocked_label = wf.get("tracker", "blocked_label", default="blocked")
        associations_cfg = wf.get("tracker", "allowed_associations")
        if associations_cfg:
            self.allowed_associations = frozenset(
                str(a).upper() for a in associations_cfg
            )
        else:
            self.allowed_associations = self.DEFAULT_ALLOWED_ASSOCIATIONS
        self._repo: str | None = None

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
        try:
            out = subprocess.run(
                [
                    "gh", "issue", "list", "--repo", repo, "--state", "open",
                    "--json", "number,title,url,labels,authorAssociation",
                    "--limit", "200",
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
            association = str(issue.get("authorAssociation") or "").upper()
            if association not in self.allowed_associations:
                log.info(
                    "gh_issues: skipping %s#%d — author association %r not in allow-list",
                    repo, number, association,
                )
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
