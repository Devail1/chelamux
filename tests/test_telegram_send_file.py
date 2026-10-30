"""``chela send-file`` (CMX-370): post a file from an agent into its Telegram topic.

The Bot API is never reached: every test swaps :func:`chela.telegram.relay.
_urllib_media_transport` (what :class:`BotSender` builds its multipart transport from)
for a recorder, and points ``$CHELA_DIR`` and the bindings file at ``tmp_path`` so the
operator's real token and topics are never read.
"""
from __future__ import annotations

import json
import sys

import pytest

from chela import config, main
from chela.telegram import relay, sendfile
from chela.telegram.bindings import BindingRegistry


class _Api:
    """A fake multipart transport: records each call, answers with ``responses`` in order."""

    def __init__(self, *responses):
        self.calls: list[tuple[str, str, dict, list]] = []
        self.responses = list(responses) or [{"ok": True, "result": {"message_id": 777}}]

    def factory(self, token):
        def transport(method, fields, files):
            self.calls.append((token, method, dict(fields), list(files)))
            resp = self.responses.pop(0)
            if isinstance(resp, BaseException):
                raise resp
            return resp
        return transport


@pytest.fixture
def tg(tmp_path, monkeypatch):
    """A sandboxed bridge: secrets.env with a token + chat, bindings @12→42 and @5→99."""
    chela_dir = tmp_path / "chela"
    chela_dir.mkdir()
    (chela_dir / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=tok-123\nTELEGRAM_CHAT_ID=-1009\n")
    bindings = chela_dir / "telegram-bindings.json"
    bindings.write_text(json.dumps({"chat_id": "-1009", "bindings": {"@12": "42", "@5": "99"}}))
    monkeypatch.setattr(config, "CHELA_DIR", chela_dir)
    monkeypatch.setenv("CHELA_ENV_FILE", "")
    monkeypatch.setenv("CHELA_TELEGRAM_BINDINGS", str(bindings))
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TMUX_PANE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CHELA_WID", "@12")
    api = _Api()
    monkeypatch.setattr(relay, "_urllib_media_transport", api.factory)
    f = tmp_path / "bundle.zip"
    f.write_bytes(b"PK\x03\x04zipdata")
    return {"api": api, "file": f, "dir": chela_dir, "bindings": bindings, "tmp": tmp_path}


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["chela", "send-file", *argv])
    main.main()


# --- binding resolution -----------------------------------------------------------------


def test_defaults_to_own_window_and_prints_message_id_and_topic(tg, monkeypatch, capsys):
    _run(monkeypatch, str(tg["file"]), "--caption", "the build")
    assert tg["api"].calls == [(
        "tok-123", "sendDocument",
        {"chat_id": "-1009", "message_thread_id": "42", "caption": "the build"},
        [("document", "bundle.zip", b"PK\x03\x04zipdata")],
    )]
    assert capsys.readouterr().out == (
        "sent bundle.zip (11 B) to @12's topic 42, message_id 777\n"
    )


@pytest.mark.parametrize("to", ["@5", "5"])
def test_to_overrides_own_window(tg, monkeypatch, capsys, to):
    _run(monkeypatch, str(tg["file"]), "--to", to)
    (_, _, fields, _), = tg["api"].calls
    assert fields == {"chat_id": "-1009", "message_thread_id": "99"}  # no caption field
    assert "to @5's topic 99, message_id 777" in capsys.readouterr().out


def test_missing_binding_exits_1_without_calling_telegram(tg, monkeypatch, capsys):
    monkeypatch.setenv("CHELA_WID", "@31")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(tg["file"]))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("chela send-file: no Telegram topic is bound to window @31 ")
    assert str(tg["bindings"]) in err
    assert tg["api"].calls == []


def test_no_window_known_exits_1(tg, monkeypatch, capsys):
    monkeypatch.delenv("CHELA_WID")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(tg["file"]))
    assert exc.value.code == 1
    assert "$CHELA_WID is unset" in capsys.readouterr().err
    assert tg["api"].calls == []


def test_resolve_thread_reads_the_registry():
    reg = BindingRegistry("-1")
    reg.bind("@3", 8)
    assert sendfile.resolve_thread(reg, "@3") == "8"
    with pytest.raises(sendfile.SendFileError, match="window @4"):
        sendfile.resolve_thread(reg, "@4")


# --- token / chat loading ---------------------------------------------------------------


def test_environment_wins_over_secrets_env(tg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-2002")
    sendfile.send_file(tg["file"], "@12")
    token, _, fields, _ = tg["api"].calls[0]
    assert (token, fields["chat_id"]) == ("env-tok", "-2002")


def test_chat_falls_back_to_the_bindings_file(tg):
    (tg["dir"] / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=tok-123\n")
    sendfile.send_file(tg["file"], "@12")
    assert tg["api"].calls[0][2]["chat_id"] == "-1009"


def test_no_chat_anywhere_fails(tg):
    (tg["dir"] / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=tok-123\n")
    tg["bindings"].write_text(json.dumps({"bindings": {"@12": "42"}}))
    with pytest.raises(sendfile.SendFileError, match="TELEGRAM_CHAT_ID is not set"):
        sendfile.send_file(tg["file"], "@12")
    assert tg["api"].calls == []


def test_no_token_fails(tg):
    (tg["dir"] / "secrets.env").write_text("TELEGRAM_CHAT_ID=-1009\n")
    with pytest.raises(sendfile.SendFileError, match="TELEGRAM_BOT_TOKEN is not set"):
        sendfile.send_file(tg["file"], "@12")
    assert tg["api"].calls == []


# --- the file ---------------------------------------------------------------------------


def test_over_50_mb_is_refused_before_any_upload(tg, monkeypatch, capsys):
    big = tg["tmp"] / "big.bin"
    with open(big, "wb") as fh:
        fh.truncate(50 * 1024 * 1024 + 1)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(big))
    assert exc.value.code == 1
    assert "big.bin is 50.0 MB, over Telegram's 50 MB limit" in capsys.readouterr().err
    assert tg["api"].calls == []


def test_exactly_50_mb_is_sent(tg):
    edge = tg["tmp"] / "edge.bin"
    with open(edge, "wb") as fh:
        fh.truncate(50 * 1024 * 1024)
    sent = sendfile.send_file(edge, "@12")
    assert sent.size == 50 * 1024 * 1024
    assert len(tg["api"].calls) == 1


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_not_a_file_fails(tg, kind):
    target = tg["tmp"] / "nope"
    if kind == "directory":
        target.mkdir()
    with pytest.raises(sendfile.SendFileError, match="not a file"):
        sendfile.send_file(target, "@12")
    assert tg["api"].calls == []


def test_unreadable_file_fails_closed(tg, monkeypatch):
    def boom(self):
        raise PermissionError("denied")
    monkeypatch.setattr(sendfile.Path, "read_bytes", boom)
    with pytest.raises(sendfile.SendFileError, match="could not read .*denied"):
        sendfile.send_file(tg["file"], "@12")
    assert tg["api"].calls == []


# --- the API ----------------------------------------------------------------------------


def test_api_error_exits_1_with_telegram_description(tg, monkeypatch, capsys):
    tg["api"].responses = [{"ok": False, "error_code": 400, "description": "Bad Request: topic closed"}]
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(tg["file"]))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "chela send-file: Telegram refused the file: Bad Request: topic closed\n"
    assert captured.out == ""


@pytest.mark.parametrize("error", [TimeoutError("read timed out"), ValueError("bad json")])
def test_transport_exception_is_a_failed_send(tg, error):
    tg["api"].responses = [error]
    with pytest.raises(sendfile.SendFileError, match="Telegram request failed: "):
        sendfile.send_file(tg["file"], "@12")


def test_unexpected_exception_is_not_swallowed(tg):
    tg["api"].responses = [RuntimeError("bug")]
    with pytest.raises(RuntimeError, match="bug"):
        sendfile.send_file(tg["file"], "@12")


def test_flood_control_is_retried_like_every_other_send(tg, monkeypatch):
    monkeypatch.setattr(relay.time, "sleep", lambda s: None)
    tg["api"].responses = [
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}},
        {"ok": True, "result": {"message_id": 5}},
    ]
    sent = sendfile.send_file(tg["file"], "@12")
    assert sent.message_id == 5
    assert len(tg["api"].calls) == 2


def test_ok_without_message_id_still_reports_success(tg, capsys, monkeypatch):
    tg["api"].responses = [{"ok": True, "result": {}}]
    _run(monkeypatch, str(tg["file"]))
    assert capsys.readouterr().out.endswith("message_id unknown\n")


# --- discoverability --------------------------------------------------------------------


def test_telegram_help_names_send_file(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["chela", "telegram", "--help"])
    with pytest.raises(SystemExit):
        main.main()
    assert "Outbound files: chela send-file" in capsys.readouterr().out
