"""Auto-topics reconcile (Slice B) — populate the registry, no live Telegram.

The reconcile loop is exercised against a **stub topic API** (fake
createForumTopic/closeForumTopic returning canned thread ids) and a fake
``discovery`` window set, so these lock in the lifecycle with zero live Telegram
calls:

  * a new agent window → one create + bind;
  * a non-agent window (shell/server) → skipped, never gets a topic;
  * a dead (no-longer-live) window → close + unbind;
  * a restart with a persisted binding → NO duplicate create (idempotent, by id);
  * a topic-closed event → unbind only (the agent is left running);
  * ``TopicManager`` maps the Bot API response to a thread id / bool over a stub
    transport (the same injectable transport the outbound relay uses).
"""
from __future__ import annotations

import os

from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import (
    TopicClosedHandler,
    TopicManager,
    reconcile_bindings,
    topic_name_for,
)


class _StubTopicApi:
    """Records create/close; hands out canned thread ids in sequence."""

    def __init__(self, threads=("100", "101", "102"), *, create_ok=True):
        self._threads = list(threads)
        self._create_ok = create_ok
        self.created: list[str] = []          # names passed to create_topic
        self.closed: list[str] = []           # thread ids passed to close_topic

    def create_topic(self, name: str):
        self.created.append(name)
        if not self._create_ok or not self._threads:
            return None
        return self._threads.pop(0)

    def close_topic(self, thread_id):
        self.closed.append(str(thread_id))
        return True


# --------------------------------------------------------------------------
# reconcile() — the pure provision + reap diff
# --------------------------------------------------------------------------

def test_new_agent_window_gets_one_topic_and_binding():
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    changed = reconcile_bindings(reg, {"@3": "coder"}, {"@3"}, api)
    assert changed is True
    assert api.created == ["coder"]          # topic named after the window
    assert reg.thread_for_window("@3") == "42"


def test_non_agent_window_is_skipped():
    # A shell / dev-server window is live but not in agent_ids — no topic for it.
    reg = BindingRegistry("777")
    api = _StubTopicApi()
    changed = reconcile_bindings(reg, {"@3": "coder", "@4": "shell"}, {"@3"}, api)
    assert api.created == ["coder"]
    assert reg.thread_for_window("@4") is None
    assert changed is True


def test_dead_window_topic_is_closed_and_unbound():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    api = _StubTopicApi()
    # @3 is bound but no longer live (not in live_windows / agent_ids) → reap.
    changed = reconcile_bindings(reg, {}, set(), api)
    assert changed is True
    assert api.closed == ["42"]
    assert reg.thread_for_window("@3") is None


def test_restart_with_persisted_binding_does_not_double_create():
    # Idempotence: an already-bound, still-live agent window is left untouched.
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    api = _StubTopicApi()
    changed = reconcile_bindings(reg, {"@3": "coder"}, {"@3"}, api)
    assert changed is False
    assert api.created == []                  # NO createForumTopic
    assert api.closed == []
    assert reg.thread_for_window("@3") == "42"


def test_window_rename_keeps_its_topic():
    # Match by window_id, never by name — a rename must not orphan the topic.
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    api = _StubTopicApi()
    changed = reconcile_bindings(reg, {"@3": "coder-renamed"}, {"@3"}, api)
    assert changed is False
    assert api.created == []
    assert reg.thread_for_window("@3") == "42"


def test_mixed_provision_and_reap_in_one_tick():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")                      # will die
    api = _StubTopicApi(threads=["77"])
    # @3 gone, @7 is a new agent window.
    changed = reconcile_bindings(reg, {"@7": "newbie"}, {"@7"}, api)
    assert changed is True
    assert api.created == ["newbie"]
    assert api.closed == ["42"]
    assert reg.thread_for_window("@7") == "77"
    assert reg.thread_for_window("@3") is None


def test_failed_create_leaves_window_unbound_for_retry():
    reg = BindingRegistry("777")
    api = _StubTopicApi(create_ok=False)      # createForumTopic rejected (perms?)
    changed = reconcile_bindings(reg, {"@3": "coder"}, {"@3"}, api)
    assert changed is False
    assert api.created == ["coder"]           # attempted...
    assert reg.thread_for_window("@3") is None  # ...but not bound; retries next tick


def test_idempotent_across_repeated_ticks():
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    reconcile_bindings(reg, {"@3": "coder"}, {"@3"}, api)
    # Second identical tick creates nothing new.
    changed = reconcile_bindings(reg, {"@3": "coder"}, {"@3"}, api)
    assert changed is False
    assert api.created == ["coder"]           # still just the one create


# --------------------------------------------------------------------------
# TopicClosedHandler — Telegram topic-closed → unbind only (no kill)
# --------------------------------------------------------------------------

def test_topic_closed_unbinds_only():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    saved: list[int] = []
    handler = TopicClosedHandler(reg, on_change=lambda: saved.append(1))
    # Wire id arrives as an int; the registry compares as str.
    assert handler.handle(42) is True
    assert reg.thread_for_window("@3") is None   # unbound
    assert saved == [1]                          # persisted


def test_topic_closed_for_unbound_thread_is_a_noop():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    saved: list[int] = []
    handler = TopicClosedHandler(reg, on_change=lambda: saved.append(1))
    assert handler.handle(999) is False
    assert reg.thread_for_window("@3") == "42"   # untouched
    assert saved == []                           # no save on no-op


def test_topic_closed_without_on_change_still_unbinds():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    handler = TopicClosedHandler(reg)
    assert handler.handle("42") is True
    assert reg.thread_for_window("@3") is None


# --------------------------------------------------------------------------
# TopicManager — Bot API response mapping over a stub transport
# --------------------------------------------------------------------------

def test_topic_manager_create_returns_thread_id():
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        return {"ok": True, "result": {"message_thread_id": 55, "name": fields["name"]}}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.create_topic("coder") == "55"     # normalised to str
    assert calls == [("createForumTopic", {"chat_id": "777", "name": "coder"})]


def test_topic_manager_create_returns_none_on_api_error():
    def transport(method, fields):
        return {"ok": False, "description": "not enough rights to manage topics"}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.create_topic("coder") is None


def test_topic_manager_create_returns_none_without_thread_id():
    def transport(method, fields):
        return {"ok": True, "result": {}}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.create_topic("coder") is None


def test_topic_manager_close_reports_success_and_failure():
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        return {"ok": True, "result": True}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.close_topic(42) is True
    assert calls == [("closeForumTopic", {"chat_id": "777", "message_thread_id": 42})]

    def failing(method, fields):
        return {"ok": False, "description": "TOPIC_ID_INVALID"}

    assert TopicManager("tok", "777", transport=failing).close_topic(42) is False


# --------------------------------------------------------------------------
# topic_name_for — name a topic after the agent's project, not the tmux window
# --------------------------------------------------------------------------

def test_topic_name_for_project_path_returns_basename():
    # A project cwd → its basename, so "shell-1" reads as the real project.
    assert topic_name_for("/home/liav/projects/chelamux", "shell-1") == "chelamux"


def test_topic_name_for_strips_trailing_slash():
    assert topic_name_for("/home/liav/projects/nautilus/", "shell-2") == "nautilus"


def test_topic_name_for_home_dir_falls_back_to_window_name():
    # A ~-rooted session must NOT become the login-name basename (e.g. "liavedunix").
    assert topic_name_for(os.path.expanduser("~"), "orchestrator") == "orchestrator"


def test_topic_name_for_root_falls_back_to_window_name():
    assert topic_name_for("/", "shell-3") == "shell-3"


def test_topic_name_for_empty_or_none_falls_back_to_window_name():
    assert topic_name_for("", "shell-4") == "shell-4"
    assert topic_name_for(None, "shell-4") == "shell-4"


def test_reconcile_names_topic_after_project_when_cwd_for_given():
    # With a cwd resolver injected, the topic is named for the agent's project.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    cwds = {"@3": "/home/liav/projects/chelamux"}
    changed = reconcile_bindings(
        reg, {"@3": "shell-1"}, {"@3"}, api, cwd_for=cwds.get
    )
    assert changed is True
    assert api.created == ["chelamux"]          # project basename, not "shell-1"
    assert reg.thread_for_window("@3") == "42"


def test_reconcile_falls_back_to_window_name_for_home_cwd():
    # A ~-rooted agent keeps its (meaningful) tmux window name.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    cwds = {"@3": os.path.expanduser("~")}
    reconcile_bindings(reg, {"@3": "orchestrator"}, {"@3"}, api, cwd_for=cwds.get)
    assert api.created == ["orchestrator"]


def test_reconcile_without_cwd_for_uses_window_name():
    # No resolver injected (back-compat) → topic named after the window as before.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    reconcile_bindings(reg, {"@3": "coder"}, {"@3"}, api)
    assert api.created == ["coder"]
