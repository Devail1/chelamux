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
    dispatched_window_ids,
    reconcile_bindings,
    topic_name_for,
)


class _StubTopicApi:
    """Records create/close; hands out canned thread ids in sequence."""

    def __init__(self, threads=("100", "101", "102"), *, create_ok=True, rename_ok=True):
        self._threads = list(threads)
        self._create_ok = create_ok
        self._rename_ok = rename_ok
        self.created: list[str] = []          # names passed to create_topic
        self.closed: list[str] = []           # thread ids passed to close_topic
        self.renamed: list[tuple[str, str]] = []   # (thread_id, name) pairs

    def create_topic(self, name: str):
        self.created.append(name)
        if not self._create_ok or not self._threads:
            return None
        return self._threads.pop(0)

    def rename_topic(self, thread_id, name: str):
        self.renamed.append((str(thread_id), name))
        return self._rename_ok

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
    # Idempotence: an already-bound, still-live agent window whose topic already
    # carries the window's name is left entirely untouched — no API calls at all.
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.set_topic_name("@3", "coder")         # topic already in sync
    api = _StubTopicApi()
    changed = reconcile_bindings(reg, {"@3": "coder"}, {"@3"}, api)
    assert changed is False
    assert api.created == []                  # NO createForumTopic
    assert api.closed == []
    assert api.renamed == []                  # NO editForumTopic — steady state
    assert reg.thread_for_window("@3") == "42"


def test_window_rename_keeps_its_topic_and_renames_it():
    # Match by window_id, never by name — a rename must not orphan the topic. And
    # now it PROPAGATES: the tmux name is the source of truth, so the bound topic is
    # renamed to match instead of drifting away from the window it bridges.
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.set_topic_name("@3", "coder")
    api = _StubTopicApi()
    changed = reconcile_bindings(reg, {"@3": "coder-renamed"}, {"@3"}, api)
    assert changed is True
    assert api.created == []                            # same topic, not a new one
    assert api.renamed == [("42", "coder-renamed")]     # ...renamed to match
    assert reg.thread_for_window("@3") == "42"
    assert reg.topic_name("@3") == "coder-renamed"


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


def test_topic_manager_rename_sends_only_the_name():
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        return {"ok": True, "result": True}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.rename_topic(42, "coder") is True
    # Only `name` — no icon fields, so a rename never clobbers the topic's icon.
    assert calls == [
        ("editForumTopic", {"chat_id": "777", "message_thread_id": 42, "name": "coder"})
    ]


def test_topic_manager_rename_treats_topic_not_modified_as_success():
    # Telegram says "you wrote the name it already had" — that is the desired state,
    # not a failure. Reading it as one is what leaked a write per topic per tick.
    def transport(method, fields):
        return {"ok": False, "error_code": 400, "description": "Bad Request: TOPIC_NOT_MODIFIED"}

    assert TopicManager("tok", "777", transport=transport).rename_topic(976, "chelamux") is True


def test_topic_manager_rename_reports_a_real_failure():
    def transport(method, fields):
        return {"ok": False, "description": "not enough rights to manage topics"}

    assert TopicManager("tok", "777", transport=transport).rename_topic(42, "coder") is False


def test_not_modified_is_cached_so_the_reconcile_loop_goes_quiet():
    # THE BUG (CMX-51): a binding written before the name cache existed reads as
    # "unsynced", so tick 1 rewrites the name Telegram already has. Telegram answers
    # TOPIC_NOT_MODIFIED — and every later tick must make ZERO calls, forever.
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        return {"ok": False, "error_code": 400, "description": "Bad Request: TOPIC_NOT_MODIFIED"}

    reg = BindingRegistry("777")
    reg.bind("@3", "976")                      # bound, but no cached topic name
    api = TopicManager("tok", "777", transport=transport)

    assert reconcile_bindings(reg, {"@3": "chelamux"}, {"@3"}, api) is True
    assert len(calls) == 1                     # one resync write...
    assert reg.topic_name("@3") == "chelamux"  # ...whose no-op answer is cached

    for _ in range(5):
        assert reconcile_bindings(reg, {"@3": "chelamux"}, {"@3"}, api) is False
    assert len(calls) == 1                     # ...and never written again


def test_a_genuine_rename_still_produces_exactly_one_api_call():
    # The leak fix must not break the feature: a real tmux rename propagates, once.
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        return {"ok": True, "result": True}

    reg = BindingRegistry("777")
    reg.bind("@3", "976")
    reg.set_topic_name("@3", "chelamux")
    api = TopicManager("tok", "777", transport=transport)

    assert reconcile_bindings(reg, {"@3": "chelamux"}, {"@3"}, api) is False
    assert calls == []                                   # steady state: silent
    assert reconcile_bindings(reg, {"@3": "cmx-51"}, {"@3"}, api) is True
    assert [f["name"] for _, f in calls] == ["cmx-51"]   # the rename went out, once
    assert reconcile_bindings(reg, {"@3": "cmx-51"}, {"@3"}, api) is False
    assert len(calls) == 1                               # and is not re-sent


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


# --------------------------------------------------------------------------
# Dispatcher-spawned agents (CMX-73) — no topic while working, one when BLOCKED
# --------------------------------------------------------------------------

def test_dispatched_agent_gets_no_topic_while_working():
    # The whole point: a fleet of short-lived cmx-N workers must not churn topics.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    changed = reconcile_bindings(
        reg, {"@9": "cmx-73"}, {"@9"}, api,
        dispatched={"@9"}, gate_for=lambda wid: None,
    )
    assert changed is False
    assert api.created == []
    assert reg.thread_for_window("@9") is None


def test_dispatched_agent_gets_a_topic_the_moment_it_blocks():
    # The trade-off this must not eat: a BLOCKED agent still reaches the phone.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    gates = {}
    changed = reconcile_bindings(
        reg, {"@9": "cmx-73"}, {"@9"}, api,
        dispatched={"@9"}, gate_for=gates.get,
    )
    assert changed is False and api.created == []   # working away — no topic

    gates["@9"] = object()                          # ...now it wants a human
    changed = reconcile_bindings(
        reg, {"@9": "cmx-73"}, {"@9"}, api,
        dispatched={"@9"}, gate_for=gates.get,
    )
    assert changed is True
    assert api.created == ["cmx-73"]
    assert reg.thread_for_window("@9") == "42"


def test_dispatched_binding_survives_the_gate_resolving():
    # Unbinding on gate-resolve would archive the topic mid-conversation and create a
    # BRAND-NEW one on the next gate — topic churn per gate, the very disease. The
    # binding is dropped by the normal reap, when the window dies.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42", "43"])
    gates = {"@9": object()}
    reconcile_bindings(reg, {"@9": "cmx-73"}, {"@9"}, api,
                       dispatched={"@9"}, gate_for=gates.get)
    gates.clear()                                   # the human answered
    changed = reconcile_bindings(reg, {"@9": "cmx-73"}, {"@9"}, api,
                                 dispatched={"@9"}, gate_for=gates.get)
    assert changed is False
    assert api.closed == []
    assert reg.thread_for_window("@9") == "42"

    # ...and when the window exits, the topic is archived exactly once.
    changed = reconcile_bindings(reg, {}, set(), api, dispatched=set(), gate_for=gates.get)
    assert changed is True
    assert api.closed == ["42"]
    assert reg.thread_for_window("@9") is None


def test_human_windows_are_unaffected_by_the_dispatched_filter():
    # The orchestrator / a project session keeps its topic exactly as before.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    changed = reconcile_bindings(
        reg, {"@6": "orchestrator", "@9": "cmx-73"}, {"@6", "@9"}, api,
        dispatched={"@9"}, gate_for=lambda wid: None,
    )
    assert changed is True
    assert api.created == ["orchestrator"]
    assert reg.thread_for_window("@6") == "42"
    assert reg.thread_for_window("@9") is None


def test_bind_dispatched_true_restores_bind_everything():
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    changed = reconcile_bindings(
        reg, {"@9": "cmx-73"}, {"@9"}, api,
        dispatched={"@9"}, gate_for=lambda wid: None, bind_dispatched=True,
    )
    assert changed is True
    assert api.created == ["cmx-73"]


def test_a_failing_gate_probe_leaves_the_window_unbound():
    # A gate probe that raises must never wedge the reconcile loop.
    def boom(wid):
        raise RuntimeError("event log unreadable")

    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    changed = reconcile_bindings(
        reg, {"@9": "cmx-73"}, {"@9"}, api, dispatched={"@9"}, gate_for=boom,
    )
    assert changed is False
    assert api.created == []


def test_dispatched_window_ids_reads_the_run_row_not_the_name():
    # The run row OWNS the wid. A live human window named like a worker is NOT
    # dispatched, and a dispatched worker renamed by a human still is.
    runs = [
        {"status": "running", "window_id": "@9", "window_name": "cmx-73"},
        {"status": "claimed", "window_id": "@10", "window_name": "renamed-by-a-human"},
        {"status": "done", "window_id": "@6", "window_name": "cmx-12"},      # stale boot
        {"status": "awaiting_review", "window_id": "@7", "window_name": "cmx-13"},
        {"status": "running", "window_id": None, "window_name": "cmx-14"},   # pre-CMX-69 row
    ]
    assert dispatched_window_ids(runs) == {"@9", "@10"}
