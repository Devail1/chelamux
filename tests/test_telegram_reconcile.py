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

And the dispatcher-spawned agents of CMX-73, which are bound LAZILY — no topic while they
work, one the moment they block. The load-bearing tests are the last three groups:

  * a **Bash permission gate** — the likeliest thing to stop a worktree agent — is
    invisible to the hook log and lives **only on the pane**, so the probe is the log OR
    the pane (:func:`~chela.telegram.reconcile.blocked_on_human`). Probe with the log alone
    and such an agent is never bound, its pane is never scraped (the gate watcher polls
    BOUND windows only), and it blocks forever, silently;
  * a run that has SETTLED but whose window is not reaped yet is still dispatcher-owned,
    or the reconcile creates it a topic that the reap archives seconds later;
  * and the real ``chela.main._reconcile_loop``, because the tests that drive
    ``reconcile_bindings`` with hand-passed stubs would all stay green with the feature
    deleted from production.
"""
from __future__ import annotations

import os

from chela.telegram.bindings import BindingRegistry
from chela.telegram.hookgate import pending_gate
from chela.telegram.reconcile import (
    TopicClosedHandler,
    TopicManager,
    ai_title_for_window,
    blocked_on_human,
    disambiguate_topic_names,
    dispatched_window_ids,
    reconcile_bindings,
    sync_pinned_titles,
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
# pin_title (TopicManager) — edit-in-place, fresh-pin fallback
# --------------------------------------------------------------------------

def test_pin_title_with_no_prior_message_sends_then_pins():
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 5001}}
        return {"ok": True, "result": True}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.pin_title(42, "Fix the flaky login test") == "5001"
    assert [m for m, _ in calls] == ["sendMessage", "pinChatMessage"]
    send_fields = calls[0][1]
    assert send_fields["message_thread_id"] == 42
    assert send_fields["text"] == "Fix the flaky login test"
    assert calls[1][1]["message_id"] == "5001"


def test_pin_title_with_existing_message_edits_in_place_no_send_no_pin():
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        return {"ok": True, "result": True}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.pin_title(42, "Now fixing auth", existing_message_id="5001") == "5001"
    assert calls == [
        ("editMessageText", {"chat_id": "777", "message_id": "5001", "text": "Now fixing auth"}),
    ]


def test_pin_title_falls_back_to_fresh_pin_when_edit_fails():
    # The tracked message was deleted (or the topic itself is gone) — editMessageText
    # fails, so pin_title must not just give up: it posts a fresh anchor and pins it.
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        if method == "editMessageText":
            return {"ok": False, "description": "Bad Request: message to edit not found"}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 5002}}
        return {"ok": True, "result": True}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.pin_title(42, "Still fixing auth", existing_message_id="stale") == "5002"
    assert [m for m, _ in calls] == ["editMessageText", "sendMessage", "pinChatMessage"]


def test_pin_title_treats_not_modified_edit_as_success():
    def transport(method, fields):
        assert method == "editMessageText"
        return {"ok": False, "description": "Bad Request: message is not modified"}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.pin_title(42, "same text", existing_message_id="5001") == "5001"


def test_pin_title_returns_none_when_the_fresh_send_itself_fails():
    def transport(method, fields):
        if method == "sendMessage":
            return {"ok": False, "description": "chat not found"}
        raise AssertionError("pinChatMessage must not be called without a message to pin")

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.pin_title(42, "title") is None


def test_pin_title_still_returns_the_id_when_pin_permission_is_missing():
    # A send that succeeds but whose pin the bot lacks rights for still leaves an
    # editable anchor message — degrading to "unpinned but current" beats re-posting
    # a brand-new message (and re-failing to pin it) every single tick forever.
    calls: list[tuple[str, dict]] = []

    def transport(method, fields):
        calls.append((method, fields))
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 5003}}
        if method == "pinChatMessage":
            return {"ok": False, "description": "not enough rights to pin a message"}
        return {"ok": True, "result": True}

    mgr = TopicManager("tok", "777", transport=transport)
    assert mgr.pin_title(42, "title") == "5003"


# --------------------------------------------------------------------------
# sync_pinned_titles — the edge-triggered per-tick pass over bound topics
# --------------------------------------------------------------------------

class _StubTopicApiWithPins(_StubTopicApi):
    """Adds pin_title recording on top of the create/close/rename stub above."""

    def __init__(self, *args, pin_ok=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._pin_ok = pin_ok
        self.pinned: list[tuple[str, str, str | None]] = []  # (thread, title, existing_id)

    def pin_title(self, thread_id, title, existing_message_id=None):
        self.pinned.append((str(thread_id), title, existing_message_id))
        return None if not self._pin_ok else "9999"


def test_sync_pinned_titles_skips_an_unbound_window():
    reg = BindingRegistry("777")  # @3 never bound — no topic to pin into
    api = _StubTopicApiWithPins()
    assert sync_pinned_titles(reg, api, {"@3"}, lambda wid: "some title") is False
    assert api.pinned == []


def test_sync_pinned_titles_skips_a_window_with_no_title_yet():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    api = _StubTopicApiWithPins()
    assert sync_pinned_titles(reg, api, {"@3"}, lambda wid: None) is False
    assert api.pinned == []


def test_sync_pinned_titles_pins_a_fresh_title_and_caches_it():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    api = _StubTopicApiWithPins()
    changed = sync_pinned_titles(reg, api, {"@3"}, lambda wid: "Fix the flaky login test")
    assert changed is True
    assert api.pinned == [("42", "Fix the flaky login test", None)]
    assert reg.pinned_title("@3") == "Fix the flaky login test"
    assert reg.pinned_message_id("@3") == "9999"


def test_sync_pinned_titles_is_silent_once_the_title_is_current():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    api = _StubTopicApiWithPins()
    assert sync_pinned_titles(reg, api, {"@3"}, lambda wid: "same title") is True
    assert sync_pinned_titles(reg, api, {"@3"}, lambda wid: "same title") is False
    assert len(api.pinned) == 1                 # the second tick made zero API calls


def test_sync_pinned_titles_edits_the_cached_message_on_a_revision():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.set_pinned_title("@3", "old title")
    reg.set_pinned_message_id("@3", "1234")
    api = _StubTopicApiWithPins()
    changed = sync_pinned_titles(reg, api, {"@3"}, lambda wid: "new title")
    assert changed is True
    assert api.pinned == [("42", "new title", "1234")]  # passed the prior id through
    assert reg.pinned_title("@3") == "new title"


def test_sync_pinned_titles_leaves_the_cache_untouched_on_a_hard_pin_failure():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    api = _StubTopicApiWithPins(pin_ok=False)
    changed = sync_pinned_titles(reg, api, {"@3"}, lambda wid: "a title")
    assert changed is False
    assert reg.pinned_title("@3") is None        # retried next tick, not marked done


def test_sync_pinned_titles_survives_a_probe_that_raises_for_one_window():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.bind("@5", "43")
    api = _StubTopicApiWithPins()

    def flaky(wid):
        if wid == "@3":
            raise RuntimeError("transcript read failed")
        return "fine"

    changed = sync_pinned_titles(reg, api, {"@3", "@5"}, flaky)
    assert changed is True
    assert api.pinned == [("43", "fine", None)]   # @3 skipped, @5 still got pinned


def test_ai_title_for_window_resolves_via_session_id_not_cwd(monkeypatch):
    from pathlib import Path

    from chela import sessions
    from chela import transcripts as transcripts_mod

    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: Path("/fake/t.jsonl"))
    monkeypatch.setattr(transcripts_mod, "latest_ai_title", lambda path: "Fix the flaky login test")
    assert ai_title_for_window("@3") == "Fix the flaky login test"


def test_ai_title_for_window_is_none_with_no_resolvable_transcript(monkeypatch):
    from chela import sessions

    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: None)
    assert ai_title_for_window("@3") is None


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
# disambiguate_topic_names — same-cwd generic-name windows must not collide
# (CMX-147: two windows in one cwd both fell back to the same project
# basename, so their topics were indistinguishable in the Telegram topic list)
# --------------------------------------------------------------------------

def test_disambiguate_leaves_unique_names_untouched():
    assert disambiguate_topic_names({"@3": "chelamux", "@4": "nautilus"}) == {
        "@3": "chelamux", "@4": "nautilus",
    }


def test_disambiguate_suffixes_every_colliding_window_with_its_id():
    # BOTH windows get the suffix — neither reads as the "plain", canonical one.
    got = disambiguate_topic_names({"@3": "chelamux", "@7": "chelamux"})
    assert got == {"@3": "chelamux (@3)", "@7": "chelamux (@7)"}


def test_disambiguate_only_touches_the_names_that_actually_collide():
    got = disambiguate_topic_names(
        {"@3": "chelamux", "@7": "chelamux", "@9": "nautilus"}
    )
    assert got == {
        "@3": "chelamux (@3)", "@7": "chelamux (@7)", "@9": "nautilus",
    }


def test_reconcile_disambiguates_two_windows_sharing_a_cwd_and_generic_name():
    # Two windows in the SAME project, both with tmux's command-follow name
    # ("claude") — topic_name_for falls both back to "chelamux" alone.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42", "43"])
    cwds = {"@3": "/home/liav/projects/chelamux", "@7": "/home/liav/projects/chelamux"}
    changed = reconcile_bindings(
        reg, {"@3": "claude", "@7": "claude"}, ["@3", "@7"], api, cwd_for=cwds.get
    )
    assert changed is True
    assert sorted(api.created) == ["chelamux (@3)", "chelamux (@7)"]
    assert reg.topic_name("@3") == "chelamux (@3)"
    assert reg.topic_name("@7") == "chelamux (@7)"


def test_reconcile_disambiguates_retroactively_when_a_sibling_appears():
    # @3 is already bound with the plain name from a tick where it was alone.
    # Once @7 shows up in the same cwd, @3's topic must be RENAMED to add the
    # suffix too — it must not keep looking like the sole/canonical one.
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.set_topic_name("@3", "chelamux")
    api = _StubTopicApi(threads=["43"])
    cwds = {"@3": "/home/liav/projects/chelamux", "@7": "/home/liav/projects/chelamux"}
    changed = reconcile_bindings(
        reg, {"@3": "claude", "@7": "claude"}, ["@3", "@7"], api, cwd_for=cwds.get
    )
    assert changed is True
    assert api.created == ["chelamux (@7)"]
    assert api.renamed == [("42", "chelamux (@3)")]
    assert reg.topic_name("@3") == "chelamux (@3)"


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


def test_a_dispatched_window_with_NO_gate_probe_stays_unbound():
    # Fail CLOSED. A caller that forgets the probe must not silently get bind-everything
    # back — that would be the churn returning by accident, with the toggle still off.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    changed = reconcile_bindings(reg, {"@9": "cmx-73"}, {"@9"}, api, dispatched={"@9"})
    assert changed is False
    assert api.created == []


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
    # With no live fleet to corroborate against, only the in-flight rows qualify.
    assert dispatched_window_ids(runs) == {"@9", "@10"}


# --------------------------------------------------------------------------
# A PERMISSION gate is on the PANE ONLY — the hook log cannot see it (D1)
# --------------------------------------------------------------------------
#
# The premise this feature was originally built on ("pending_gate answers this") is FALSE
# for the likeliest gate a worktree agent hits: a non-allowlisted Bash/Edit. The log says
# nothing; the pane says everything. If the probe cannot see it, the agent is never bound,
# its pane is therefore never scraped by the gate watcher (which polls BOUND windows only),
# and it blocks FOREVER, silently. These tests pin that the probe sees it.

# A real Bash permission gate, as Claude Code draws it (copied from the panescan fixtures).
BASH_GATE_PANE = """\
 Bash command
   rm -rf build/
   Remove the build directory

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands in this project
   3. No, and tell Claude what to do differently (esc)

 Esc to cancel
"""

# A perfectly ordinary working pane — nothing is blocked, nobody should be told anything.
WORKING_PANE = """\
✻ Cerebrating… (2m 45s · ↓ 12.0k tokens)

> try harder
"""


def _bash_gate_log(wid="@9"):
    """The event log of an agent BLOCKED on a Bash permission gate.

    A ``pre_tool_use`` for ``Bash`` with no ``post_tool_use`` — i.e. the tool call is
    genuinely still pending. This is what the log *has*, and it is not enough.
    """
    batch = {
        "boot_id": "b1",
        "events": [{
            "type": "hook.pre_tool_use",
            "boot_id": "b1",
            "seq": 1,
            "payload": {
                "tool_name": "Bash",
                "tool_use_id": "toolu_01",
                "tool_input": {"command": "rm -rf build/"},
            },
        }],
    }
    return lambda **_kwargs: batch


def test_pending_gate_is_blind_to_a_bash_permission_gate():
    # THE PREMISE, MEASURED. pending_gate only reports INTERACTIVE_TOOLS
    # (AskUserQuestion / ExitPlanMode), so a blocked Bash is None to it — which is why
    # it must NEVER be the sole gate probe. This test is the guard on that claim: if
    # Bash ever joins INTERACTIVE_TOOLS, this goes red and the reasoning gets re-read.
    assert pending_gate("@9", read=_bash_gate_log()) is None


def test_blocked_on_human_sees_the_bash_gate_that_the_log_cannot():
    # The pane half, doing the entire job the log cannot do.
    probe = blocked_on_human(
        "@9",
        gate=lambda wid: pending_gate(wid, read=_bash_gate_log()),
        capture=lambda wid: BASH_GATE_PANE,
    )
    assert probe is not None


def test_blocked_on_human_says_no_for_an_agent_that_is_just_working():
    # The other half of the contract: a working agent must NOT earn a topic, or the
    # feature is just "bind everything" wearing a pane capture.
    probe = blocked_on_human(
        "@9",
        gate=lambda wid: None,
        capture=lambda wid: WORKING_PANE,
    )
    assert probe is None


def test_a_bash_permission_gate_binds_a_dispatched_window():
    # 🔴 THE TEST. A dispatched worker hits a non-allowlisted `rm -rf build/`, the gate is
    # on the pane and NOWHERE else, and it must reach a human. Pre-fix this was silently
    # stuck forever: no topic, no pane poll, no notification, no agent.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    panes = {"@9": WORKING_PANE}

    def probe(wid):                     # the production probe, wired to stub sources
        return blocked_on_human(
            wid,
            gate=lambda w: pending_gate(w, read=_bash_gate_log()),
            capture=panes.get,
        )

    changed = reconcile_bindings(reg, {"@9": "cmx-73"}, {"@9"}, api,
                                 dispatched={"@9"}, gate_for=probe)
    assert changed is False and api.created == []       # still working — no topic

    panes["@9"] = BASH_GATE_PANE                        # ...and now it is BLOCKED on Bash
    changed = reconcile_bindings(reg, {"@9": "cmx-73"}, {"@9"}, api,
                                 dispatched={"@9"}, gate_for=probe)
    assert changed is True
    assert api.created == ["cmx-73"]
    assert reg.thread_for_window("@9") == "42"


def test_an_unreadable_event_log_does_not_cost_us_the_pane_probe():
    # The second door: pending_gate is boot_id-scoped, so a gate raised before a daemon
    # restart — or by an agent with no hooks plugin at all — is invisible in the log. For
    # an UNBOUND window there is no pane fallback unless this probe IS one. So a hook read
    # that fails (or returns nothing) must still let the pane speak.
    def boom(wid):
        raise RuntimeError("event log unreadable")

    assert blocked_on_human("@9", gate=boom, capture=lambda w: BASH_GATE_PANE) is not None


def test_a_failing_pane_capture_does_not_cost_us_the_hook_gate():
    # ...and symmetrically: a tmux hiccup must not swallow a gate the log DID see.
    def boom(wid):
        raise RuntimeError("tmux is gone")

    gate = object()
    assert blocked_on_human("@9", gate=lambda w: gate, capture=boom) is gate


def test_blocked_on_human_returns_none_when_both_sources_fail():
    def boom(wid):
        raise RuntimeError("nope")

    assert blocked_on_human("@9", gate=boom, capture=boom) is None


# --------------------------------------------------------------------------
# The awaiting_review gap (D3) — a settled run whose window has not been reaped
# --------------------------------------------------------------------------

def test_a_just_settled_run_whose_window_still_lives_is_still_dispatched():
    # dispatcher.mark_awaiting_review commits status='awaiting_review' and only THEN kills
    # the window. A reconcile tick landing in that gap used to see a live window that was
    # no longer "in flight", eagerly create it a topic, and the reap would archive it
    # seconds later — the exact churn this feature exists to kill, through the back door.
    runs = [{"status": "awaiting_review", "window_id": "@7", "window_name": "cmx-13"}]
    live = {"@7": "cmx-13"}                     # not reaped yet
    assert dispatched_window_ids(runs, live_windows=live) == {"@7"}


def test_a_settled_run_whose_id_tmux_recycled_is_NOT_dispatched():
    # The safety rail on the rule above. tmux hands out @N afresh after a server restart,
    # so a finished run's recorded id can be a HUMAN's window in this boot. Honouring it
    # blindly would silently strip the orchestrator of its topic. A recycled id wears a
    # different NAME — the row's own recorded one is the proof, and it fails here.
    runs = [{"status": "done", "window_id": "@6", "window_name": "cmx-12"}]
    live = {"@6": "orchestrator"}               # @6 is Liav's window now
    assert dispatched_window_ids(runs, live_windows=live) == set()


# --------------------------------------------------------------------------
# The judge's OWN window (CMX-97) — `_spawn_judge` launches with `judge_window=True`,
# so it must be found through `judge_window_id`, never `window_id`.
# --------------------------------------------------------------------------

def test_a_running_judge_window_is_dispatched_even_though_window_id_is_someone_elses():
    from chela import judge as judge_mod

    runs = [{
        "task_id": "abc123", "status": "awaiting_review", "branch_name": "cmx-9",
        "window_id": "@1", "window_name": "cmx-9",             # the RUN's own window
        "judge_window_id": "@42", "judge_state": judge_mod.J_RUNNING,
    }]
    # @1 (the run's own window) is not "in flight" (status is awaiting_review) and is not
    # live at all here — only the judge's @42 should come back dispatched.
    assert dispatched_window_ids(runs) == {"@42"}


def test_a_just_finished_judge_whose_window_is_not_reaped_yet_is_still_dispatched():
    from chela import judge as judge_mod

    # judge.judge_run writes the verdict, THEN judge._cleanup kills the window last —
    # so a tick landing in that gap must still see the judge as dispatcher-owned.
    runs = [{"task_id": "abc123", "status": "awaiting_review", "branch_name": "cmx-9",
             "judge_window_id": "@42", "judge_state": judge_mod.J_CLEAN}]
    live = {"@42": judge_mod.judge_window_name("cmx-9")}
    assert dispatched_window_ids(runs, live_windows=live) == {"@42"}


def test_a_judge_id_tmux_recycled_to_a_human_is_NOT_dispatched():
    from chela import judge as judge_mod

    runs = [{"task_id": "abc123", "status": "awaiting_review", "branch_name": "cmx-9",
             "judge_window_id": "@42", "judge_state": judge_mod.J_CLEAN}]
    live = {"@42": "orchestrator"}                  # @42 is Liav's window now
    assert dispatched_window_ids(runs, live_windows=live) == set()


def test_a_dead_servers_judge_window_cannot_disown_a_humans_window():
    from chela import epoch, judge as judge_mod

    OLD, NEW = "old-epoch", "new-epoch"
    runs = [{"task_id": "abc123", "status": "awaiting_review", "branch_name": "cmx-9",
             "judge_window_id": "@42", "judge_window_epoch": OLD,
             "judge_state": judge_mod.J_RUNNING}]
    live = {"@42": "orchestrator"}

    assert dispatched_window_ids(runs, live_windows=live, now_epoch=NEW) == set()
    assert dispatched_window_ids(runs, live_windows=live, now_epoch=OLD) == {"@42"}
    assert epoch.is_dangling(OLD, NEW)               # sanity: the fixture actually differs


def test_a_lingering_failed_run_does_not_churn_a_topic():
    # End to end through the reconcile: the window of a failed run is still alive and
    # quiet, so it gets NO topic — where before it would have been created and archived.
    reg = BindingRegistry("777")
    api = _StubTopicApi(threads=["42"])
    runs = [{"status": "failed", "window_id": "@9", "window_name": "cmx-73"}]
    live = {"@9": "cmx-73"}
    changed = reconcile_bindings(
        reg, live, {"@9"}, api,
        dispatched=dispatched_window_ids(runs, live_windows=live),
        gate_for=lambda wid: None,
    )
    assert changed is False
    assert api.created == []


# --------------------------------------------------------------------------
# The PRODUCTION wiring (D2) — the loop that actually runs, and the toggle
# --------------------------------------------------------------------------
#
# Everything above drives reconcile_bindings() with hand-passed stubs, which tests the
# artifact we WROTE, not the one that RUNS. The whole feature could be deleted from
# `_reconcile_loop` and every test above would stay green. So these drive the real loop.

class _OneTick:
    """A stop event that lets exactly one reconcile tick run, then stops the loop."""

    def __init__(self):
        self.checks = 0
        self.waited: list[int] = []

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > 1

    def wait(self, interval):
        self.waited.append(interval)


def _run_one_tick(monkeypatch, *, live=None, dispatched=None, reconcile_returns=False,
                  roster_raises=None, cwds=None):
    """Run ONE tick of the real ``chela.main._reconcile_loop``; return its kwargs."""
    from chela import discovery, main, roster
    from chela import telegram as tg

    live = {"@9": "cmx-73", "@6": "orchestrator"} if live is None else live
    seen: dict = {}

    def _fake_reconcile(registry, live_windows, agent_ids, topic_api, **kwargs):
        seen["args"] = (registry, live_windows, agent_ids, topic_api)
        seen["kwargs"] = kwargs
        return reconcile_returns

    def _fake_dispatched(runs=None, live_windows=None, now_epoch=None):
        seen["dispatched_call"] = {"runs": runs, "live_windows": live_windows,
                                   "now_epoch": now_epoch}
        return {"@9"} if dispatched is None else dispatched

    def _fake_sync_pinned_titles(registry, topic_api, agent_ids, ai_title_for):
        seen["pinned_call"] = {
            "registry": registry, "topic_api": topic_api,
            "agent_ids": agent_ids, "ai_title_for": ai_title_for,
        }
        return False

    # CMX-195: the tick now also writes the durable fleet snapshot. Capture it instead of
    # letting the real one run — otherwise every test in this section writes a roster into
    # the operator's live CHELA_DIR as a side effect of driving the loop.
    def _fake_record(live_windows, agent_ids, now_epoch, cwd_for, *a, **kw):
        seen["roster_call"] = {"live": live_windows, "agents": agent_ids,
                               "now_epoch": now_epoch, "cwd_for": cwd_for}
        if roster_raises is not None:
            raise roster_raises
        return None

    monkeypatch.setattr(roster, "record", _fake_record)
    monkeypatch.setattr(tg, "live_agent_windows", lambda: (live, set(live)))
    monkeypatch.setattr(tg, "dispatched_window_ids", _fake_dispatched)
    monkeypatch.setattr(tg, "reconcile_bindings", _fake_reconcile)
    monkeypatch.setattr(tg, "sync_pinned_titles", _fake_sync_pinned_titles)
    monkeypatch.setattr(discovery, "get_window_cwd_by_id",
                        lambda wid: (cwds or {}).get(wid))

    stop = _OneTick()
    main._reconcile_loop(BindingRegistry("777"), _StubTopicApi(), 7, stop)
    seen["stop"] = stop
    return seen


def test_the_reconcile_loop_actually_wires_the_dispatched_feature(monkeypatch):
    # Revert the wiring in main.py and this goes RED — which is the point. Every other
    # test in this section passes with the feature deleted from production.
    from chela import telegram as tg

    seen = _run_one_tick(monkeypatch)
    kwargs = seen["kwargs"]
    assert kwargs["dispatched"] == {"@9"}
    assert kwargs["gate_for"] is tg.blocked_on_human   # ...and NOT pending_gate alone (D1)
    assert kwargs["bind_dispatched"] is False
    assert seen["stop"].waited == [7]


def test_the_reconcile_loop_actually_writes_the_roster_snapshot(monkeypatch):
    """🔴 GUARD (CMX-195): objective 1 is the WRITE, not the module.

    ``chela/roster.py`` is exhaustively tested as a pure function, and every one of those
    tests passes with the call deleted from ``_reconcile_loop`` — at which point no roster
    is ever written on this box and ``chela restore`` can never answer the question the
    ticket exists for ("what did the dead server have?"). The judge proved exactly that by
    replacing the call site with ``pass`` and watching 2058 tests stay green.

    The tick is the ONLY writer, so this is the only place the invariant can be observed.
    """
    seen = _run_one_tick(monkeypatch, cwds={"@9": "/home/liav/projects/thing"})
    call = seen.get("roster_call")
    assert call is not None, "the reconcile tick must call roster.record — objective 1"
    # ...and with the fleet it already has in hand, not a re-derived one.
    assert call["live"] == {"@9": "cmx-73", "@6": "orchestrator"}
    assert call["agents"] == {"@9", "@6"}
    # ⛔ NOT `is not None` — that is what round 6 asserted, and `lambda wid: None` IS not
    # None, so blanking the resolver at the call site was invisible. `cwd` is half of what
    # the roster preserves: without it every MANUAL row degrades from the exact
    # `cd <cwd> && CHELA_WID=@N claude --resume <sid>` one-liner to "(no cwd/session on
    # record)", which is objective 2's entire operator payload. Assert the VALUE.
    assert call["cwd_for"]("@9") == "/home/liav/projects/thing", (
        "the tick must hand roster.record the REAL cwd resolver — a blanked one still "
        "satisfies an is-not-None check while erasing every relaunch command"
    )


def test_the_roster_snapshot_is_stamped_with_the_epoch_the_tick_read(monkeypatch):
    """🔴 GUARD (CMX-195): a snapshot keyed on the WRONG epoch is worse than none.

    ``record`` returns None and writes nothing on a falsy epoch, so passing the tick's
    ``now_epoch`` through is what makes the snapshot joinable later. Hand the loop a known
    epoch and assert it arrives.
    """
    from chela import epoch as epoch_mod

    monkeypatch.setattr(epoch_mod, "current", lambda: "999-1785358190")
    seen = _run_one_tick(monkeypatch)
    assert seen["roster_call"]["now_epoch"] == "999-1785358190"


def test_a_failing_roster_write_does_not_take_down_the_reconcile_tick(monkeypatch):
    """🔴 GUARD (CMX-195): the roster is a PASSENGER on this tick, never its driver.

    Bindings reaping, the dispatched probe and pinned-title sync all ride behind the roster
    write. A full disk or a bad-permissions `roster.json` must cost the snapshot and nothing
    else — narrow that `except Exception` (the judge narrowed it to `ValueError`) and any
    other failure propagates out, silently killing the whole tick for the live
    `chela-telegram` daemon.

    ⚠️ The capture stub in `_run_one_tick` never raises, which is precisely why this cut
    survived round 4. Make the guarded call actually fail, then assert the tick got PAST it.
    """
    seen = _run_one_tick(monkeypatch, roster_raises=OSError("no space left on device"))

    assert seen.get("roster_call") is not None, "the roster write must have been attempted"
    assert "kwargs" in seen, (
        "the tick must reach reconcile_bindings even when the roster write blew up — "
        "the snapshot is best-effort, the reconcile is not"
    )


def test_the_reconcile_loop_hands_the_live_fleet_to_the_dispatched_probe(monkeypatch):
    # D3's corroboration needs the live fleet, so the loop must actually pass it.
    seen = _run_one_tick(monkeypatch)
    assert seen["dispatched_call"]["live_windows"] == {"@9": "cmx-73", "@6": "orchestrator"}


def test_the_reconcile_loop_honours_CHELA_TELEGRAM_BIND_DISPATCHED(monkeypatch):
    # The toggle is the escape hatch; if it does not reach the loop it is decoration.
    from chela import main

    monkeypatch.setattr(main, "BIND_DISPATCHED", True)
    seen = _run_one_tick(monkeypatch)
    assert seen["kwargs"]["bind_dispatched"] is True
    # ...and with bind-everything on, the runs table is not even consulted.
    assert seen["kwargs"]["dispatched"] == set()
    assert "dispatched_call" not in seen


def test_bind_dispatched_defaults_to_OFF(monkeypatch):
    # Pins the DEFAULT, not just the plumbing: inverting config.py's false→true must be a
    # red test, or "lazy binding" ships as "bind everything" and nobody notices.
    import importlib

    from chela import config

    monkeypatch.delenv("CHELA_TELEGRAM_BIND_DISPATCHED", raising=False)
    try:
        assert importlib.reload(config).BIND_DISPATCHED is False
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_bind_dispatched_env_var_turns_it_on(monkeypatch):
    import importlib

    from chela import config

    monkeypatch.setenv("CHELA_TELEGRAM_BIND_DISPATCHED", "true")
    try:
        assert importlib.reload(config).BIND_DISPATCHED is True
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_reconcile_loop_actually_wires_pinned_titles(monkeypatch):
    # Revert the wiring in main.py (delete the sync_pinned_titles call) and this goes RED.
    from chela import telegram as tg

    seen = _run_one_tick(monkeypatch)
    call = seen["pinned_call"]
    assert call["agent_ids"] == {"@9", "@6"}
    assert call["ai_title_for"] is tg.ai_title_for_window
    assert call["topic_api"] is not None


def test_the_reconcile_loop_still_syncs_pinned_titles_when_bindings_also_changed(monkeypatch):
    # THE guard this whole wiring exists to catch: `a or b` short-circuits, so chaining
    # sync_pinned_titles onto reconcile_bindings with `or` would skip it on any tick that
    # also provisioned/reaped/renamed a topic. Both must run on EVERY tick.
    seen = _run_one_tick(monkeypatch, reconcile_returns=True)
    assert "pinned_call" in seen
