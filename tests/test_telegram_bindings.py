"""BindingRegistry — the pure thread↔window map the multi-topic bridge routes on.

No Telegram: the registry is exercised directly to lock in

  * bidirectional lookup (thread→window and window→thread);
  * 1:1 replacement — rebinding either side drops the stale binding;
  * int↔str normalisation (wire ids are int, JSON keys are str);
  * General-topic / empty ids never bind and always look up as unbound;
  * a JSON save→load round-trip preserves bindings, with env chat_id winning.
"""
from __future__ import annotations

import json

from chela.telegram.bindings import BindingRegistry, default_bindings_path


def test_bind_is_bidirectional():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    assert reg.window_for_thread("42") == "@3"
    assert reg.thread_for_window("@3") == "42"
    assert reg.windows() == ["@3"]
    assert len(reg) == 1


def test_lookup_normalises_int_and_str_ids():
    # Wire thread ids arrive as ints; stored keys are str — lookup bridges both.
    reg = BindingRegistry("777")
    reg.bind("@3", 42)
    assert reg.window_for_thread(42) == "@3"
    assert reg.window_for_thread("42") == "@3"


def test_rebinding_a_window_replaces_its_thread():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.bind("@3", "99")
    assert reg.thread_for_window("@3") == "99"
    # The old thread must no longer resolve to the window.
    assert reg.window_for_thread("42") is None
    assert reg.window_for_thread("99") == "@3"


def test_rebinding_a_thread_replaces_its_window():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.bind("@7", "42")
    assert reg.window_for_thread("42") == "@7"
    # The old window must no longer resolve to the thread.
    assert reg.thread_for_window("@3") is None
    assert reg.windows() == ["@7"]


def test_unbind_removes_both_directions():
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    assert reg.unbind("@3") is True
    assert reg.window_for_thread("42") is None
    assert reg.thread_for_window("@3") is None
    assert reg.windows() == []
    # Unbinding an unknown window is a no-op.
    assert reg.unbind("@9") is False


def test_general_topic_and_empty_ids_never_bind_or_resolve():
    reg = BindingRegistry("777")
    # A forum's General topic reports no thread id — it can never bind.
    for bad in (None, ""):
        try:
            reg.bind("@3", bad)
        except ValueError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError(f"bind should reject thread {bad!r}")
    # And looking one up is always unbound.
    assert reg.window_for_thread(None) is None
    assert reg.window_for_thread("") is None
    assert reg.thread_for_window(None) is None


def test_missing_window_id_is_rejected_on_bind():
    reg = BindingRegistry("777")
    try:
        reg.bind("", "42")
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("bind should reject an empty window id")


def test_save_load_round_trip(tmp_path):
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    reg.bind("@7", "88")
    path = tmp_path / "bindings.json"
    reg.save(path)

    # Persisted shape is stable and JSON-clean. `topic_names` caches the name each
    # window's topic currently carries on Telegram (empty until a topic is named),
    # so a reconcile tick can spot a drifted topic without calling the Bot API for
    # every window on every tick. It is a cache, never a source of truth — the tmux
    # window name is.
    data = json.loads(path.read_text())
    # `epochs` stamps each bound `@N` with the tmux SERVER that issued it (CMX-77): the id
    # is an address, not an identity, and a binding that outlives its server would relay a
    # stranger's pane into the topic a human opened for a dead agent. Unstamped here — bind()
    # was given no epoch, which is exactly what a pre-CMX-77 file reads as.
    assert data == {"chat_id": "777",
                    "bindings": {"@3": "42", "@7": "88"},
                    "topic_names": {},
                    "epochs": {}}

    loaded = BindingRegistry.load(path)
    assert loaded.chat_id == "777"
    assert loaded.window_for_thread("42") == "@3"
    assert loaded.thread_for_window("@7") == "88"
    assert sorted(loaded.windows()) == ["@3", "@7"]


def test_load_missing_file_yields_empty_registry(tmp_path):
    reg = BindingRegistry.load(tmp_path / "nope.json", chat_id="555")
    assert reg.chat_id == "555"
    assert reg.windows() == []


def test_load_env_chat_id_overrides_persisted(tmp_path):
    path = tmp_path / "bindings.json"
    reg = BindingRegistry("111")
    reg.bind("@1", "2")
    reg.save(path)
    # The daemon feeds the live TELEGRAM_CHAT_ID so env stays the boundary.
    loaded = BindingRegistry.load(path, chat_id="999")
    assert loaded.chat_id == "999"
    assert loaded.window_for_thread("2") == "@1"


def test_load_corrupt_file_yields_empty_registry(tmp_path):
    path = tmp_path / "bindings.json"
    path.write_text("{ not valid json")
    reg = BindingRegistry.load(path, chat_id="333")
    assert reg.chat_id == "333"
    assert reg.windows() == []


def test_default_bindings_path_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CHELA_TELEGRAM_BINDINGS", str(tmp_path / "custom.json"))
    assert default_bindings_path() == tmp_path / "custom.json"


def test_load_tolerates_a_file_written_before_topic_names_existed(tmp_path):
    # Back-compat: bindings files predating the topic-name cache have no such key.
    # Those windows simply read as "never synced", so the next reconcile tick names
    # each topic once from the live tmux name — no migration step, no crash.
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps({"chat_id": "777", "bindings": {"@3": "42"}}))

    loaded = BindingRegistry.load(path)
    assert loaded.thread_for_window("@3") == "42"
    assert loaded.topic_name("@3") is None
