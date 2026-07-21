"""Global rename — the tmux window name as the SINGLE SOURCE OF TRUTH.

Renaming a pane used to write a label into ``localStorage`` and stop there: the new
name never left the browser it was typed in (invisible on the phone), never reached
tmux, and never reached the bound Telegram topic. Now a rename is one server call —
``POST /api/agents/<wid>/rename`` → ``tmux rename-window`` — and every surface reads
the name back from tmux, so it lands everywhere at once.

Covered here: the endpoint (validation, wid-keying, collisions, the name lock) and
the Telegram propagation (a bound topic follows its window's name). The other half —
that a rename SURVIVES the 30s reconcile tick instead of being reverted — lives in
tests/test_agent_manager_naming.py, which is where the reconciler's tests are.

No live tmux: ``subprocess.run`` is monkeypatched.
"""
from __future__ import annotations

import types

import pytest

from chela.dashboard import app as dash
from chela.telegram import BindingRegistry, reconcile_bindings, topic_name_for


@pytest.fixture
def client():
    return dash.app.test_client()


class _FakeTmux:
    """Records every tmux argv; rename-window succeeds unless told otherwise."""

    def __init__(self, returncode: int = 0, stderr: str = ""):
        self.calls: list[list[str]] = []
        self._returncode = returncode
        self._stderr = stderr

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["tmux", "rename-window"]:
            return types.SimpleNamespace(returncode=self._returncode, stdout="",
                                         stderr=self._stderr)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def renames(self) -> list[tuple[str, str]]:
        return [(c[-2], c[-1]) for c in self.calls if c[:2] == ["tmux", "rename-window"]]

    def set_options(self) -> dict[str, str]:
        return {c[4]: c[5] for c in self.calls if c[:2] == ["tmux", "set-window-option"]}


@pytest.fixture
def tmux(monkeypatch):
    fake = _FakeTmux()
    monkeypatch.setattr(dash.subprocess, "run", fake)
    monkeypatch.setattr(dash.agent_manager.subprocess, "run", fake)
    monkeypatch.setattr(dash.discovery, "get_windows_by_id",
                        lambda: {"@2": "shell-1", "@5": "nautilus"})
    return fake


# --- the endpoint -------------------------------------------------------------

def test_rename_renames_the_tmux_window_and_locks_the_name(client, tmux):
    resp = client.post("/api/agents/@2/rename", json={"name": "billing-fix"})

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "wid": "@2", "name": "billing-fix"}
    # Targeted BY WINDOW ID (stable, unique) — never by name, which collides.
    target, name = tmux.renames()[0]
    assert target.endswith(":@2") and name == "billing-fix"
    # Locked against tmux's own renamers, so a shell-out can't clobber the new name.
    assert tmux.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


@pytest.mark.parametrize("bad", ["", "   ", "has space", "colon:name", "dot.name", "sl/ash"])
def test_rename_rejects_names_tmux_cannot_address(client, tmux, bad):
    # tmux reserves ':' (window index) and '.' (pane index) in target specs, so a
    # name carrying either is unaddressable — reuse the spawn path's _WINDOW_NAME_RE.
    resp = client.post("/api/agents/@2/rename", json={"name": bad})
    assert resp.status_code == 400
    assert tmux.renames() == []       # nothing shelled out


def test_rename_rejects_an_unknown_window(client, tmux):
    resp = client.post("/api/agents/@99/rename", json={"name": "ghost"})
    assert resp.status_code == 404
    assert tmux.renames() == []


def test_rename_rejects_a_duplicate_name(client, tmux):
    # Names are an identity users read AND that name->id lookups resolve on, so a
    # duplicate could send a by-name lookup to the wrong window.
    resp = client.post("/api/agents/@2/rename", json={"name": "nautilus"})
    assert resp.status_code == 409
    assert tmux.renames() == []


def test_rename_to_its_own_current_name_is_allowed(client, tmux):
    # The collision check must exclude the window itself (it already owns the name).
    resp = client.post("/api/agents/@5/rename", json={"name": "nautilus"})
    assert resp.status_code == 200


def test_rename_surfaces_a_tmux_failure(client, monkeypatch):
    fake = _FakeTmux(returncode=1, stderr="can't find window: @2")
    monkeypatch.setattr(dash.subprocess, "run", fake)
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", lambda: {"@2": "shell-1"})

    resp = client.post("/api/agents/@2/rename", json={"name": "billing-fix"})
    assert resp.status_code == 500
    assert "can't find window" in resp.get_json()["error"]


# --- Telegram propagation ------------------------------------------------------

def test_topic_name_prefers_a_deliberate_window_name_over_the_cwd():
    # The window name is the source of truth: a name a human chose wins over the
    # project basename, so the topic reads as what the human called the agent.
    assert topic_name_for("/home/liav/projects/chelamux", "billing-fix") == "billing-fix"
    # ...but a placeholder is not a choice, so the project still names the topic.
    assert topic_name_for("/home/liav/projects/chelamux", "shell-1") == "chelamux"


class _RecordingTopicApi:
    def __init__(self):
        self.renamed: list[tuple[str, str]] = []

    def create_topic(self, name):          # not exercised here
        raise AssertionError("should not create")

    def rename_topic(self, thread_id, name):
        self.renamed.append((str(thread_id), name))
        return True

    def close_topic(self, thread_id):
        raise AssertionError("should not close")


def test_renaming_a_window_renames_its_bound_telegram_topic():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.set_topic_name("@3", "chelamux")      # topic currently named for the project
    api = _RecordingTopicApi()

    # The daemon's next tick sees the window under its new name and follows it.
    changed = reconcile_bindings(reg, {"@3": "billing-fix"}, {"@3"}, api,
                                 cwd_for=lambda wid: "/home/liav/projects/chelamux")

    assert changed is True
    assert api.renamed == [("42", "billing-fix")]
    assert reg.topic_name("@3") == "billing-fix"


def test_a_topic_rename_telegram_rejects_is_retried_next_tick():
    class _RefusingApi(_RecordingTopicApi):
        def rename_topic(self, thread_id, name):
            super().rename_topic(thread_id, name)
            return False                       # e.g. missing Manage Topics permission

    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.set_topic_name("@3", "chelamux")
    api = _RefusingApi()

    reconcile_bindings(reg, {"@3": "billing-fix"}, {"@3"}, api)
    # The cached name is NOT advanced, so the next tick tries again rather than
    # believing a rename that never landed.
    assert reg.topic_name("@3") == "chelamux"
    reconcile_bindings(reg, {"@3": "billing-fix"}, {"@3"}, api)
    assert api.renamed == [("42", "billing-fix"), ("42", "billing-fix")]


def test_topic_name_cache_persists_and_survives_a_rebind():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.set_topic_name("@3", "billing-fix")

    reloaded = BindingRegistry.from_dict(reg.to_dict())
    assert reloaded.topic_name("@3") == "billing-fix"   # no needless resync on restart

    # Rebinding to a DIFFERENT topic must drop the cached name: an unknown name
    # resyncs once (safe), while a stale one would read as "in sync" and leave the
    # new topic misnamed forever.
    reloaded.bind("@3", "99")
    assert reloaded.topic_name("@3") is None
