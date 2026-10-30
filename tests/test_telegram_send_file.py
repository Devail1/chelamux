"""``chela telegram send-file`` (CMX-370): post a file from an agent into its Telegram topic.

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
        self.timeouts: list[float] = []
        self.responses = list(responses) or [{"ok": True, "result": {"message_id": 777}}]

    def factory(self, token, timeout=60):
        self.timeouts.append(timeout)
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
    monkeypatch.setattr(sendfile, "_ptb_installed", lambda: True)
    f = tmp_path / "bundle.zip"
    f.write_bytes(b"PK\x03\x04zipdata")
    return {"api": api, "file": f, "dir": chela_dir, "bindings": bindings, "tmp": tmp_path}


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["chela", "telegram", "send-file", *argv])
    main.main()


# --- binding resolution -----------------------------------------------------------------


def test_defaults_to_own_window_and_prints_message_id_and_topic(tg, monkeypatch, capsys):
    _run(monkeypatch, str(tg["file"]), "--caption", "the build")
    assert tg["api"].calls == [(
        "tok-123", "sendDocument",
        {"chat_id": "-1009", "message_thread_id": "42", "caption": "the build"},
        [("document", "bundle.zip", b"PK\x03\x04zipdata")],
    )]
    assert tg["api"].timeouts == [300.0]  # not the relay's 60 s screenshot budget
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
    assert err.startswith("chela telegram send-file: no Telegram topic is bound to window @31 ")
    assert str(tg["bindings"]) in err
    assert err.endswith("setup: skills/telegram-setup/SKILL.md\n")
    assert "Traceback" not in err
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
    with pytest.raises(sendfile.SendFileError, match=r"not configured \(no TELEGRAM_CHAT_ID, .*: see skills/telegram-setup/SKILL.md$"):
        sendfile.send_file(tg["file"], "@12")
    assert tg["api"].calls == []


# --- Telegram not set up: one actionable line, exit 1, no traceback --------------------


def test_no_token_is_a_clean_not_configured_exit(tg, monkeypatch, capsys):
    (tg["dir"] / "secrets.env").write_text("TELEGRAM_CHAT_ID=-1009\n")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(tg["file"]))
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        "chela telegram send-file: Telegram is not configured (no TELEGRAM_BOT_TOKEN in "
        f"the environment, chela.env or {tg['dir'] / 'secrets.env'}): see "
        "skills/telegram-setup/SKILL.md\n"
    )
    assert tg["api"].calls == []


def test_missing_extra_is_a_clean_not_installed_exit(tg, monkeypatch, capsys):
    monkeypatch.setattr(sendfile, "_ptb_installed", lambda: False)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(tg["file"]))
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        "chela telegram send-file: Telegram is not installed (the `telegram` extra is "
        "missing: uv tool install 'chelamux[telegram]'): see skills/telegram-setup/SKILL.md\n"
    )
    assert tg["api"].calls == []


def test_extra_detection_looks_for_python_telegram_bot(monkeypatch):
    monkeypatch.setitem(sys.modules, "telegram", None)
    assert sendfile._ptb_installed() is False


def test_extra_detection_finds_an_installed_package(monkeypatch):
    seen = []
    monkeypatch.setattr(sendfile.importlib.util, "find_spec",
                        lambda name: seen.append(name) or object())
    assert sendfile._ptb_installed() is True
    assert seen == ["telegram"]


CORE_ONLY = """
import sys
sys.modules["telegram"] = None   # python-telegram-bot absent: a core-only install
from chela import main
for argv in (["chela", "--help"], ["chela", "telegram", "--help"]):
    sys.argv = argv
    try:
        main.main()
    except SystemExit as e:
        assert e.code in (0, None), e.code
sys.argv = ["chela", "telegram", "send-file", "--to", "@1", __file__]
main.main()
"""


def test_core_only_install_runs_help_and_fails_send_file_cleanly(tmp_path):
    import subprocess
    script = tmp_path / "core_only.py"
    script.write_text(CORE_ONLY)
    r = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "CHELA_DIR": str(tmp_path), "CHELA_ENV_FILE": "",
             "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""},
    )
    assert r.returncode == 1, r.stderr
    assert "Outbound files: chela telegram send-file" in r.stdout
    assert r.stderr == (
        "chela telegram send-file: Telegram is not installed (the `telegram` extra is "
        "missing: uv tool install 'chelamux[telegram]'): see skills/telegram-setup/SKILL.md\n"
    )


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
    assert captured.err == "chela telegram send-file: Telegram refused the file: Bad Request: topic closed\n"
    assert captured.out == ""


@pytest.mark.parametrize("error", [TimeoutError("read timed out"), ValueError("bad json")])
def test_transport_exception_is_a_failed_send(tg, error):
    tg["api"].responses = [error]
    with pytest.raises(sendfile.SendFileError, match="Telegram request failed: ") as exc:
        sendfile.send_file(tg["file"], "@12")
    assert "may still have arrived" in str(exc.value)


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
    assert "Outbound files: chela telegram send-file" in capsys.readouterr().out


def test_core_help_does_not_advertise_send_file(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["chela", "--help"])
    with pytest.raises(SystemExit):
        main.main()
    out = capsys.readouterr().out
    assert "telegram" in out
    assert "send-file" not in out


def test_telegram_bridge_flags_still_parse_without_a_subcommand(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "cmd_telegram", lambda args: seen.append(args))
    monkeypatch.setattr(main, "cmd_telegram_send_file", lambda args: seen.append("send-file"))
    monkeypatch.setattr(sys, "argv", ["chela", "telegram", "--no-inbound", "--bind", "@3:42"])
    main.main()
    (args,) = seen
    assert args.tg_cmd is None and args.no_inbound and args.bind == ["@3:42"]


def test_filename_cannot_break_the_multipart_header(tg):
    odd = tg["tmp"] / 'a "b"\\c\r\nX-Evil: 1.zip'
    odd.write_bytes(b"z")
    sendfile.send_file(odd, "@12")
    (_, _, _, files), = tg["api"].calls
    assert files == [("document", "a bcX-Evil: 1.zip", b"z")]


def test_a_name_of_only_unsafe_characters_falls_back(tg):
    sender = relay.BotSender("t", "c", media_transport=tg["api"].factory("t"))
    sender.send_document('"\n"', b"z")
    assert tg["api"].calls[0][3] == [("document", "file", b"z")]
