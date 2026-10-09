"""⚖️🔓 Override approvals on Telegram — Approve / Deny from the phone (CMX-61).

``chela merge --override`` opens an approval request on disk (:func:`chela.gateanswer.
open_approval`) and blocks until the operator decides or the window runs out. Before this
module the only ways to decide were the dashboard's ``/override/<id>`` page and ``chela
merge-approve`` in a plain terminal — and the push notification carried a RELATIVE path, so
on a phone neither was reachable and two requests expired unapproved.

:class:`ApprovalRelay` closes that from the ``chela telegram`` process, which already owns
the bot: each poll it posts every NEW pending request once — to the orchestrator's topic,
else the chat's General topic — with inline **Approve / Deny** buttons, and edits that
message when the request leaves the pending set (approved / denied here, decided elsewhere,
or expired). A tap is delivered through :func:`chela.gateanswer.answer_approval`, the exact
path ``chela merge-approve`` and the dashboard page use, so the waiting merge picks it up on
its next poll.

⛔ Human-only, and the operator only. A tap counts ONLY when the pressing Telegram user's id
is one of ``TELEGRAM_OPERATOR_ID`` (comma-separated); anyone else in the chat is refused and
nothing is written. With no operator configured the card still arrives (it is the
notification) but carries no buttons and says how to enable them — fail closed, never "any
member of the chat may approve". A Claude session cannot press a Telegram button at all,
and is still denied ``chela merge-approve`` and the dashboard route by :mod:`chela.mergegate`.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from chela import config, gateanswer

log = logging.getLogger("chela.telegram.approvals")

OVERRIDE_CB_PREFIX = "ov:"
_APPROVE, _DENY = "a", "d"

OPERATOR_ENV = "TELEGRAM_OPERATOR_ID"


def operator_ids(raw: str | None = None) -> frozenset[int]:
    """The Telegram user ids allowed to decide an override — ``TELEGRAM_OPERATOR_ID``,
    comma/space separated. Anything that is not an integer is ignored."""
    raw = os.environ.get(OPERATOR_ENV, "") if raw is None else raw
    out: set[int] = set()
    for part in raw.replace(",", " ").split():
        try:
            out.add(int(part))
        except ValueError:
            log.warning("%s: ignoring non-numeric id %r", OPERATOR_ENV, part)
    return frozenset(out)


def encode_callback(request_id: str, approve: bool) -> str:
    return f"{OVERRIDE_CB_PREFIX}{_APPROVE if approve else _DENY}:{request_id}"


def decode_callback(data: str) -> tuple[str, bool] | None:
    """``(request_id, approve)`` for an ``ov:`` tap, else None."""
    if not (data or "").startswith(OVERRIDE_CB_PREFIX):
        return None
    verb, _, request_id = data[len(OVERRIDE_CB_PREFIX):].partition(":")
    if verb not in (_APPROVE, _DENY) or not request_id:
        return None
    return request_id, verb == _APPROVE


def keyboard(request_id: str) -> dict:
    """The two-button inline keyboard (Bot API ``reply_markup`` shape)."""
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": encode_callback(request_id, True)},
        {"text": "⛔ Deny", "callback_data": encode_callback(request_id, False)},
    ]]}


def _human(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 120:
        return f"{seconds}s"
    return f"{seconds // 60} min"


def card_text(req: dict, *, operators: bool) -> str:
    """The override card's body (plain text — no parse mode, nothing to escape)."""
    meta = req.get("meta") or {}
    lines = ["⚖️ Override approval needed", "", str(req.get("question") or "")]
    if meta.get("pr_url"):
        lines.append(f"PR: {meta['pr_url']}")
    if meta.get("actor"):
        lines.append(f"Asked by: {meta['actor']}")
    lines.append(f"Expires in {_human(req.get('seconds_left') or 0)} — a timeout is a deny.")
    link = config.dashboard_link(f"/override/{req['id']}")
    if link:
        lines.append(f"Dashboard: {link}")
    if not operators:
        lines += ["", f"Telegram approval is OFF: set {OPERATOR_ENV} to your Telegram user "
                      "id in chela-telegram's environment. Until then approve in a plain "
                      f"terminal: chela merge-approve {req['id']}"]
    return "\n".join(lines)


class ApprovalRelay:
    """Posts pending override requests with Approve/Deny buttons and settles them.

    ``post(text, message_thread_id=, reply_markup=) -> message_id | None`` and
    ``edit(message_id, text) -> bool`` are :class:`chela.telegram.relay.BotSender`'s
    methods (an edit without ``reply_markup`` drops the keyboard). ``thread()`` names the
    topic to post into (None = the chat's General topic). :meth:`poll` runs on the
    outbound side; :meth:`on_tap` on PTB's — the lock keeps their bookkeeping consistent.
    """

    def __init__(
        self,
        post: Callable[..., int | None],
        edit: Callable[..., bool],
        *,
        operators: frozenset[int] | None = None,
        thread: Callable[[], str | int | None] = lambda: None,
        pending: Callable[[], list[dict]] = gateanswer.pending_approvals,
        answer: Callable[[str, bool, str], tuple[bool, str]] = gateanswer.answer_approval,
        clock: Callable[[], float] = time.time,
    ):
        self._post = post
        self._edit = edit
        self._operators = operator_ids() if operators is None else frozenset(operators)
        self._thread = thread
        self._pending = pending
        self._answer = answer
        self._clock = clock
        self._lock = threading.Lock()
        # request id -> {"message_id", "deadline", "outcome"}; ``outcome`` is set once
        # this process settled it (a tap), so the poll does not re-edit it.
        self._tracked: dict[str, dict] = {}

    @property
    def operators(self) -> frozenset[int]:
        return self._operators

    def poll(self) -> None:
        """Post each new pending request once; settle each tracked one that left."""
        live = {req["id"]: req for req in self._pending()}
        now = self._clock()
        with self._lock:
            for rid, req in live.items():
                if rid in self._tracked:
                    continue
                try:
                    thread = self._thread()
                except Exception:          # noqa: BLE001 — a bad lookup posts to General
                    log.debug("approvals: topic lookup failed", exc_info=True)
                    thread = None
                markup = keyboard(rid) if self._operators else None
                message_id = self._post(card_text(req, operators=bool(self._operators)),
                                        message_thread_id=thread, reply_markup=markup)
                if message_id is None:
                    continue                # not delivered — the next poll retries
                self._tracked[rid] = {"message_id": message_id,
                                      "deadline": now + float(req.get("seconds_left") or 0),
                                      "outcome": None,
                                      "question": str(req.get("question") or "")}
            for rid in [r for r in self._tracked if r not in live]:
                entry = self._tracked.pop(rid)
                if entry["outcome"] is not None:
                    continue
                if now >= entry["deadline"]:
                    verdict = ("⌛ Expired — nobody approved it in time, so it was DENIED. "
                               "Nothing merged.")
                else:
                    verdict = ("☑️ Closed — decided on the dashboard or in a terminal "
                               "(or the merge stopped waiting).")
                self._edit(entry["message_id"], f"{verdict}\n\n{entry['question']}")

    def on_tap(self, data: str, user_id: int | None, user_name: str = "") -> tuple[str, str | None]:
        """Handle one ``ov:`` callback. Returns ``(toast, new_text)`` — ``new_text`` is
        what the tapped message should now say (its keyboard removed), or None to leave it.

        ⛔ A user whose id is not an operator id changes NOTHING."""
        decoded = decode_callback(data)
        if decoded is None:
            return "", None
        request_id, approve = decoded
        if user_id is None or user_id not in self._operators:
            log.warning("approvals: ignored a %s tap on %s from non-operator %s",
                        "approve" if approve else "deny", request_id, user_id)
            return "⛔ Only the operator can decide this override.", None
        who = f"telegram:{user_name or user_id}"
        ok, why = self._answer(request_id, approve, who)
        with self._lock:
            entry = self._tracked.get(request_id)
            question = entry["question"] if entry else ""
            if ok and entry is not None:
                entry["outcome"] = why
        if not ok:
            text = f"⌛ No longer waiting — {why}. Nothing was changed."
            return f"⌛ {why}", (f"{text}\n\n{question}" if question else text)
        verdict = ("✅ Approved by " if approve else "⛔ Denied by ") + who
        if approve:
            verdict += " — the merge proceeds."
        else:
            verdict += " — nothing will merge."
        return ("✅ Approved" if approve else "⛔ Denied"), (
            f"{verdict}\n\n{question}" if question else verdict)
