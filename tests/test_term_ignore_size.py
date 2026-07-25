"""Per-client `ignore-size` unit tests (CMX-175).

`window-size largest` (scripts/agent-terminals.sh) sizes a grouped session's
shared window to its BIGGEST attached tmux client, so a phone pane opened
alongside a big desktop wall renders at the desktop's geometry. Rather than
`window-size latest` (which trades this for the opposite bug — see
agent-terminals.sh's own comment on it), a client that a wall tile reports as
NOT being watched gets tmux's per-client `ignore-size` flag, dropping it out of
the `largest` computation.

These tests exercise the pure helpers (`_session_client_ttys`,
`_set_client_ignore_size`, `_claim_new_client_tty`) and the `/api/term/<wid>/
watch` route directly, stubbing `subprocess.run` so no real tmux/ttyd is
needed — mirrors tests/test_share_reaper.py's approach to the other tmux-backed
term_* helpers.
"""

from __future__ import annotations

import pytest

from chela.dashboard import app as dash


@pytest.fixture(autouse=True)
def _isolate():
    dash._TERM_CID_TTY.clear()
    yield
    dash._TERM_CID_TTY.clear()


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_session_client_ttys_parses_and_filters_blank_lines(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompleted(stdout="/dev/pts/3\n\n/dev/pts/7\n")

    monkeypatch.setattr(dash.subprocess, "run", fake_run)
    ttys = dash._session_client_ttys("webterm_chela_%409")
    assert ttys == {"/dev/pts/3", "/dev/pts/7"}
    assert calls[0][:3] == ["tmux", "list-clients", "-t"]


def test_session_client_ttys_empty_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(dash.subprocess, "run", lambda *a, **k: _FakeCompleted(returncode=1, stdout="/dev/pts/3\n"))
    assert dash._session_client_ttys("some_session") == set()


def test_session_client_ttys_empty_on_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("tmux not found")
    monkeypatch.setattr(dash.subprocess, "run", boom)
    assert dash._session_client_ttys("some_session") == set()


def test_set_client_ignore_size_sets_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(dash.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _FakeCompleted())
    dash._set_client_ignore_size("/dev/pts/3", ignore=True)
    assert calls == [["tmux", "refresh-client", "-t", "/dev/pts/3", "-f", "ignore-size"]]


def test_set_client_ignore_size_clears_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(dash.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _FakeCompleted())
    dash._set_client_ignore_size("/dev/pts/3", ignore=False)
    assert calls == [["tmux", "refresh-client", "-t", "/dev/pts/3", "-f", "!ignore-size"]]


def test_set_client_ignore_size_swallows_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("client gone")
    monkeypatch.setattr(dash.subprocess, "run", boom)
    dash._set_client_ignore_size("/dev/pts/3", ignore=True)   # must not raise


def test_claim_new_client_tty_picks_the_new_one(monkeypatch):
    monkeypatch.setattr(dash.time, "sleep", lambda s: None)
    monkeypatch.setattr(dash, "_session_client_ttys", lambda session: {"/dev/pts/1", "/dev/pts/9"})
    tty = dash._claim_new_client_tty("sess", before={"/dev/pts/1"})
    assert tty == "/dev/pts/9"


def test_claim_new_client_tty_excludes_already_claimed_ttys(monkeypatch):
    """Two connections racing to the same session: a tty another connection
    already registered must never be handed out again, even if it's "new"
    relative to THIS connection's own before-snapshot."""
    monkeypatch.setattr(dash.time, "sleep", lambda s: None)
    monkeypatch.setattr(dash, "_session_client_ttys", lambda session: {"/dev/pts/1", "/dev/pts/9"})
    dash._TERM_CID_TTY[("@9", "other-cid")] = "/dev/pts/9"
    tty = dash._claim_new_client_tty("sess", before={"/dev/pts/1"})
    assert tty is None


def test_claim_new_client_tty_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(dash.time, "sleep", lambda s: None)
    monkeypatch.setattr(dash, "_session_client_ttys", lambda session: {"/dev/pts/1"})
    tty = dash._claim_new_client_tty("sess", before={"/dev/pts/1"})
    assert tty is None


def test_api_term_watch_targets_only_the_registered_tty(monkeypatch):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    dash._TERM_CID_TTY[("@9", "cid-A")] = "/dev/pts/3"
    calls = []
    monkeypatch.setattr(dash, "_set_client_ignore_size", lambda tty, ignore: calls.append((tty, ignore)))

    resp = dash.app.test_client().post("/api/term/@9/watch", json={"cid": "cid-A", "watching": False})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "tracked": True}
    assert calls == [("/dev/pts/3", True)]   # not watching -> ignore=True

    resp = dash.app.test_client().post("/api/term/@9/watch", json={"cid": "cid-A", "watching": True})
    assert calls[-1] == ("/dev/pts/3", False)   # watching -> ignore=False


def test_api_term_watch_unregistered_cid_is_a_noop(monkeypatch):
    """A cid term_ws never managed to claim a tty for (e.g. the race lost) must
    not touch ANY client — there is nothing to safely target."""
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    calls = []
    monkeypatch.setattr(dash, "_set_client_ignore_size", lambda *a: calls.append(a))

    resp = dash.app.test_client().post("/api/term/@9/watch", json={"cid": "unknown-cid", "watching": False})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "tracked": False}
    assert calls == []


def test_api_term_watch_404s_for_unknown_window(monkeypatch):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {})
    resp = dash.app.test_client().post("/api/term/@404/watch", json={"cid": "x", "watching": True})
    assert resp.status_code == 404
