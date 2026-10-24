"""CMX-328 — the core log: chela's own events outlive the hook firehose.

The Decisions dialog read "No decisions logged yet" after a day of dozens of them: every
``hook.*`` event shared the one 8 MB file with the decisions, the file rolled every ~7 h,
and ``/api/log`` serves only the current file. Every non-hook record is now also written
to ``events-core.jsonl`` (same record, same ``seq``), and a read whose types are all
non-hook is served from there.
"""
from __future__ import annotations

import json

import pytest

from chela import event_log
from chela.dashboard import app as dash


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(path))
    return path


def _core(tmp_path):
    return tmp_path / "events-core.jsonl"


def _lines(path):
    return path.read_text().splitlines() if path.exists() else []


def _seqs(events):
    return [e["seq"] for e in events]


def _busy_day(n_decisions=6, hooks_between=30):
    """Decisions interleaved with enough hook traffic to roll the main log many times."""
    decisions = []
    for i in range(n_decisions):
        for _ in range(hooks_between):
            event_log.append("hook.pre_tool_use", "Bash: ls", {"pad": "p" * 200})
        decisions.append(event_log.append("run_review", f"cmx-{i} awaiting review",
                                          {"task_id": f"t{i}"})["seq"])
    return decisions


def test_the_core_log_path_is_a_sibling_of_the_log(log_file, tmp_path):
    assert event_log.core_path() == tmp_path / "events-core.jsonl"


@pytest.mark.parametrize("type_, core", [
    ("run_review", True), ("orchestrator.merge", True), ("daemon_start", True),
    ("hook.pre_tool_use", False), ("hook.stop", False), ("hooks", True), (None, True),
])
def test_is_core_is_everything_but_a_hook(type_, core):
    assert event_log.is_core(type_) is core


def test_a_decision_is_written_to_both_logs_byte_identical(log_file, tmp_path):
    rec = event_log.append("run_review", "cmx-1 awaiting review", {"task_id": "t1"})
    assert _lines(_core(tmp_path)) == _lines(log_file)
    assert json.loads(_lines(_core(tmp_path))[0])["seq"] == rec["seq"]


def test_a_hook_event_never_reaches_the_core_log(log_file, tmp_path):
    event_log.append("hook.pre_tool_use", "Bash: ls", {})
    assert len(_lines(log_file)) == 1
    assert not _core(tmp_path).exists()


def test_decisions_survive_the_main_log_rolling_several_times(log_file, monkeypatch):
    """THE bug: a log rolled several times still hands the dialog its old decisions."""
    monkeypatch.setattr(event_log, "MAX_BYTES", 4000)
    monkeypatch.setattr(event_log, "KEEP_ROTATIONS", 2)
    decisions = _busy_day()
    rolled_out = [s for s in decisions if s < event_log.read()["first_seq"]]
    assert len(rolled_out) >= 4                            # the old files really are gone
    assert not log_file.with_name("events.jsonl.3").exists()

    got = event_log.read(types=["run_review", "run_failed"])
    assert _seqs(got["events"]) == decisions
    assert got["gap"] is None
    assert got["first_seq"] == decisions[0]
    assert got["last_seq"] == decisions[-1]
    assert got["next_seq"] == decisions[-1]


def test_an_unfiltered_read_still_serves_the_current_file(monkeypatch):
    """No ``types`` (``chela events``, the hook readers): the ring, as before."""
    monkeypatch.setattr(event_log, "MAX_BYTES", 4000)
    monkeypatch.setattr(event_log, "KEEP_ROTATIONS", 2)
    decisions = _busy_day()
    got = event_log.read()
    assert decisions[0] not in _seqs(got["events"])
    assert any(e["type"].startswith("hook.") for e in got["events"])


def test_a_read_naming_any_hook_type_is_served_from_the_ring(monkeypatch):
    monkeypatch.setattr(event_log, "MAX_BYTES", 4000)
    monkeypatch.setattr(event_log, "KEEP_ROTATIONS", 2)
    decisions = _busy_day()
    got = event_log.read(types=["run_review", "hook.pre_tool_use"])
    assert decisions[0] not in _seqs(got["events"])
    assert any(e["type"] == "hook.pre_tool_use" for e in got["events"])


def test_the_api_serves_the_dialogs_query_across_rolls(monkeypatch):
    monkeypatch.setattr(event_log, "MAX_BYTES", 4000)
    monkeypatch.setattr(event_log, "KEEP_ROTATIONS", 2)
    decisions = _busy_day()
    data = dash.app.test_client().get(
        "/api/log?limit=300&type=run_review&type=run_needs_human").get_json()
    assert _seqs(data["events"]) == decisions
    unfiltered = dash.app.test_client().get("/api/log?limit=2000").get_json()
    assert decisions[0] not in _seqs(unfiltered["events"])


def test_a_cursor_resumes_in_the_core_log():
    decisions = _busy_day(n_decisions=3, hooks_between=2)
    first = event_log.read(types=["run_review"], limit=1)
    assert _seqs(first["events"]) == decisions[:1]
    rest = event_log.read(first["next_seq"], after_boot=first["boot_id"], types=["run_review"])
    assert _seqs(rest["events"]) == decisions[1:]
    assert rest["gap"] is None


def test_a_cursor_at_the_logs_tip_is_not_ahead_of_the_core_log():
    """A reader anchored at ``tip()`` (wait.py, a pre-upgrade dialog cursor) sits past the
    core log's last record — hooks came after it. That is not a reset seq space."""
    event_log.append("run_review", "a", {})
    event_log.append("hook.stop", "b", {})
    tip = event_log.tip()["seq"]
    got = event_log.read(tip, types=["run_review"])
    assert got["gap"] is None
    assert got["events"] == []
    later = event_log.append("run_review", "c", {})
    assert _seqs(event_log.read(tip, types=["run_review"])["events"]) == [later["seq"]]


def test_a_cursor_past_the_tip_is_still_a_gap():
    event_log.append("run_review", "a", {})
    got = event_log.read(99, types=["run_review"])
    assert got["gap"]["reason"].startswith("cursor is ahead of the log")
    assert _seqs(got["events"]) == [1]


def test_a_cursor_behind_the_core_logs_retention_is_a_gap(monkeypatch):
    event_log.seed_core()                  # the one-time seed is done already
    monkeypatch.setattr(event_log, "CORE_MAX_BYTES", 600)
    seqs = [event_log.append("run_review", f"r{i}", {"pad": "x" * 100})["seq"]
            for i in range(12)]
    got = event_log.read(1, types=["run_review"])
    assert got["gap"] is not None
    assert got["gap"]["reason"].startswith("events before this point")
    assert _seqs(got["events"]) == seqs[seqs.index(got["first_seq"]):]


def test_the_core_log_rolls_once_and_reads_both_generations(tmp_path, monkeypatch):
    event_log.seed_core()                  # the one-time seed is done already
    monkeypatch.setattr(event_log, "CORE_MAX_BYTES", 600)
    seqs = [event_log.append("run_review", f"r{i}", {"pad": "x" * 100})["seq"]
            for i in range(12)]
    core = _core(tmp_path)
    assert core.with_name("events-core.jsonl.1").exists()
    assert not core.with_name("events-core.jsonl.2").exists()      # ONE rolled generation
    held = _seqs(event_log.read(types=["run_review"])["events"])
    one = [json.loads(x)["seq"] for x in _lines(core.with_name("events-core.jsonl.1"))]
    assert held == seqs[-len(held):]
    assert held[:len(one)] == one                    # .1 is read, and read first
    # every main-log line is still there: the core bound does not touch the main log
    assert len(_lines(tmp_path / "events.jsonl")) == 12


def test_corrupt_core_lines_are_counted_from_the_core_log(tmp_path):
    event_log.append("run_review", "a", {})
    with open(_core(tmp_path), "a") as fh:
        fh.write("{torn\n")
    got = event_log.read(types=["run_review"])
    assert got["corrupt_lines"] == 1
    assert event_log.read()["corrupt_lines"] == 0               # the main log is clean


def test_the_core_read_is_cached_until_a_file_changes(tmp_path, monkeypatch):
    event_log.append("run_review", "a", {})
    event_log.read(types=["run_review"])
    calls = []
    real = event_log._parse
    monkeypatch.setattr(event_log, "_parse", lambda p: calls.append(p) or real(p))
    event_log.read(types=["run_review"])
    assert _core(tmp_path) not in calls
    event_log.append("run_review", "b", {})
    assert _seqs(event_log.read(types=["run_review"])["events"]) == [1, 2]
    assert _core(tmp_path) in calls


def test_a_core_write_failure_keeps_the_event(tmp_path, monkeypatch, caplog):
    """The main log has it: append reports the record, it does not return None."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr(event_log, "core_path", lambda: blocker / "events-core.jsonl")
    rec = event_log.append("run_review", "a", {})
    assert rec is not None and rec["seq"] == 1
    assert [json.loads(x)["seq"] for x in _lines(tmp_path / "events.jsonl")] == [1]
    assert "core append failed" in caplog.text


# --- seeding the core log from what the main log still holds --------------------

def _write_main(log_file, records):
    log_file.write_text("".join(json.dumps(r) + "\n" for r in records))


def _rec(seq, type_):
    return {"seq": seq, "boot_id": "b", "ts": float(seq), "type": type_, "payload": {}}


def test_seed_copies_the_rolled_decisions_once(log_file, tmp_path):
    log_file.with_name("events.jsonl.2").write_text(
        json.dumps(_rec(1, "run_review")) + "\n" + json.dumps(_rec(2, "hook.stop")) + "\n")
    log_file.with_name("events.jsonl.1").write_text(json.dumps(_rec(3, "run_failed")) + "\n")
    _write_main(log_file, [_rec(4, "hook.stop"), _rec(5, "orchestrator.merge")])
    assert event_log.seed_core() == 3
    assert [json.loads(x)["seq"] for x in _lines(_core(tmp_path))] == [1, 3, 5]
    assert _core(tmp_path).with_name("events-core.jsonl.seeded").exists()
    _write_main(log_file, [_rec(6, "run_review")])
    assert event_log.seed_core() == 0                              # once, by the marker
    assert [json.loads(x)["seq"] for x in _lines(_core(tmp_path))] == [1, 3, 5]


def test_seed_merges_with_what_the_core_log_already_has(log_file, tmp_path):
    _write_main(log_file, [_rec(1, "run_review"), _rec(2, "run_failed")])
    core = _core(tmp_path)
    core.with_name("events-core.jsonl.1").write_text(json.dumps(_rec(0, "daemon_start")) + "\n")
    core.write_text(json.dumps(_rec(2, "run_failed")) + "\n")      # appended before seeding
    assert event_log.seed_core() == 1
    assert [json.loads(x)["seq"] for x in _lines(core)] == [0, 1, 2]
    assert not core.with_name("events-core.jsonl.1").exists()


def test_seed_with_nothing_to_add_still_marks_and_writes_nothing(log_file, tmp_path):
    _write_main(log_file, [_rec(1, "hook.stop")])
    assert event_log.seed_core() == 0
    assert not _core(tmp_path).exists()
    assert _core(tmp_path).with_name("events-core.jsonl.seeded").exists()


def test_seed_failure_is_logged_not_raised(log_file, tmp_path, monkeypatch, caplog):
    _write_main(log_file, [_rec(1, "run_review")])
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr(event_log, "core_path", lambda: blocker / "events-core.jsonl")
    assert event_log.seed_core() == 0
    assert "seeding the core log failed" in caplog.text


def test_new_boot_seeds_the_core_log(log_file, tmp_path):
    _write_main(log_file, [_rec(1, "run_review"), _rec(2, "hook.stop")])
    event_log.new_boot()
    assert [json.loads(x)["seq"] for x in _lines(_core(tmp_path))] == [1]


def test_rotate_retires_the_core_log_too(log_file, tmp_path):
    event_log.append("run_review", "a", {})
    event_log.rotate()
    assert not _core(tmp_path).exists()
    assert list(tmp_path.glob("events-core.jsonl.bak.*"))
    assert event_log.read(types=["run_review"])["events"] == []


def test_the_first_core_read_seeds_before_the_daemon_restarts(log_file):
    """A dashboard or CLI on the new code reads the old decisions straight away."""
    _write_main(log_file, [_rec(1, "orchestrator.merge"), _rec(2, "hook.stop")])
    got = event_log.read(types=["orchestrator.merge"])
    assert _seqs(got["events"]) == [1]


def test_an_unfiltered_read_does_not_seed(log_file, tmp_path):
    _write_main(log_file, [_rec(1, "run_review")])
    event_log.read()
    assert not _core(tmp_path).with_name("events-core.jsonl.seeded").exists()


def test_a_core_read_never_hands_back_a_cursor_behind_the_callers():
    event_log.append("run_review", "a", {})
    event_log.append("hook.stop", "b", {})
    event_log.append("hook.stop", "c", {})
    assert event_log.read(3, types=["run_review"])["next_seq"] == 3
    assert event_log.read(None, types=["run_review"])["next_seq"] == 1
    assert event_log.read(None)["next_seq"] == 3          # the ring: its own position


def test_a_failed_seed_is_not_retried_on_every_read(log_file, tmp_path, monkeypatch):
    _write_main(log_file, [_rec(1, "run_review")])
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr(event_log, "core_path", lambda: blocker / "events-core.jsonl")
    monkeypatch.setattr(event_log, "_seed_failed", set())
    event_log.read(types=["run_review"])
    calls = []
    monkeypatch.setattr(event_log, "_append_lock", lambda: calls.append(1) or (_ for _ in ()))
    assert event_log.seed_core() == 0
    assert calls == []


def test_the_hook_prefix_is_the_hooks_own():
    from chela import hooks
    assert event_log.HOOK_PREFIX == hooks.TYPE_PREFIX == "hook."
