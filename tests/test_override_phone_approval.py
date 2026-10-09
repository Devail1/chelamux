"""⚖️🔓📱 CMX-61 — an override can be approved from a phone.

Two `chela merge --override` requests expired unapproved because nothing reachable from a
phone could approve them: no Telegram prompt, and a push whose dashboard link was a RELATIVE
path. These pin the fix: one Telegram card with Approve / Deny per request, a tap that
counts only from the configured operator id, the card edited to its outcome (incl.
expired), an absolute dashboard URL in the push, and a configurable (longer) window.

GitHub is stubbed as in ``test_merge_override``; the approval rendezvous is REAL (files
under the sandboxed ``CHELA_DIR``), driven from a second thread through the Telegram relay.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, contract, dispatcher, gateanswer
from chela.dispatcher import CI_PASSING, CIStatus
from chela.judge import J_BLOCKED
from chela.telegram import approvals as tg_approvals
from chela.telegram.approvals import ApprovalRelay, decode_callback, encode_callback

HEAD = "a" * 40
OPERATOR = 4242
STRANGER = 9999


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.delenv(config.ACTOR_ENV, raising=False)
    monkeypatch.delenv("CHELA_DASHBOARD_PUBLIC_URL", raising=False)
    monkeypatch.delenv("CHELA_OVERRIDE_WAIT_S", raising=False)
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text("# wf\n")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, started_at, "
            "attempt, pr_url, pr_state, judge_state, judge_sha, branch_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", str(tmp_path / "WORKFLOW.md"), "t", "changes_requested", "@9",
             dispatcher._now(), 1, "https://github.com/o/r/pull/1", "open", J_BLOCKED,
             "b" * 40, "cmx-1"),
        )
        conn.commit()
    return tmp_path


def _merge(**kw):
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(CI_PASSING, head_sha=HEAD)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "m" * 40}) as squash:
        result = contract.merge("t1", **kw)
    return result, squash


class _Bot:
    """BotSender's post/edit, recorded."""

    def __init__(self):
        self.posts: list[dict] = []
        self.edits: list[dict] = []

    def post(self, text, message_thread_id=None, reply_markup=None, **_kw):
        self.posts.append({"text": text, "thread": message_thread_id, "markup": reply_markup})
        return 100 + len(self.posts)

    def edit(self, message_id, text, **kw):
        self.edits.append({"message_id": message_id, "text": text, **kw})
        return True


def _relay(bot, operators=frozenset({OPERATOR}), **kw):
    return ApprovalRelay(bot.post, bot.edit, operators=operators, **kw)


def _drive(relay, bot, *, tap_user: int | None, approve: bool = True, timeout: float = 5.0):
    """A second thread playing the bridge: polls the relay until the card is posted, then
    (optionally) taps it as ``tap_user``. Returns the thread and what the tap returned."""
    seen: dict = {}

    def run():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            relay.poll()
            if bot.posts:
                if tap_user is not None:
                    rid = gateanswer.pending_approvals()[0]["id"]
                    seen["tap"] = relay.on_tap(encode_callback(rid, approve), tap_user, "op")
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, seen


# --- Telegram: one card, two buttons -------------------------------------------------

def test_an_override_request_posts_one_telegram_card_with_two_callback_buttons():
    """🔴 GUARD: a pending request ⇒ exactly ONE message (re-polling never re-posts) with
    an Approve and a Deny callback button for THAT request."""
    bot = _Bot()
    relay = _relay(bot, thread=lambda: "77")
    assert gateanswer.open_approval("override-" + "c" * 24, "Override-merge cmx-1?", 60,
                                    {"label": "cmx-1", "pr_url": "https://x/pull/1"})
    relay.poll()
    relay.poll()
    assert len(bot.posts) == 1
    post = bot.posts[0]
    assert post["thread"] == "77"                         # the orchestrator's topic
    assert "Override-merge cmx-1?" in post["text"]
    buttons = post["markup"]["inline_keyboard"][0]
    assert len(buttons) == 2
    decoded = [decode_callback(b["callback_data"]) for b in buttons]
    assert decoded == [("override-" + "c" * 24, True), ("override-" + "c" * 24, False)]


def test_an_approve_tap_from_the_operator_approves_and_the_merge_proceeds(repo):
    """🔴 GUARD: the operator taps Approve on the card ⇒ the waiting override is approved
    (audited as a telegram approver) and the merge goes through."""
    bot = _Bot()
    relay = _relay(bot)
    bridge, seen = _drive(relay, bot, tap_user=OPERATOR)
    result, squash = _merge(override=True, reason="judge flaked", approval_wait=5)
    bridge.join()
    assert seen["tap"][0] == "✅ Approved"
    assert "Approved by telegram:op" in seen["tap"][1]
    assert result["ok"] is True, result
    squash.assert_called_once()
    assert result["override"]["approved_by"] == "telegram:op"


def test_a_tap_from_anyone_but_the_operator_is_ignored(repo):
    """🔴 GUARD: a chat member who is NOT the operator taps Approve ⇒ nothing is written,
    the request stays unapproved, and the override times out with nothing merged. Drop the
    operator-id check and this approves → RED."""
    bot = _Bot()
    relay = _relay(bot)
    bridge, seen = _drive(relay, bot, tap_user=STRANGER)
    result, squash = _merge(override=True, reason="judge flaked", approval_wait=0.6)
    bridge.join()
    toast, new_text = seen["tap"]
    assert "Only the operator" in toast and new_text is None
    assert result["ok"] is False and "not approved" in result["error"]
    squash.assert_not_called()


def test_a_deny_tap_from_the_operator_denies(repo):
    bot = _Bot()
    relay = _relay(bot)
    bridge, seen = _drive(relay, bot, tap_user=OPERATOR, approve=False)
    result, squash = _merge(override=True, reason="judge flaked", approval_wait=5)
    bridge.join()
    assert "Denied by telegram:op" in seen["tap"][1]
    assert result["ok"] is False and "DENIED by telegram:op" in result["error"]
    squash.assert_not_called()


def test_expiry_edits_the_card_to_expired():
    """🔴 GUARD: nobody decides ⇒ once the request is gone past its deadline, the card is
    edited (keyboard dropped) to say it EXPIRED."""
    bot = _Bot()
    clock = {"t": 1000.0}
    relay = _relay(bot, clock=lambda: clock["t"])
    rid = "override-" + "d" * 24
    assert gateanswer.open_approval(rid, "Override-merge cmx-2?", 30, {"label": "cmx-2"})
    relay.poll()
    assert len(bot.posts) == 1 and bot.edits == []
    gateanswer.close_gate(rid)                             # the waiter timed out
    clock["t"] += 31
    relay.poll()
    assert len(bot.edits) == 1
    edit = bot.edits[0]
    assert edit["message_id"] == 101
    assert "Expired" in edit["text"] and "reply_markup" not in edit


def test_a_card_decided_by_tap_is_not_re_edited_by_the_poll():
    bot = _Bot()
    relay = _relay(bot)
    rid = "override-" + "e" * 24
    assert gateanswer.open_approval(rid, "Override-merge cmx-3?", 30, {"label": "cmx-3"})
    relay.poll()
    relay.on_tap(encode_callback(rid, True), OPERATOR, "op")
    gateanswer.close_gate(rid)
    relay.poll()
    assert bot.edits == []                                 # the tap's own edit stands


def test_without_a_configured_operator_the_card_has_no_buttons_and_nobody_can_approve():
    """Fail closed: no TELEGRAM_OPERATOR_ID ⇒ the card still arrives (it is the
    notification) but with no buttons, and a tap from anyone is refused."""
    bot = _Bot()
    relay = _relay(bot, operators=frozenset())
    rid = "override-" + "f" * 24
    assert gateanswer.open_approval(rid, "Override-merge cmx-4?", 30, {"label": "cmx-4"})
    relay.poll()
    assert bot.posts[0]["markup"] is None
    assert "TELEGRAM_OPERATOR_ID" in bot.posts[0]["text"]
    toast, _ = relay.on_tap(encode_callback(rid, True), OPERATOR, "op")
    assert "Only the operator" in toast
    assert gateanswer.approval(rid) is not None            # still pending, untouched


def test_operator_ids_parse_from_the_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_OPERATOR_ID", "4242, 17 nope")
    assert tg_approvals.operator_ids() == frozenset({4242, 17})
    monkeypatch.delenv("TELEGRAM_OPERATOR_ID")
    assert tg_approvals.operator_ids() == frozenset()


# --- the PTB handler: the pressing user's id reaches the check -----------------------

class _Query:
    def __init__(self, data):
        self.data = data
        self.answers: list = []
        self.edited: list[str] = []

    async def answer(self, text=None, **_kw):
        self.answers.append(text)

    async def edit_message_text(self, text, **_kw):
        self.edited.append(text)


class _Update:
    def __init__(self, data, user_id, chat_id=777):
        self.message = None
        self.callback_query = _Query(data)
        self.effective_chat = type("Chat", (), {"id": chat_id})()
        self.effective_user = type("User", (), {"id": user_id, "username": "op"})()


def _override_handler(relay):
    from telegram.ext import CallbackQueryHandler

    from chela.telegram.inbound import TopicRouter, build_application

    app = build_application(
        "123:fake-token", TopicRouter("777", "@3", "4", sender=lambda *_a: True),
        approvals=relay,
    )
    cbs = [h for group in app.handlers.values() for h in group
           if isinstance(h, CallbackQueryHandler)]
    match = [h for h in cbs if h.pattern is not None and h.pattern.match("ov:a:x")]
    assert len(match) == 1, "no ov: callback handler registered"
    return match[0].callback


@pytest.mark.parametrize("user_id, approved", [(OPERATOR, True), (STRANGER, False)])
def test_the_bot_handler_passes_the_pressing_users_id(user_id, approved):
    pytest.importorskip("telegram")
    bot = _Bot()
    relay = _relay(bot)
    rid = "override-" + "1" * 24
    assert gateanswer.open_approval(rid, "Override-merge cmx-5?", 30, {"label": "cmx-5"})
    update = _Update(encode_callback(rid, True), user_id)
    asyncio.run(_override_handler(relay)(update, None))
    still_pending = gateanswer.approval(rid) is not None
    answered = gateanswer._read_json(gateanswer._answer_path(rid))
    assert (answered is not None) is approved and still_pending
    assert bool(update.callback_query.edited) is approved


def test_the_bot_handler_ignores_a_tap_from_another_chat():
    pytest.importorskip("telegram")
    bot = _Bot()
    relay = _relay(bot)
    rid = "override-" + "2" * 24
    assert gateanswer.open_approval(rid, "Override-merge cmx-6?", 30, {"label": "cmx-6"})
    update = _Update(encode_callback(rid, True), OPERATOR, chat_id=1)
    asyncio.run(_override_handler(relay)(update, None))
    assert gateanswer._read_json(gateanswer._answer_path(rid)) is None


# --- the push: an ABSOLUTE link, or an honest "no link" ------------------------------

def _pushed(repo, monkeypatch) -> str:
    sent: list[str] = []
    monkeypatch.setattr(contract.notify, "enabled", lambda: True)
    monkeypatch.setattr(contract.notify, "send", lambda msg, title=None: sent.append(msg))
    result, _ = _merge(override=True, reason="judge flaked", approval_wait=0.2)
    assert result["ok"] is False
    assert len(sent) == 1
    return sent[0]


def test_the_push_carries_an_absolute_dashboard_url_when_one_is_configured(repo, monkeypatch):
    """🔴 GUARD: with CHELA_DASHBOARD_PUBLIC_URL set the push contains a clickable
    ``https?://host/override/<id>``. Print the relative path again and this goes RED."""
    monkeypatch.setenv("CHELA_DASHBOARD_PUBLIC_URL", "https://dash.example.ts.net:8443/")
    body = _pushed(repo, monkeypatch)
    assert re.search(r"https://dash\.example\.ts\.net:8443/override/override-[0-9a-f]{24}\b",
                     body), body


def test_without_a_public_url_the_push_says_so_instead_of_a_dead_path(repo, monkeypatch):
    body = _pushed(repo, monkeypatch)
    assert "/override/" not in body
    assert "CHELA_DASHBOARD_PUBLIC_URL is not set" in body
    assert "chela merge-approve override-" in body


@pytest.mark.parametrize("raw", ["dash.example:5005", "ftp://x", "/override", "  "])
def test_a_non_absolute_public_url_is_treated_as_unset(monkeypatch, raw):
    monkeypatch.setenv("CHELA_DASHBOARD_PUBLIC_URL", raw)
    assert config.dashboard_link("/override/x") is None


# --- the window: a Dispatch knob, 15 min by default ----------------------------------

def test_the_override_window_is_a_dispatch_knob_defaulting_to_15_minutes(monkeypatch):
    from chela import userconfig
    assert contract.override_wait_budget() == 900.0
    assert config.set_dispatch("override_wait_seconds", "1200") is None
    assert contract.override_wait_budget() == 1200.0
    monkeypatch.setenv("CHELA_OVERRIDE_WAIT_S", "60")
    assert contract.override_wait_budget() == 60.0         # env still wins
    assert config.set_dispatch("override_wait_seconds", "0") is not None   # floor 1
    userconfig.set_("override_wait_seconds", None)


def test_the_api_lists_a_pending_override_until_it_expires():
    pytest.importorskip("flask")
    from chela.dashboard import app as dash
    rid = "override-" + "3" * 24
    assert gateanswer.open_approval(rid, "Override-merge cmx-7?", 30, {"label": "cmx-7"})
    client = dash.app.test_client()
    pending = client.get("/api/overrides").get_json()["pending"]
    assert [p["id"] for p in pending] == [rid]
    gateanswer.close_gate(rid)
    assert client.get("/api/overrides").get_json()["pending"] == []
