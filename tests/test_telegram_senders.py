"""CMX-185: the Telegram bridge accepts updates only from allow-listed SENDER ids.

Every test drives the REAL handlers of a real ``build_application`` with stub updates and
a stub sender — never the live bot, never live tmux.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from chela.telegram import senders
from chela.telegram.inbound import TopicRouter
from chela.telegram.senders import SenderGate, parse_allowed_users

pytest.importorskip("telegram")

OPERATOR, STRANGER = 111, 222


class _Sender:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def __call__(self, window_id, text):
        self.calls.append((window_id, text))
        return True


class _Msg:
    def __init__(self, text=None, photo=None, document=None):
        self.text, self.caption = text, None
        self.photo, self.document = photo, document
        self.message_thread_id = 4
        self.replies: list[str] = []

    async def reply_text(self, text, **_kw):
        self.replies.append(text)


class _Query:
    def __init__(self, data):
        self.data = data
        self.message = _Msg()
        self.answers: list = []

    async def answer(self, text=None, **_kw):
        self.answers.append(text)


class _Update:
    def __init__(self, user_id, *, msg=None, query=None):
        self.message = msg
        self.callback_query = query
        self.effective_chat = type("Chat", (), {"id": 777})()
        self.effective_user = (None if user_id is None
                               else type("User", (), {"id": user_id})())


def _handlers(allowed_users=(OPERATOR,), **kw):
    """``{callback.__name__: gated callback}`` from a real build_application."""
    from chela.telegram.inbound import build_application

    sender = _Sender()
    app = build_application("123:fake-token", TopicRouter("777", "@3", "4", sender=sender),
                            allowed_users=allowed_users, **kw)
    out = {getattr(h.callback, "__wrapped__", h.callback).__name__: h.callback
           for group in app.handlers.values() for h in group}
    return out, sender


def _run(cb, update):
    asyncio.run(cb(update, None))
    return update


@pytest.mark.parametrize("text", ["hello agent", "/clear"])
def test_allowed_sender_is_forwarded_and_a_stranger_in_the_same_chat_is_dropped(text):
    hs, sender = _handlers()
    _run(hs["_on_message"], _Update(OPERATOR, msg=_Msg(text)))
    assert sender.calls == [("@3", text)]

    stranger = _run(hs["_on_message"], _Update(STRANGER, msg=_Msg(text)))
    assert sender.calls == [("@3", text)], "a non-allow-listed sender must never type"
    assert stranger.message.replies == [], "no reply: never confirm the bot is listening"


def test_an_update_with_no_sender_is_dropped():
    hs, sender = _handlers()
    _run(hs["_on_message"], _Update(None, msg=_Msg("hi")))
    assert sender.calls == []


def test_a_strangers_permission_prompt_tap_is_ignored_and_unanswered():
    picks = []

    class _Drafts:
        def pick(self, *a):
            picks.append(a)
            return type("Tap", (), {"ok": True, "toast": "✓", "markup": None})()

    keys = []
    hs, _ = _handlers(drafts=_Drafts(), send_key=lambda w, k: keys.append(k) or True,
                      capture=lambda *_a, **_k: "")
    taps = (("_on_qa", "qa:h:toolu_01:0:1"), ("_on_qa", "qa:nav:esc"),
            ("_on_key", "k:esc"), ("_on_mirror", "m:ent"))
    for cb, data in taps:
        update = _run(hs[cb], _Update(STRANGER, query=_Query(data)))
        assert update.callback_query.answers == [], (cb, data)
    assert picks == [] and keys == []

    # positive control: the operator's same taps answer the prompt and press the keys
    for cb, data in taps:
        update = _run(hs[cb], _Update(OPERATOR, query=_Query(data)))
        assert update.callback_query.answers, (cb, data)
    assert picks == [("toolu_01", 0, 1)]
    assert keys == ["Escape", "Escape", "Enter"]


@pytest.mark.parametrize("cb, receiver, kw", [
    ("_on_photo", "receive_photo", {"photo": [object()]}),
    ("_on_document", "receive_document", {"document": object()}),
])
def test_a_strangers_photo_or_document_is_never_downloaded(monkeypatch, cb, receiver, kw):
    from chela.telegram import media

    got = []

    async def _receive(msg, *_a, **_k):
        got.append(msg)

    monkeypatch.setattr(media, receiver, _receive)
    hs, _ = _handlers()
    _run(hs[cb], _Update(STRANGER, msg=_Msg(**kw)))
    assert got == []
    _run(hs[cb], _Update(OPERATOR, msg=_Msg(**kw)))
    assert len(got) == 1


def test_a_strangers_command_reaches_no_handler():
    launched, escaped = [], []
    hs, _ = _handlers(launch_session=lambda cwd: launched.append(cwd) or ("@9", None),
                      send_escape=lambda w: escaped.append(w) or True)
    for cb, text in (("_on_new", "/new /tmp"), ("_on_esc", "/esc"),
                     ("_on_screenshot", "/screenshot")):
        update = _run(hs[cb], _Update(STRANGER, msg=_Msg(text)))
        assert update.message.replies == [], cb
    assert launched == [] and escaped == []


# -- where the allowlist comes from: explicit > env > empty (chat-only) ---------------

@pytest.mark.parametrize("env, explicit, operator_ok, stranger_ok", [
    ("222", (111,), True, False),     # the explicit argument wins over the env
    ("111", None, True, False),       # None reads CHELA_TELEGRAM_ALLOWED_USERS
    (None, None, True, True),         # unset: legacy chat-only mode, everyone in the chat
])
def test_allowlist_precedence(monkeypatch, env, explicit, operator_ok, stranger_ok):
    if env is None:
        monkeypatch.delenv(senders.ALLOWED_USERS_VAR, raising=False)
    else:
        monkeypatch.setenv(senders.ALLOWED_USERS_VAR, env)
    hs, sender = _handlers(allowed_users=explicit)
    _run(hs["_on_message"], _Update(OPERATOR, msg=_Msg("a")))
    _run(hs["_on_message"], _Update(STRANGER, msg=_Msg("b")))
    assert sender.calls == ([("@3", "a")] if operator_ok else []) + (
        [("@3", "b")] if stranger_ok else [])


def test_unset_allowlist_warns_loudly_at_boot(caplog):
    with caplog.at_level(logging.WARNING, logger="chela.telegram.senders"):
        _handlers(allowed_users=())
    assert "CHELA_TELEGRAM_ALLOWED_USERS is NOT set" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="chela.telegram.senders"):
        _handlers(allowed_users=(OPERATOR,))
    assert caplog.text == ""


# -- the parser: positive AND negative fixtures ---------------------------------------

@pytest.mark.parametrize("raw, allowed, bad", [
    ("111", {"111"}, []),
    (" 111 , 222 333 ", {"111", "222", "333"}, []),
    ("111\n222", {"111", "222"}, []),
    ("0111", {"111"}, []),
    ("", set(), []),
    (None, set(), []),
    ("@liav", set(), ["@liav"]),
    ("-5,12a", set(), ["-5", "12a"]),
    ("١٢٣", set(), ["١٢٣"]),            # non-ASCII digits are not an id
    ("111,x", {"111"}, ["x"]),
])
def test_parse_allowed_users(raw, allowed, bad):
    assert parse_allowed_users(raw) == (frozenset(allowed), bad)


# -- the rejection log: id + hash, never the text, rate-limited ------------------------

def test_rejection_logs_id_and_hash_not_text_and_is_rate_limited(caplog):
    now = [1000.0]
    gate = SenderGate(frozenset({"111"}), clock=lambda: now[0], log_every=60.0)
    secret = "rm -rf my secret plan"
    with caplog.at_level(logging.WARNING, logger="chela.telegram.senders"):
        assert gate.allows(_Update(STRANGER, msg=_Msg(secret))) is False
        assert gate.allows(_Update(STRANGER, msg=_Msg(secret))) is False
        now[0] += 59.0
        assert gate.allows(_Update(STRANGER, msg=_Msg(secret))) is False
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1
    assert "222" in lines[0] and senders.text_hash(secret) in lines[0]
    assert secret not in caplog.text

    caplog.clear()
    now[0] += 1.0                       # the window has passed: log again, with the tally
    with caplog.at_level(logging.WARNING, logger="chela.telegram.senders"):
        gate.allows(_Update(STRANGER, query=_Query("qa:h:t:0:0")))
    assert len(caplog.records) == 1
    assert "2 more suppressed" in caplog.text
    assert senders.text_hash("qa:h:t:0:0") in caplog.text


def test_text_hash_is_a_stable_sha256_prefix():
    assert senders.text_hash("abc") == "ba7816bf8f01"


# -- chela doctor: telegram.sender_allowlist -------------------------------------------

@pytest.fixture
def doctor_env(tmp_path, monkeypatch):
    from chela import config
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv("CHELA_ENV_FILE", str(tmp_path / "chela.env"))
    for var in ("TELEGRAM_BOT_TOKEN", senders.ALLOWED_USERS_VAR):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _doctor():
    from chela import runtime_truth
    return [(f.level, f.title)
            for f in runtime_truth.audit(runtime_truth.fact("telegram.sender_allowlist"))]


@pytest.mark.parametrize("environ, chela_env, secrets_env, expect", [
    # the environment wins, then chela.env, then secrets.env (scripts/chela-env.sh)
    ({senders.ALLOWED_USERS_VAR: "111"}, "CHELA_TELEGRAM_ALLOWED_USERS=\n", "", "OK"),
    ({senders.ALLOWED_USERS_VAR: ""}, "CHELA_TELEGRAM_ALLOWED_USERS=111\n", "", "WARN"),
    ({}, "CHELA_TELEGRAM_ALLOWED_USERS=111\n", "CHELA_TELEGRAM_ALLOWED_USERS=\n", "OK"),
    ({}, "", "CHELA_TELEGRAM_ALLOWED_USERS=111\n", "OK"),
    ({}, "", "", "WARN"),
])
def test_doctor_reads_the_allowlist_the_way_the_bridge_starts(
        doctor_env, monkeypatch, environ, chela_env, secrets_env, expect):
    from chela.runtime_truth import OK, WARN
    (doctor_env / "chela.env").write_text(chela_env)
    (doctor_env / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=1:x\n" + secrets_env)
    for k, v in environ.items():
        monkeypatch.setenv(k, v)
    levels = [level for level, _ in _doctor()]
    assert levels == [{"OK": OK, "WARN": WARN}[expect]]


def test_doctor_is_silent_with_no_bridge_and_warns_on_empty(doctor_env, monkeypatch):
    from chela.runtime_truth import OK, WARN
    assert _doctor() == [(OK, "Telegram bridge: not configured (optional)")]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    [(level, title)] = _doctor()
    assert level == WARN and "allowlist is EMPTY" in title
    monkeypatch.setenv(senders.ALLOWED_USERS_VAR, "111,222")
    assert _doctor() == [(OK, "Telegram accepts updates from 2 allow-listed user(s) only")]


def test_doctor_names_non_numeric_entries(doctor_env, monkeypatch):
    from chela.runtime_truth import OK, WARN
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv(senders.ALLOWED_USERS_VAR, "@liav")
    found = _doctor()
    assert found[0] == (WARN, "CHELA_TELEGRAM_ALLOWED_USERS ignores 1 non-numeric entry: @liav")
    assert found[1][0] == WARN and "EMPTY" in found[1][1]
    monkeypatch.setenv(senders.ALLOWED_USERS_VAR, "111 @a x")
    assert _doctor() == [
        (WARN, "CHELA_TELEGRAM_ALLOWED_USERS ignores 2 non-numeric entries: @a, x"),
        (OK, "Telegram accepts updates from 1 allow-listed user(s) only"),
    ]


# -- the WHOLE registry: every handler, present and future, is behind the gate ---------

class _Tripwire:
    """A stub whose every attribute read is recorded. Only ``exempt`` (what the gate
    itself reads to decide and to hash) is answered without a record."""

    def __init__(self, touched, name, exempt=(), **fields):
        object.__setattr__(self, "_touched", touched)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_exempt", dict(exempt, **fields))

    def __getattr__(self, attr):
        if attr in self._exempt:
            return self._exempt[attr]
        self._touched.append(f"{self._name}.{attr}")
        raise AttributeError(attr)          # whatever a handler does next, it got nothing


# Each button handler only acts on its own prefix, so each gets data it would act on.
_TAP_DATA = {"_on_qa": "qa:nav:esc", "_on_mirror": "m:ent", "_on_new_cb": "n:up",
             "_on_override": "ov:a:override-x", "_on_key": "k:esc"}


def _tripwire_update(user_id, touched, name):
    user = type("User", (), {"id": user_id})()
    data = _TAP_DATA.get(name, "k:esc")
    return _Tripwire(touched, "update", effective_user=user,
                     callback_query=_Tripwire(touched, "query", data=data),
                     message=_Tripwire(touched, "message", text="hi", caption=None))


def test_every_registered_handler_reads_nothing_of_a_strangers_update():
    """Walks ``app.handlers`` itself, so a handler added later without ``_gated`` goes red
    here even if nobody writes a test for it. Behavioural, not ``hasattr(__wrapped__)``: a
    wrapper that forgot to check the gate still has ``__wrapped__``."""
    closed = []
    hs, sender = _handlers(on_topic_closed=closed.append,
                           decide_override=lambda *a, **k: pytest.fail("decided"),
                           launch_session=lambda cwd: pytest.fail("launched"),
                           send_key=lambda *a: pytest.fail("key"),
                           send_escape=lambda *a: pytest.fail("esc"),
                           capture=lambda *a, **k: pytest.fail("captured"))
    assert {"_on_message", "_on_photo", "_on_document", "_on_new", "_on_screenshot",
            "_on_esc", "_on_qa", "_on_mirror", "_on_new_cb", "_on_override", "_on_key",
            "_on_topic_closed"} <= set(hs)
    for name, cb in hs.items():
        touched: list[str] = []
        _run(cb, _tripwire_update(STRANGER, touched, name))
        assert touched == [], f"{name} read a stranger's update: {touched}"
    assert sender.calls == [] and closed == []

    # positive control: the operator's identical update DOES reach every handler, so the
    # tripwire above is able to see a handler that runs
    for name, cb in hs.items():
        touched = []
        try:
            _run(cb, _tripwire_update(OPERATOR, touched, name))
        except Exception:                   # noqa: BLE001 — the stub is hostile on purpose
            pass
        assert touched, f"{name}: the tripwire saw nothing even for the operator"


def test_a_strangers_override_approve_tap_decides_nothing_and_is_unanswered():
    decided = []

    def _decide(request_id, approve, *a, **k):
        decided.append((request_id, approve))
        return {"state": "denied", "ok": True}

    hs, _ = _handlers(decide_override=_decide)
    data = "ov:a:override-abc123"
    stranger = _run(hs["_on_override"], _Update(STRANGER, query=_Query(data)))
    assert decided == [] and stranger.callback_query.answers == []
    operator = _run(hs["_on_override"], _Update(OPERATOR, query=_Query(data)))
    assert decided == [("override-abc123", True)]
    assert operator.callback_query.answers


def test_explicit_empty_allowlist_is_chat_only_even_with_the_env_set(monkeypatch):
    """``()`` is an explicit choice, not "unset": only ``None`` falls back to the env."""
    monkeypatch.setenv(senders.ALLOWED_USERS_VAR, str(OPERATOR))
    hs, sender = _handlers(allowed_users=())
    _run(hs["_on_message"], _Update(STRANGER, msg=_Msg("b")))
    assert sender.calls == [("@3", "b")]


def test_the_bridge_reads_an_allowlist_that_lives_only_in_a_file(doctor_env):
    """Started WITHOUT chela-env.sh (nothing in os.environ), the bridge still loads the
    allowlist doctor reports — from chela.env, or secrets.env when chela.env lacks it."""
    for chela_env, secrets_env in ((f"{senders.ALLOWED_USERS_VAR}={OPERATOR}\n", ""),
                                   ("", f"{senders.ALLOWED_USERS_VAR}={OPERATOR}\n")):
        (doctor_env / "chela.env").write_text(chela_env)
        (doctor_env / "secrets.env").write_text(secrets_env)
        assert senders.allowed_users_from_env() == frozenset({str(OPERATOR)})
        hs, sender = _handlers(allowed_users=None)
        _run(hs["_on_message"], _Update(STRANGER, msg=_Msg("b")))
        _run(hs["_on_message"], _Update(OPERATOR, msg=_Msg("a")))
        assert sender.calls == [("@3", "a")]


def test_the_rejection_rate_limit_is_per_sender(caplog):
    """A second stranger inside the first one's window is still logged, and the
    suppressed tally belongs to the sender it was counted for."""
    now = [1000.0]
    gate = SenderGate(frozenset({"111"}), clock=lambda: now[0], log_every=60.0)
    with caplog.at_level(logging.WARNING, logger="chela.telegram.senders"):
        gate.allows(_Update(222, msg=_Msg("x")))
        gate.allows(_Update(222, msg=_Msg("x")))
        gate.allows(_Update(333, msg=_Msg("y")))
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert "user 333" in lines[1] and "0 more suppressed" in lines[1]
    caplog.clear()
    now[0] += 60.0
    with caplog.at_level(logging.WARNING, logger="chela.telegram.senders"):
        gate.allows(_Update(333, msg=_Msg("y")))
        gate.allows(_Update(222, msg=_Msg("x")))
    lines = [r.getMessage() for r in caplog.records]
    assert "user 333" in lines[0] and "0 more suppressed" in lines[0]
    assert "user 222" in lines[1] and "1 more suppressed" in lines[1]


def test_a_photo_caption_is_hashed_never_logged(caplog):
    gate = SenderGate(frozenset({"111"}))
    msg = _Msg(photo=[object()])
    msg.caption = "my private caption"
    with caplog.at_level(logging.WARNING, logger="chela.telegram.senders"):
        assert gate.allows(_Update(STRANGER, msg=msg)) is False
    assert senders.text_hash("my private caption") in caplog.text
    assert "my private caption" not in caplog.text


def test_doctor_treats_a_whitespace_token_as_no_bridge(doctor_env, monkeypatch):
    from chela.runtime_truth import OK
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    assert _doctor() == [(OK, "Telegram bridge: not configured (optional)")]
