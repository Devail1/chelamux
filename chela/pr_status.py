"""What state is a transcript's PR in — open, draft, merged or closed? (CMX-41)

Claude Code keeps appending ``{"type": "pr-link", ...}`` records to a session's
transcript for as long as that session touches the PR's branch — long after the PR
merged. :func:`chela.transcripts.latest_pr` reads the newest one, so a pane that pushed
a PR days ago still carries it, and the Wall used to render "✓ PR ready for review" from
its presence alone. This module answers the one question that claim rests on.

Sources, cheapest first:

1. **Run records** (``dispatcher.list_runs``). A dispatched PR's row already carries
   ``pr_state``. Only a TERMINAL value (merged / closed) is trusted from there: a row's
   ``open`` can lag the PR by a reconcile tick, and it never says whether the PR is a draft.
2. **``gh pr view <url> --json state,isDraft``**, cached per URL for :data:`TTL_SECONDS`.
   ``/api/agents`` is polled every few seconds for every pane; without the cache that is
   one ``gh`` spawn per pane per tick.

Any failure — no ``gh``, no network, an unparsable answer — is :data:`UNKNOWN`, cached
too (a dead network must not turn into a spawn storm). ⛔ Unknown is fail-CLOSED: the
caller must never read it as "open". Only a confirmed ``open`` + not-draft earns the
"ready for review" claim.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time

log = logging.getLogger(__name__)

OPEN, MERGED, CLOSED, UNKNOWN = "open", "merged", "closed", "unknown"
_STATES = (OPEN, MERGED, CLOSED)

TTL_SECONDS = 180.0         # a live (open/unknown) answer goes stale after this
GH_TIMEOUT_SECONDS = 8.0

_PR_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)(?:[/?#]|$)")

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _info(state: str, draft: bool = False) -> dict:
    return {"state": state, "draft": bool(draft) if state == OPEN else False}


def _gh_view(url: str) -> dict:
    """One ``gh pr view`` for the PR the URL NAMES (never resolved against a cwd's repo)."""
    m = _PR_URL_RE.search(url)
    if not m:
        return _info(UNKNOWN)
    owner, repo, number = m.groups()
    argv = ["gh", "pr", "view", number, "--repo", f"{owner}/{repo}", "--json", "state,isDraft"]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return _info(UNKNOWN)
    if out.returncode != 0:
        return _info(UNKNOWN)
    try:
        data = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return _info(UNKNOWN)
    if not isinstance(data, dict):
        return _info(UNKNOWN)
    state = str(data.get("state") or "").strip().lower()
    if state not in _STATES:
        return _info(UNKNOWN)
    return _info(state, data.get("isDraft") is True)


def runs_pr_states(runs: list[dict] | None) -> dict[str, str]:
    """``{pr_url: merged|closed}`` from run rows — the terminal states only (see module doc)."""
    out: dict[str, str] = {}
    for r in runs or ():
        url, state = r.get("pr_url"), r.get("pr_state")
        if url and state in (MERGED, CLOSED):
            out[str(url)] = state
    return out


def lookup(url: str | None, run_states: dict[str, str] | None = None) -> dict:
    """``{"state": open|merged|closed|unknown, "draft": bool}`` for a PR url.

    ``run_states`` is :func:`runs_pr_states` of the caller's run rows, read once per
    request. A terminal state there wins with no ``gh`` call; otherwise the cached
    ``gh`` answer, refreshed after :data:`TTL_SECONDS`. A merged PR is cached for good —
    it cannot un-merge.
    """
    if not url:
        return _info(UNKNOWN)
    terminal = (run_states or {}).get(url)
    if terminal:
        return _info(terminal)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
        if hit and (hit[1]["state"] == MERGED or now < hit[0]):
            return dict(hit[1])
    info = _gh_view(url)
    with _lock:
        _cache[url] = (time.monotonic() + TTL_SECONDS, info)
    return dict(info)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
