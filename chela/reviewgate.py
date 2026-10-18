"""🔎 CMX-209: the hand-in self-review gate. Before ``chela task-finished`` the agent runs
``/code-review`` on its own diff and writes a ``## Review`` section into its PR body (counts,
then one line per finding: fixed / dismissed with a reason / pre-existing). ``task-finished``
REFUSES a code PR whose body has none, unless ``--no-review-reason`` (recorded on the run).
A prose-only PR is exempt — there is no code for a bug-finding review to read."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

GH_TIMEOUT = 30

# A ``## Review`` heading (exactly level 2) with at least one non-blank line before the next
# level-1/2 heading or the end — an empty section is a dodge, not a review. ``### …``
# sub-headings inside the section stay inside it.
_SECTION = re.compile(r"^##[ \t]+Review[ \t]*\r?$(.*?)(?=^#{1,2}[ \t]|\Z)", re.M | re.S)

REFUSAL = (
    "this PR's body has no `## Review` section. Run `/code-review` on your own diff vs "
    "origin/<base> (effort medium), fix each Important finding or dismiss it with a reason, "
    "then add `## Review` to the PR body: counts first (\"3 findings: 2 fixed, 1 dismissed\" "
    "or \"0 findings\"), then one line per finding (file:line, verdict, reason). Re-run "
    "`chela request-push` with the new --pr-body-file (or `gh pr edit` an open PR). Only if "
    "no review can run, pass --no-review-reason '<why>' (recorded).")


def has_review(body: str) -> bool:
    """Whether ``body`` carries a non-empty ``## Review`` section."""
    return any(m.group(1).strip() for m in _SECTION.finditer(body or ""))


def read_body(worktree: str | Path, pr_url: str | None, push_marker: Path) -> str:
    """The PR body this hand-in will be judged on. An open PR (``pr_url`` on the run row) is
    authoritative — its body is read from GitHub; otherwise the ``pr_body`` the pending
    ``chela request-push`` marker will open it with. Raises RuntimeError when neither can be
    read: an unknown body must never read as reviewed."""
    if pr_url:
        cmd = ["gh", "pr", "view", pr_url, "--json", "body", "-q", ".body"]
        try:
            view = subprocess.run(cmd, cwd=str(worktree), capture_output=True, text=True,
                                  timeout=GH_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"`gh pr view` could not run: {e}") from e
        if view.returncode != 0:
            raise RuntimeError(f"`gh pr view {pr_url}` failed: {view.stderr.strip()[:200]}")
        return view.stdout
    try:
        marker = json.loads(push_marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError("no PR is open and no `chela request-push` is pending "
                           f"({e}) — request the push with --pr-body-file first") from e
    body = marker.get("pr_body") if isinstance(marker, dict) else None
    if not isinstance(body, str):
        raise RuntimeError("the pending `chela request-push` carries no PR body")
    return body


def render(record: dict) -> str:
    """The PR-body / judge-prompt note for a hand-in without a review ("" when none)."""
    if record.get("no_review_reason"):
        return f"🔎 **No self-review:** agent's reason: {record['no_review_reason']}"
    return ""
