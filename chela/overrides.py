"""⚖️🔓 Override requests — ``chela merge --override`` as a REQUEST the operator approves later.

CMX-389 made the override a blocking wait: ``chela merge --override`` opened an approval
and sat in a poll loop for ``CHELA_OVERRIDE_WAIT_S`` (300s). In practice (CMX-79) the
requesting command ran inside a Claude Code tool call that timed out at 120s and went to
the background, and the operator then had to paste ``chela merge-approve <id>`` inside the
remaining window — the approval expired unapproved three times. So the two halves are now
DECOUPLED:

* :func:`chela.contract.request_override` runs the gate (everything but the judge), records
  ONE request here — bound to the PR's exact head SHA — and returns at once.
* :func:`decide` is the operator's half. Approve and the merge happens right there, in the
  approving process, through :func:`chela.contract.complete_override` — which re-reads
  GitHub at that moment (head still the recorded SHA, CI green, MERGEABLE, the autonomous
  base) and refuses on any miss. Deny, and nothing merges.
* :func:`sweep` retires requests nobody decided: the PR head moved past the recorded SHA
  (a new push invalidates the approval request), or the safety TTL ran out.

Each request is one JSON file under ``$CHELA_DIR/overrides/`` and every state change happens
under that request's own ``flock``. That lock is what makes "approve twice ⇒ one merge"
true across processes: the dashboard, the Telegram bridge and a terminal can all press
Approve, and exactly one of them moves the request out of ``pending``.

⛔ **Human-only.** The orchestrator may REQUEST; it may never approve. Three surfaces can
approve, and each proves a human its own way:

* the **Telegram button** — a callback query only a person pressing a button in the bound
  chat can produce (:mod:`chela.telegram.overridewatch`; a bot's tap is refused);
* the **dashboard** confirm page — a same-origin POST behind the dashboard's auth;
* a **plain terminal** (``chela merge-approve``) — and here, where nothing about the call
  itself proves a human, :func:`decide` refuses a process carrying the orchestrator's
  ``$CHELA_ACTOR`` stamp or Claude Code's ``CLAUDECODE`` session marker
  (:func:`agent_refusal`): that is an agent's tool call (or a shell spawned from one), by
  whatever spelling it reached this function.

On top of that the merge-gate hook (:mod:`chela.mergegate`) denies a session's
``chela merge-approve``, a ``curl`` to ``/override/``, and a ``python`` call into
``chela.overrides.decide``. The env check is deliberately NOT applied to the two service
channels: a pm2 service can inherit a Claude marker from the shell that started it (CMX-21),
and that must not silently disable the one-tap button the operator relies on.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from chela import config, event_log, notify

log = logging.getLogger("chela.overrides")

PENDING = "pending"
MERGING = "merging"      # approved; the merge is in flight in the approving process
MERGED = "merged"
DENIED = "denied"
EXPIRED = "expired"
FAILED = "failed"
FINAL_STATES = (MERGED, DENIED, EXPIRED, FAILED)

# The request stays answerable until it is decided or its head moves — this is only the
# backstop for a request everybody forgot. `CHELA_OVERRIDE_WAIT_S` keeps its name (it was the
# blocking wait before CMX-79) and is now this TTL.
DEFAULT_TTL_S = 24 * 3600.0
TTL_ENV = "CHELA_OVERRIDE_WAIT_S"

_ID_RE = re.compile(r"^override-[0-9a-f]{24}$")


def ttl() -> float:
    """Seconds a request stays pending at most — ``CHELA_OVERRIDE_WAIT_S`` (default 24h)."""
    try:
        value = float(os.environ.get("CHELA_OVERRIDE_WAIT_S", DEFAULT_TTL_S))
    except ValueError:
        value = DEFAULT_TTL_S
    return max(0.0, value)


def new_request_id() -> str:
    """An unguessable id — it is also the dashboard confirm page's URL."""
    return f"override-{secrets.token_hex(12)}"


def valid_id(request_id: str | None) -> bool:
    return bool(_ID_RE.match(request_id or ""))


def store_dir() -> Path:
    # Per call, not latched at import — see event_log.log_path().
    return config.CHELA_DIR / "overrides"


def _path(request_id: str) -> Path:
    return store_dir() / f"{request_id}.json"


@contextlib.contextmanager
def _locked(request_id: str):
    """Exclusive, cross-process lock on ONE request for a read-modify-write."""
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{request_id}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write(rec: dict) -> bool:
    path = _path(rec["id"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        log.warning("overrides: could not write %s", path, exc_info=True)
        return False


def load(request_id: str) -> dict | None:
    if not valid_id(request_id):
        return None
    try:
        data = json.loads(_path(request_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def all_requests() -> list[dict]:
    """Every request on disk, oldest first."""
    out = []
    try:
        paths = list(store_dir().glob("override-*.json"))
    except OSError:
        return out
    for path in paths:
        rec = load(path.stem)
        if rec is not None:
            out.append(rec)
    return sorted(out, key=lambda r: r.get("created") or 0)


def pending() -> list[dict]:
    """The requests still waiting on the operator (not past their TTL), oldest first."""
    now = time.time()
    return [r for r in all_requests()
            if r.get("state") == PENDING and (r.get("deadline") or 0) > now]


def create(meta: dict, *, ttl_s: float | None = None) -> dict | None:
    """Record a new PENDING request. ``meta`` must carry ``head_sha`` — the one commit this
    request is for. None if it could not be written (the caller then refuses)."""
    budget = ttl() if ttl_s is None else max(0.0, float(ttl_s))
    if budget <= 0 or not meta.get("head_sha"):
        return None
    now = time.time()
    rec = {**meta, "id": new_request_id(), "state": PENDING, "created": now,
           "deadline": now + budget, "ttl": budget, "decided_by": None,
           "decided_at": None, "detail": None, "merge_commit_sha": None}
    with _locked(rec["id"]):
        if not _write(rec):
            return None
    return rec


def set_fields(request_id: str, **fields) -> dict | None:
    """Merge ``fields`` into a request under its lock (the Telegram relay's bookkeeping)."""
    with _locked(request_id):
        rec = load(request_id)
        if rec is None:
            return None
        rec.update(fields)
        _write(rec)
        return rec


def _finish(request_id: str, state: str, detail: str | None, *, from_states,
            **extra) -> dict | None:
    """Move a request into a final ``state`` — only out of one of ``from_states``. Returns
    the updated record, or None when it was not in one of them (someone else got there)."""
    with _locked(request_id):
        rec = load(request_id)
        if rec is None or rec.get("state") not in from_states:
            return None
        rec.update(state=state, detail=detail, finished_at=time.time(), **extra)
        _write(rec)
        return rec


def agent_refusal(env=None) -> str | None:
    """Why THIS process may not approve an override — or None if it may.

    An approval is the operator's. A process carrying the auto-orchestrator's
    ``$CHELA_ACTOR`` stamp, or Claude Code's ``CLAUDECODE`` session marker, is an agent's
    tool call (or a shell spawned from one) — so it is refused here, whatever spelling got
    it to this function (``chela merge-approve``, a ``python -c``, the dashboard's code).
    """
    src = os.environ if env is None else env
    if (src.get(config.ACTOR_ENV) or "").strip():
        return (f"this process carries ${config.ACTOR_ENV}={src.get(config.ACTOR_ENV)!r} — "
                "the orchestrator may REQUEST an override, never approve one")
    if src.get("CLAUDECODE"):
        return ("this process runs inside a Claude Code session — an override is approved "
                "by the operator (the Telegram button, the dashboard, or a plain terminal), "
                "never from an agent's tool call")
    return None


def _tell(rec: dict, outcome: str, detail: str | None) -> None:
    """Tell the operator (push notification) and the orchestrator (inbox) how a request
    ended, when it did NOT merge. The Telegram message is edited by the bridge's relay."""
    label = rec.get("label") or rec.get("task_id")
    text = f"override of {label} {outcome}" + (f": {detail}" if detail else "")
    try:
        if notify.enabled():
            notify.send(f"{text}\n\nNothing was merged.", title=f"chela: override {outcome}")
    except Exception:                                        # noqa: BLE001 — best effort
        log.debug("override notify failed", exc_info=True)
    try:
        from chela import inbox
        inbox.enqueue(f"override_{outcome}",
                      f"⚖️ {text} — nothing was merged",
                      {"request_id": rec.get("id"), "task_id": rec.get("task_id"),
                       "pr_url": rec.get("pr_url"), "state": outcome, "detail": detail})
    except Exception:                                        # noqa: BLE001 — best effort
        log.debug("override inbox enqueue failed", exc_info=True)


def _expire(rec: dict, detail: str) -> dict | None:
    done = _finish(rec["id"], EXPIRED, detail, from_states=(PENDING, MERGING))
    if done is not None:
        event_log.append("orchestrator.merge_override_expired",
                         f"override of {rec.get('label')} expired: {detail}",
                         payload={**_public(done)})
        _tell(done, EXPIRED, detail)
    return done


def _public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "telegram"}


# The surfaces whose CALL proves a human pressed something (see the module docstring).
SERVICE_CHANNELS = ("telegram", "dashboard")


def decide(request_id: str, approve: bool, by: str, *, channel: str = "terminal",
           env=None) -> dict:
    """The operator's decision. ``{ok, state, detail, merge_commit_sha?}``.

    Approve ⇒ the merge happens HERE, now, via :func:`chela.contract.complete_override`
    (which re-checks head/CI/mergeable/base live). Exactly one caller ever moves a request
    out of ``pending`` — a second Approve (another button, another process) is told the
    request was already decided and merges nothing.
    """
    why = None if channel in SERVICE_CHANNELS else agent_refusal(env)
    if why:
        return {"ok": False, "state": None, "detail": why, "refused": True}
    by = (by or "").strip() or "unknown"
    with _locked(request_id):
        rec = load(request_id)
        if rec is None:
            return {"ok": False, "state": None, "detail": "no such override request"}
        state = rec.get("state")
        if state != PENDING:
            return {"ok": False, "state": state,
                    "detail": f"that request is already {state} — nothing changed"}
        expired = (rec.get("deadline") or 0) <= time.time()
        if not expired:
            rec.update(state=MERGING if approve else DENIED, decided_by=by,
                       decided_at=time.time())
            if not approve:
                rec.update(detail=f"denied by {by}", finished_at=time.time())
            _write(rec)
    if expired:
        done = _expire(rec, f"not decided within {rec.get('ttl') or 0:.0f}s")
        return {"ok": False, "state": EXPIRED, "detail": (done or rec).get("detail")}

    if not approve:
        event_log.append("orchestrator.merge_override_denied",
                         f"override of {rec.get('label')} DENIED by {by}",
                         payload={**_public(rec), "approved_by": by})
        return {"ok": True, "state": DENIED, "detail": f"denied by {by}"}

    from chela import contract
    try:
        outcome = contract.complete_override(rec, by)
    except Exception as e:                                   # noqa: BLE001 — never wedge
        log.exception("override completion crashed for %s", request_id)
        outcome = {"ok": False, "state": FAILED, "detail": f"internal error: {e}"}
    state = outcome.get("state") or (MERGED if outcome.get("ok") else FAILED)
    detail = outcome.get("detail")
    if state == EXPIRED:
        done = _expire(rec, detail or "the PR head moved")
        return {"ok": False, "state": EXPIRED, "detail": (done or rec).get("detail")}
    done = _finish(request_id, state, detail, from_states=(MERGING,),
                   merge_commit_sha=outcome.get("merge_commit_sha"))
    if state != MERGED:
        event_log.append("orchestrator.merge_override_failed",
                         f"override of {rec.get('label')} approved by {by} but NOT merged: "
                         f"{detail}", payload={**_public(done or rec), "approved_by": by})
        _tell(done or rec, FAILED, detail)
    return {"ok": state == MERGED, "state": state, "detail": detail,
            "merge_commit_sha": outcome.get("merge_commit_sha")}


def sweep(now: float | None = None, head_of=None) -> list[dict]:
    """Expire every pending request whose TTL ran out or whose PR head moved past the
    recorded SHA. ``head_of(task_id) -> sha | None`` defaults to the run row's
    ``pr_head_sha`` (the dispatcher refreshes it from GitHub every tick — no network here).
    Returns the requests it expired."""
    now = time.time() if now is None else now
    if head_of is None:
        def head_of(task_id):
            from chela import dispatcher
            run = dispatcher.resolve_run(task_id) if task_id else None
            return (run or {}).get("pr_head_sha")
    expired = []
    for rec in all_requests():
        if rec.get("state") != PENDING:
            continue
        if (rec.get("deadline") or 0) <= now:
            why = f"not decided within {rec.get('ttl') or 0:.0f}s"
        else:
            try:
                head = head_of(rec.get("task_id"))
            except Exception:                                # noqa: BLE001 — best effort
                head = None
            if not head or head == rec.get("head_sha"):
                continue
            why = (f"the PR head moved ({str(rec.get('head_sha'))[:12]} → {head[:12]}) — "
                   "a new push invalidates the request; request a new override for the new "
                   "head")
        done = _expire(rec, why)
        if done is not None:
            expired.append(done)
    return expired
