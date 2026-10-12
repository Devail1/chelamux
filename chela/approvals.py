"""⚖️ Pending approvals — every request waiting on the operator, in one list (CMX-103).

The one-tap override (CMX-79) reached an operator only through the Telegram card or the CLI
output of whoever filed it, so a dashboard-only operator never learned a request was waiting
and it sat until it expired. This module is the read side the dashboard lists on every page:
one row per request, already carrying WHY it cannot be approved right now (``blocked``), so
the page shows the reason instead of a button that would fail.

It decides nothing. :func:`decide` only routes an id to its OWN kind's decide path — for an
override that is :func:`chela.overrides.decide`, the same call the ``/override/<id>`` page,
the Telegram button and ``chela merge-approve`` make. There is no second approve path.

🔌 **Hook for release requests (CMX-87).** A request kind is one entry in :data:`KINDS`: an id
prefix, a ``rows()`` function and a ``decide()`` function. When ``chela release`` lands its
request record, it adds ``"release"`` here (its rows carry ``kind: "release"`` and a
``version``) and the dashboard lists and decides it with no other change.
"""
from __future__ import annotations

import time

from chela import overrides

# GitHub's mergeability values that will make the merge refuse. UNKNOWN / unread are NOT
# here: GitHub is still computing, and the approve re-reads it live anyway.
_UNMERGEABLE = ("CONFLICTING",)


def _override_blocked(rec: dict, run: dict | None, now: float) -> str | None:
    """Why approving this override would fail RIGHT NOW, from what chela already knows —
    or None. The approve re-checks all of it live (``contract.complete_override``); this is
    only so the dashboard never offers a button it knows will fail."""
    if (rec.get("deadline") or 0) <= now:
        return "expired — it was not decided in time"
    if run is None:
        return "the run no longer exists"
    head = run.get("pr_head_sha")
    if head and head != rec.get("head_sha"):
        return (f"the PR head moved ({str(rec.get('head_sha'))[:12]} → {head[:12]}) — "
                "request a new override for the new head")
    if (run.get("pr_state") or "open").lower() != "open":
        return f"the PR is {run.get('pr_state')}"
    from chela import dispatcher
    if run.get("pr_mergeable") in _UNMERGEABLE:
        return "GitHub reports the PR conflicts with its base — not MERGEABLE"
    checks = run.get("pr_checks")
    if checks and checks != dispatcher.CI_PASSING:
        return f"CI is {checks}, not green"
    return None


def _run_of(task_id: str | None) -> dict | None:
    from chela import dispatcher
    return dispatcher.resolve_run(task_id) if task_id else None


def override_rows(now: float | None = None, run_of=None) -> list[dict]:
    """The pending override requests as approval rows, oldest first."""
    now = time.time() if now is None else now
    run_of = run_of or _run_of
    rows = []
    for rec in overrides.all_requests():
        if rec.get("state") != overrides.PENDING:
            continue
        try:
            run = run_of(rec.get("task_id"))
        except Exception:                                    # noqa: BLE001 — best effort
            run = None
        created = rec.get("created") or now
        deadline = rec.get("deadline") or now
        rows.append({
            "kind": "override",
            "id": rec["id"],
            "what": (f"override-merge {rec.get('label') or rec.get('task_id')} past "
                     f"{rec.get('judge_state') or 'a judge that never ran'} on "
                     f"{str(rec.get('head_sha') or '?')[:12]}"),
            "label": rec.get("label") or rec.get("task_id"),
            "task_id": rec.get("task_id"),
            "pr_url": rec.get("pr_url"),
            "base": rec.get("base"),
            "head_sha": rec.get("head_sha"),
            "judge_state": rec.get("judge_state"),
            "reason": rec.get("reason"),
            "actor": rec.get("actor"),
            "created": created,
            "deadline": deadline,
            "age_s": max(0.0, now - created),
            "expires_in_s": max(0.0, deadline - now),
            "blocked": _override_blocked(rec, run, now),
        })
    return rows


def _override_decide(request_id: str, approve: bool, by: str, channel: str) -> dict:
    return overrides.decide(request_id, approve, by=by, channel=channel)


# kind → (id prefix, rows(), decide()). Add "release" here when CMX-87 lands (see above).
KINDS: dict[str, tuple[str, object, object]] = {
    "override": ("override-", override_rows, _override_decide),
}


def kind_of(request_id: str) -> str | None:
    for kind, (prefix, _rows, _decide) in KINDS.items():
        if (request_id or "").startswith(prefix):
            return kind
    return None


def pending(now: float | None = None) -> list[dict]:
    """Every request waiting on the operator, of every kind, oldest first. A request past
    its deadline is NOT listed (call ``overrides.sweep`` first to retire it for good)."""
    now = time.time() if now is None else now
    rows = []
    for _prefix, rows_fn, _decide in KINDS.values():
        rows.extend(r for r in rows_fn(now) if r["deadline"] > now)
    return sorted(rows, key=lambda r: r["created"])


def find(request_id: str, now: float | None = None) -> dict | None:
    return next((r for r in pending(now) if r["id"] == request_id), None)


def decide(request_id: str, approve: bool, *, by: str, channel: str) -> dict:
    """Route one decision to its kind's OWN decide path. ``{ok, state, detail, …}``."""
    kind = kind_of(request_id)
    if kind is None:
        return {"ok": False, "state": None, "detail": "no such request"}
    return KINDS[kind][2](request_id, approve, by, channel)


def snapshot() -> list[list]:
    """``[[id, blocked], …]`` for every pending request — cheap (JSON + one sqlite read per
    request, no tmux/gh). The dashboard's SSE loop diffs it to push an ``approvals`` frame
    the moment one is filed or decided, or turns approvable / stale."""
    return [[r["id"], r["blocked"]] for r in pending()]
