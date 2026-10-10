"""CMX-39: a Remote Control session's claude.ai / desktop name follows its window name.

Every rename chela makes of a chela-launched Remote Control window — a manual dashboard
rename, the reconcile loop's duplicate ``-N`` — queues ``/rename <new name>`` through
:func:`chela.rc_rename.request`, and it is typed into the session ONLY when that session
is idle with an empty prompt (a ghost suggestion counts as empty; a typed draft does not).
Otherwise it stays queued and the daemon's :func:`chela.rc_rename.flush_pending` retries.

No live tmux: a fake tmux keeps per-window user options, and the send/capture/status
seams are stubbed.
"""
from __future__ import annotations

import logging
import subprocess
import types

import pytest

from chela import agent_manager, config, messenger, rc_rename
from chela.dashboard import app as dash

# Real `capture-pane -e` shapes of Claude Code's input line.
EMPTY = "\x1b[39m❯ \x1b[0m\n"
GHOST = '\x1b[39m❯ \x1b[2mTry "fix lint errors"\x1b[0m\n'
DRAFT = "\x1b[39m❯ half-typed thought\n"
NO_PROMPT = "✻ Thinking… (12s · ↓ 300 tokens)\n"


class _FakeTmux:
    """Window user options + the calls rc_rename/reconcile make against them."""

    def __init__(self, options: dict[str, dict[str, str]], reconcile_rows: str = ""):
        self.options = options              # {wid: {option: value}}
        self.reconcile_rows = reconcile_rows
        self.calls: list[list[str]] = []

    @staticmethod
    def _wid(target: str) -> str:
        return target.rsplit(":", 1)[-1]

    def __call__(self, cmd, *a, **kw):
        self.calls.append(list(cmd))
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["tmux", "display-message"]:
            wid = self._wid(cmd[cmd.index("-t") + 1])
            opt = cmd[-1][2:-1]
            ok.stdout = self.options.get(wid, {}).get(opt, "") + "\n"
        elif cmd[:2] == ["tmux", "set-window-option"]:
            if "-u" in cmd:
                self.options.setdefault(self._wid(cmd[cmd.index("-t") + 1]), {}).pop(cmd[-1], None)
            else:
                self.options.setdefault(self._wid(cmd[3]), {})[cmd[4]] = cmd[5]
        elif cmd[:2] == ["tmux", "list-windows"]:
            if rc_rename.PENDING_OPTION in cmd[-1]:
                ok.stdout = "".join(
                    f"{w}\t{o.get(rc_rename.PENDING_OPTION, '')}\t"
                    f"{o.get(rc_rename.PUSHED_OPTION, '')}\n"
                    for w, o in self.options.items())
            else:
                ok.stdout = self.reconcile_rows
        return ok


class _Session:
    """The stubbed seams: native status, the ANSI pane, and the messenger send."""

    def __init__(self, monkeypatch, *, status="idle", pane=EMPTY, send_ok=True):
        self.status, self.pane, self.send_ok = status, pane, send_ok
        self.sent: list[tuple[str, str, dict]] = []
        monkeypatch.setattr(rc_rename, "_status", lambda wid: self.status)
        monkeypatch.setattr(messenger, "capture_pane", lambda wid, ansi=False: self.pane)
        monkeypatch.setattr(messenger, "send_tmux", self._send)

    def _send(self, wid, text, **kw):
        self.sent.append((wid, text, kw))
        return self.send_ok

    def texts(self) -> list[str]:
        return [t for _, t, _ in self.sent]


@pytest.fixture
def tmux(monkeypatch):
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    fake = _FakeTmux({"@2": {rc_rename.PUSHED_OPTION: "shell-old"}})
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


# --- the two rename paths both push /rename -------------------------------------------------

def test_a_manual_dashboard_rename_pushes_slash_rename(monkeypatch, tmux):
    s = _Session(monkeypatch)
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", lambda: {"@2": "shell-old"})

    r = dash.app.test_client().post("/api/agents/@2/rename", json={"name": "billing-fix"})

    assert r.status_code == 200
    assert s.texts() == ["/rename billing-fix"]
    # Never preceded by the Escape a slash command normally gets — that would cancel a turn.
    assert s.sent[0][2] == {"interrupt": False}
    assert tmux.options["@2"][rc_rename.PUSHED_OPTION] == "billing-fix"
    assert rc_rename.PENDING_OPTION not in tmux.options["@2"]


def test_a_duplicate_suffix_rename_pushes_slash_rename(monkeypatch, tmux):
    s = _Session(monkeypatch)
    tmux.options = {"@1": {}, "@2": {rc_rename.PUSHED_OPTION: "chelamux"}}
    tmux.reconcile_rows = ("@1\tchelamux\tclaude\t1\t0\t0\t\n"
                           "@2\tchelamux\tclaude\t2\t0\t0\t\n")

    assert agent_manager.reconcile_window_names() == ["chelamux -> chelamux-2"]
    assert s.sent == [("@2", "/rename chelamux-2", {"interrupt": False})]
    assert tmux.options["@2"][rc_rename.PUSHED_OPTION] == "chelamux-2"


def test_a_window_launched_without_remote_control_gets_no_slash_rename(monkeypatch, tmux):
    """A dispatcher agent, a judge, a plain shell, or Remote Control off: never typed into."""
    s = _Session(monkeypatch)
    tmux.options = {"@3": {}}

    assert rc_rename.request("@3", "renamed") == "not-rc"
    assert s.sent == []
    assert rc_rename.PENDING_OPTION not in tmux.options["@3"]


def test_renaming_back_to_the_pushed_name_sends_nothing(monkeypatch, tmux):
    s = _Session(monkeypatch)
    assert rc_rename.request("@2", "shell-old") == "current"
    assert s.sent == []
    assert rc_rename.PENDING_OPTION not in tmux.options["@2"]


# --- delivery safety: idle AND empty, else queued -------------------------------------------

@pytest.mark.parametrize("status", ["busy", "waiting", None])
def test_a_session_that_is_not_idle_does_not_receive_it_until_idle(monkeypatch, tmux, status):
    s = _Session(monkeypatch, status=status)

    assert rc_rename.request("@2", "billing-fix") == "busy"
    assert s.sent == []
    assert tmux.options["@2"][rc_rename.PENDING_OPTION] == "billing-fix"
    assert rc_rename.flush_pending() == {"@2": "busy"}       # still busy: still held
    assert s.sent == []

    s.status = "idle"
    assert rc_rename.flush_pending() == {"@2": "sent"}
    assert s.texts() == ["/rename billing-fix"]
    assert rc_rename.PENDING_OPTION not in tmux.options["@2"]
    assert rc_rename.flush_pending() == {}                  # nothing re-sent


@pytest.mark.parametrize("pane", [DRAFT, NO_PROMPT], ids=["typed-draft", "no-prompt"])
def test_a_typed_draft_does_not_receive_it_until_the_prompt_is_empty(monkeypatch, tmux, pane):
    s = _Session(monkeypatch, pane=pane)

    assert rc_rename.request("@2", "billing-fix") == "draft"
    assert s.sent == []
    assert tmux.options["@2"][rc_rename.PENDING_OPTION] == "billing-fix"

    s.pane = EMPTY
    assert rc_rename.flush_pending() == {"@2": "sent"}
    assert s.texts() == ["/rename billing-fix"]


def test_a_ghost_suggestion_counts_as_an_empty_prompt(monkeypatch, tmux):
    s = _Session(monkeypatch, pane=GHOST)
    assert rc_rename.request("@2", "billing-fix") == "sent"
    assert s.texts() == ["/rename billing-fix"]


def test_prompt_is_empty_reads_sgr_not_plain_text():
    assert rc_rename.prompt_is_empty(EMPTY)
    assert rc_rename.prompt_is_empty(GHOST)
    assert not rc_rename.prompt_is_empty(DRAFT)
    assert not rc_rename.prompt_is_empty(NO_PROMPT)


def test_a_failed_send_stays_queued_and_is_logged(monkeypatch, tmux, caplog):
    s = _Session(monkeypatch, send_ok=False)
    with caplog.at_level(logging.WARNING, logger="chela.rc_rename"):
        assert rc_rename.request("@2", "billing-fix") == "failed"
    assert "FAILED" in caplog.text
    assert tmux.options["@2"][rc_rename.PENDING_OPTION] == "billing-fix"
    assert tmux.options["@2"][rc_rename.PUSHED_OPTION] == "shell-old"

    s.send_ok = True
    assert rc_rename.flush_pending() == {"@2": "sent"}


def test_the_latest_rename_wins_while_queued(monkeypatch, tmux):
    s = _Session(monkeypatch, status="busy")
    rc_rename.request("@2", "first")
    rc_rename.request("@2", "second")
    s.status = "idle"
    rc_rename.flush_pending()
    assert s.texts() == ["/rename second"]


def test_an_unsafe_name_is_never_typed(monkeypatch, tmux):
    s = _Session(monkeypatch)
    assert rc_rename.request("@2", "evil\x1b[2J\nrm -rf") == "unsafe"
    assert s.sent == []


def test_the_daemon_tick_flushes_the_queue(monkeypatch):
    """🔴 WIRING — drives ONE real pass of ``cmd_run``'s loop (the harness
    ``tests/test_context.py::_run_one_daemon_tick`` uses) and proves the loop calls
    ``flush_pending``. A source-substring check could not fail: dead-coding the call
    (``if False: rc_rename.flush_pending()``) leaves the text intact (defeat shape 01)."""
    import chela.main as main
    from tests.test_context import _run_one_daemon_tick

    _run_one_daemon_tick(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(main.rc_rename, "flush_pending", lambda: calls.append(1) or {})

    main.cmd_run(types.SimpleNamespace())

    assert calls == [1], "the daemon tick never flushed the queued /rename"


# --- the messenger seam ---------------------------------------------------------------------

def test_send_tmux_without_interrupt_sends_no_escape(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(messenger, "refuses_paste", lambda wid: None)
    monkeypatch.setattr(messenger.time, "sleep", lambda s: None)
    monkeypatch.setattr(messenger.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(
                            returncode=0, stdout="", stderr=""))

    assert messenger.send_tmux("@2", "/rename x", interrupt=False)
    assert not any("Escape" in c for c in calls)
    calls.clear()
    assert messenger.send_tmux("@2", "/rename x")
    assert any("Escape" in c for c in calls)                 # the default is unchanged
