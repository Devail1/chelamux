"""⚖️🔓 Override requests on Telegram — ONE message, one-tap Approve / Deny (CMX-79).

``chela merge --override`` (or ``chela override request``) records a pending request
(:mod:`chela.overrides`) and returns. This relay posts each new request ONCE to the
orchestrator's topic — task, absolute PR link, the judge's state on the head, the reason —
with ✅ Approve / ❌ Deny buttons (:func:`chela.telegram.interactive.override_reply_markup`),
and then EDITS that same message to the outcome: merged ``<sha>`` / denied / expired /
failed (``<reason>``), dropping the buttons. A tap resolves the request through
:func:`chela.overrides.decide` — the same path as ``chela merge-approve`` and the dashboard —
so an approval completes the merge right there.

Trust model: the same as every other Telegram button — the tap must come from the bound
chat (CMX-8; a ``from.id`` check was declined 2026-10-09). A callback can only be produced by
a person pressing a button in that chat — no agent tool call can mint one — and a bot
account's tap is refused as well.

Everything here but :meth:`OverrideRelay.tick`'s I/O seams is pure, so it is tested without
``python-telegram-bot``.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from chela import overrides
from chela.telegram.interactive import decode_override_callback, override_reply_markup

log = logging.getLogger("chela.telegram.overridewatch")

_MAX_REASON = 600


def _outcome_line(rec: dict) -> str | None:
    state = rec.get("state")
    by = rec.get("decided_by") or "?"
    detail = rec.get("detail") or ""
    if state == overrides.MERGING:
        return f"⏳ Approved by {by} — merging…"
    if state == overrides.MERGED:
        return f"✅ Approved by {by} — merged {(rec.get('merge_commit_sha') or '?')[:12]}"
    if state == overrides.DENIED:
        return f"❌ Denied by {by} — nothing was merged"
    if state == overrides.EXPIRED:
        return f"⌛ Expired — {detail or 'no decision'}. Nothing was merged."
    if state == overrides.FAILED:
        return f"⚠️ Approved by {by} but NOT merged — failed ({detail or 'unknown'})"
    return None


def _judge_line(rec: dict) -> str:
    state = rec.get("judge_state") or "never ran"
    judged, head = rec.get("judge_sha"), rec.get("head_sha")
    if judged and head and judged != head:
        return f"Judge: {state} — on {judged[:12]}, not this head (never judged)"
    return f"Judge: {state} on this head"


def format_override_message(rec: dict) -> str:
    """The request card. Plain text (no parse mode): every field here is agent- or
    GitHub-authored, and plain text cannot be mis-rendered or injected into markup."""
    reason = str(rec.get("reason") or "")
    if len(reason) > _MAX_REASON:
        reason = reason[:_MAX_REASON - 1] + "…"
    head = str(rec.get("head_sha") or "?")[:12]
    title = rec.get("title") or rec.get("label") or rec.get("task_id")
    lines = [
        "⚖️ Override requested — merge past the judge?",
        f"Task: {rec.get('task_id')} — {title}" if title != rec.get("task_id")
        else f"Task: {rec.get('task_id')}",
        f"PR: {rec.get('pr_url')}",
        f"Into: {rec.get('base')}  ·  head {head}",
        _judge_line(rec),
        f"Reason: {reason}",
        f"Asked by: {rec.get('actor') or 'human'}",
    ]
    outcome = _outcome_line(rec)
    if outcome:
        lines += ["", outcome]
    else:
        lines += ["", "Approve merges now — after re-checking that the head is unchanged, "
                      "CI is green and the PR is mergeable. A new push expires this request. "
                      f"(Dashboard: /override/{rec.get('id')})"]
    return "\n".join(lines)


class OverrideRelay:
    """Sync the override store to Telegram: post each pending request once, edit it to its
    outcome. ``post(text, message_thread_id=…, reply_markup=…) -> message_id | None`` and
    ``edit(message_id, text, reply_markup=…) -> bool`` are :class:`~chela.telegram.relay.
    BotSender`'s; ``thread()`` names the orchestrator's topic (None ⇒ the General topic)."""

    def __init__(self, post: Callable, edit: Callable,
                 thread: Callable[[], "str | int | None"] = lambda: None):
        self._post = post
        self._edit = edit
        self._thread = thread
        # The pane-loop thread and a button tap's refresh both call tick(); without this a
        # request could be posted twice by the two of them racing.
        self._lock = threading.Lock()

    def tick(self) -> None:
        with self._lock:
            try:
                overrides.sweep()
            except Exception:                                # noqa: BLE001 — best effort
                log.exception("override sweep failed")
            for rec in overrides.all_requests():
                try:
                    self._sync(rec)
                except Exception:                            # noqa: BLE001 — next tick
                    log.exception("override relay failed for %s", rec.get("id"))

    def _sync(self, rec: dict) -> None:
        tg = rec.get("telegram") or {}
        state = rec.get("state")
        mid = tg.get("message_id")
        if mid is None:
            # Only a request still waiting is worth a NEW message — one decided before the
            # bridge ever saw it was decided by someone already looking at it.
            if state != overrides.PENDING:
                return
            try:
                thread = self._thread()
            except Exception:                                # noqa: BLE001
                thread = None
            mid = self._post(format_override_message(rec), message_thread_id=thread,
                             reply_markup=override_reply_markup(rec["id"]))
            if mid is not None:
                overrides.set_fields(rec["id"], telegram={"message_id": mid,
                                                          "rendered": state})
            return
        if tg.get("rendered") == state:
            return
        markup = override_reply_markup(rec["id"]) if state == overrides.PENDING else None
        if self._edit(mid, format_override_message(rec), reply_markup=markup):
            overrides.set_fields(rec["id"], telegram={**tg, "rendered": state})


def handle_override_tap(data: str, *, is_bot: bool = False,
                        decide: Callable = overrides.decide) -> str:
    """Resolve one ``ov:`` tap (already known to come from the bound chat). Returns the
    toast to show. The decision goes through ``decide`` — :func:`chela.overrides.decide`,
    the same path as ``chela merge-approve`` and the dashboard."""
    decoded = decode_override_callback(data or "")
    if decoded is None:
        return "Unknown button"
    if is_bot:
        return "⛔ A bot cannot approve an override"
    approve, request_id = decoded
    out = decide(request_id, approve, "telegram", channel="telegram")
    state = out.get("state")
    if state == overrides.MERGED:
        return f"✅ Merged {(out.get('merge_commit_sha') or '')[:12]}".strip()
    if state == overrides.DENIED and out.get("ok"):
        return "❌ Denied — nothing merged"
    return f"Not merged: {out.get('detail') or state}"[:190]
