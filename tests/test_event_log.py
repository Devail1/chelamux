"""Event log — the durable, ordered record an event can live in.

The contract these lock in, in the order the log's usefulness depends on it:

  * a record, not prose — ``type`` + one-line ``summary`` + structured ``payload``,
    the SAME shape the inbox already queues (``kind`` maps straight onto ``type``);
  * ``seq`` is monotonic, has no duplicates under N concurrent writer PROCESSES, and
    survives a restart — a cursor is worthless otherwise;
  * a cursor that CANNOT be honoured is reported as a gap, never silently resumed
    across (a boot change, a reset seq space, events no longer served);
  * a torn final line — a crash mid-append — is skipped, not fatal.

Fully programmatic: no tmux, no Claude Code, no daemon. That is the point of building
the store before the hooks that will feed it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chela import event_log


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    """Never the real ``~/.chela/events.jsonl``.

    The read cache is keyed by the file's PATH (plus size/mtime), so pointing the env at
    a fresh tmp file per test also invalidates it — no module state to reset by hand.
    """
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(path))
    return path


def _seqs(events: list[dict]) -> list[int]:
    return [e["seq"] for e in events]


# --- the record ------------------------------------------------------------------

def test_append_stores_a_record_not_a_sentence(log_file):
    rec = event_log.append("run_review", "cmx-40 awaiting review — PR #52",
                           {"task_id": "abc123", "pr_url": "https://example.test/pull/52"},
                           wid="@3", session_id="sess-1")

    assert rec["seq"] == 1
    assert rec["boot_id"]
    assert rec["ts"] > 0
    assert rec["type"] == "run_review"
    assert rec["wid"] == "@3"
    assert rec["session_id"] == "sess-1"
    assert rec["summary"] == "cmx-40 awaiting review — PR #52"
    assert rec["payload"]["task_id"] == "abc123"

    # And it is on disk as ONE json object on ONE line — the audit trail.
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == rec


def test_summary_is_collapsed_to_one_line():
    """A notification is a line. The essay goes in the payload — that is the whole split."""
    rec = event_log.append("finished", "did\nthe   work\n\nand more", {"title": "x\ny"})
    assert "\n" not in rec["summary"]
    assert rec["summary"] == "did the work and more"
    assert rec["payload"]["title"] == "x\ny"          # the payload is NOT collapsed


def test_from_inbox_maps_the_inbox_record_onto_the_log(monkeypatch):
    """One event schema. The inbox's `kind` IS the log's `type` — no second shape."""
    from chela import inbox

    event = inbox._event("died", "📥 @6 (cmx-33) DIED mid-task",
                         {"wid": "@6", "window_name": "cmx-33"}, wid="@6")
    rec = event_log.from_inbox(event)

    assert rec["type"] == event["kind"] == "died"
    assert rec["summary"] == event["summary"]
    assert rec["payload"] == event["payload"]
    assert rec["wid"] == "@6"


def test_append_never_raises_on_an_unserialisable_payload():
    """A hook calls this INSIDE a live agent. It may lose an event; it may not raise."""
    rec = event_log.append("weird", "odd payload", {"obj": object()})
    assert rec is not None and rec["seq"] == 1
    stored = event_log.read()["events"][0]
    assert isinstance(stored["payload"]["obj"], str)   # default=str, not an exception


# --- seq ---------------------------------------------------------------------------

def test_seq_is_monotonic():
    for i in range(1, 6):
        assert event_log.append("t", f"e{i}")["seq"] == i
    assert _seqs(event_log.read()["events"]) == [1, 2, 3, 4, 5]


def test_seq_survives_a_restart(log_file):
    """`seq` is derived from durable state, never from a process-local counter."""
    event_log.append("t", "before")
    event_log.append("t", "before")

    # A "restart" is just a new process reading the same files. Simulate the strongest
    # form: the sidecar is GONE, so seq has to be recovered from the log itself.
    (log_file.parent / (log_file.name + ".state")).unlink()

    rec = event_log.append("t", "after")
    assert rec["seq"] == 3                             # no duplicate, no reset to 1
    assert _seqs(event_log.read()["events"]) == [1, 2, 3]


def test_a_lost_sidecar_mints_a_new_boot_id(log_file):
    """A re-derived seq space is exactly when an old cursor stops meaning what it meant."""
    first = event_log.append("t", "a")["boot_id"]
    (log_file.parent / (log_file.name + ".state")).unlink()
    assert event_log.append("t", "b")["boot_id"] != first


def test_new_boot_changes_the_epoch_but_not_the_seq():
    """A restart does not renumber the log — two events must never share an identity."""
    event_log.append("t", "a")
    before = event_log.current_boot()

    after = event_log.new_boot()

    assert after != before
    assert event_log.append("t", "b")["seq"] == 2      # seq kept counting
    assert event_log.append("t", "c")["boot_id"] == after


# --- the read API: cursor, filters, gaps -------------------------------------------

def test_cursor_replays_only_what_is_new():
    for i in range(5):
        event_log.append("t", f"e{i}")
    assert _seqs(event_log.read(after_seq=3)["events"]) == [4, 5]
    assert event_log.read(after_seq=5)["events"] == []


def test_filters_by_type_and_wid():
    event_log.append("run_review", "a", wid="@1")
    event_log.append("died", "b", wid="@2")
    event_log.append("run_review", "c", wid="@2")

    assert _seqs(event_log.read(types=["run_review"])["events"]) == [1, 3]
    assert _seqs(event_log.read(wid="@2")["events"]) == [2, 3]
    assert _seqs(event_log.read(types=["run_review"], wid="@2")["events"]) == [3]
    assert _seqs(event_log.read(types=["died", "run_review"])["events"]) == [1, 2, 3]


def test_limit_is_oldest_first_and_resumable():
    """A bounded read must never skip an event the caller has not seen."""
    for i in range(5):
        event_log.append("t", f"e{i}")

    batch = event_log.read(limit=2)
    assert _seqs(batch["events"]) == [1, 2]            # oldest-first, NOT the newest two
    assert batch["next_seq"] == 2

    rest = event_log.read(after_seq=batch["next_seq"])
    assert _seqs(rest["events"]) == [3, 4, 5]          # nothing was skipped


def test_a_filtered_read_still_advances_the_cursor():
    """Otherwise a reader following one type re-scans the whole ring forever."""
    event_log.append("noise", "a")
    event_log.append("noise", "b")
    batch = event_log.read(types=["run_review"])
    assert batch["events"] == []
    assert batch["next_seq"] == 2                      # the log's position, not 0


def test_a_boot_change_is_reported_as_a_resume_gap():
    """The cursor is not silently honoured across a restart — it is called out."""
    event_log.append("t", "a")
    old_boot = event_log.current_boot()
    event_log.new_boot()
    event_log.append("t", "b")

    batch = event_log.read(after_seq=1, after_boot=old_boot)

    assert batch["gap"] is not None
    assert "boot_id changed" in batch["gap"]["reason"]
    assert batch["gap"]["cursor_boot_id"] == old_boot
    assert batch["boot_id"] != old_boot
    assert _seqs(batch["events"]) == [1, 2]            # and it replays, rather than lying


def test_a_matching_boot_id_is_not_a_gap():
    event_log.append("t", "a")
    batch = event_log.read(after_seq=0, after_boot=event_log.current_boot())
    assert batch["gap"] is None


def test_a_cursor_ahead_of_the_log_is_a_gap():
    """The seq space was reset under the reader (the log was wiped)."""
    event_log.append("t", "a")
    batch = event_log.read(after_seq=99, after_boot=event_log.current_boot())
    assert batch["gap"] is not None
    assert "ahead of the log" in batch["gap"]["reason"]


def test_events_no_longer_served_are_a_gap(monkeypatch):
    """A cursor pointing behind the ring/rotation must not resume as if nothing was lost."""
    monkeypatch.setattr(event_log, "RING_SIZE", 3)
    for i in range(6):
        event_log.append("t", f"e{i}")

    batch = event_log.read(after_seq=1, after_boot=event_log.current_boot())

    assert batch["gap"] is not None
    assert "no longer served" in batch["gap"]["reason"]
    assert batch["gap"]["first_seq"] == 4
    assert _seqs(batch["events"]) == [4, 5, 6]


# --- corruption --------------------------------------------------------------------

def test_a_truncated_final_line_is_skipped_not_fatal(log_file):
    """A crash mid-append leaves a torn line. A log you cannot read is worse than a hole."""
    event_log.append("t", "a")
    event_log.append("t", "b")
    with open(log_file, "a") as fh:
        fh.write('{"seq": 3, "type": "t", "summ')      # died mid-write

    batch = event_log.read()

    assert _seqs(batch["events"]) == [1, 2]
    assert batch["corrupt_lines"] == 1
    # ...and the log keeps working: the next append lands on a clean line.
    assert event_log.append("t", "c")["seq"] == 3
    assert _seqs(event_log.read()["events"]) == [1, 2, 3]


def test_garbage_lines_are_skipped(log_file):
    log_file.write_text('not json\n{"seq": 1, "type": "t"}\n[]\n\n')
    batch = event_log.read()
    assert _seqs(batch["events"]) == [1]
    assert batch["corrupt_lines"] == 2                 # the prose and the non-object


def test_reading_a_log_that_does_not_exist_is_empty_not_an_error():
    batch = event_log.read()
    assert batch["events"] == [] and batch["gap"] is None and batch["last_seq"] == 0


# --- rotation ----------------------------------------------------------------------

def test_the_file_rotates_and_seq_keeps_counting(log_file, monkeypatch):
    """An unbounded append is a disk-filler on a busy fleet."""
    monkeypatch.setattr(event_log, "MAX_BYTES", 400)
    for i in range(20):
        event_log.append("t", f"event number {i} with some body to take up room")

    rolled = log_file.parent / (log_file.name + ".1")
    assert rolled.exists()                             # the audit trail rolled, not grew
    assert log_file.stat().st_size <= 400 + 200        # the live file stayed bounded

    live = event_log.read()["events"]
    assert live and _seqs(live)[-1] == 20              # seq never restarted at the roll
    assert _seqs(live) == list(range(_seqs(live)[0], 21))


def test_rotation_keeps_a_bounded_number_of_generations(log_file, monkeypatch):
    monkeypatch.setattr(event_log, "MAX_BYTES", 300)
    monkeypatch.setattr(event_log, "KEEP_ROTATIONS", 2)
    for i in range(40):
        event_log.append("t", f"event {i} padded out with a body of some length")

    assert (log_file.parent / (log_file.name + ".1")).exists()
    assert (log_file.parent / (log_file.name + ".2")).exists()
    assert not (log_file.parent / (log_file.name + ".3")).exists()


# --- retirement (the operator's wipe) ------------------------------------------------

def test_rotate_retires_the_log_to_a_bak_and_mints_a_fresh_boot(log_file):
    """An operator step, not a boot-time unlink: the old file is KEPT, renamed."""
    event_log.append("hook", "a row written when the wid was still wrong")
    old_boot = event_log.current_boot()

    result = event_log.rotate()

    assert result["backup"] == str(log_file) + ".bak"
    assert (log_file.parent / (log_file.name + ".bak")).exists()   # kept, never unlinked
    assert not log_file.exists()
    assert result["boot_id"] != old_boot
    assert result["seq"] == 1                                      # seq stays monotonic


def test_after_a_rotate_the_log_reads_empty_and_a_stale_cursor_is_a_gap(log_file):
    event_log.append("hook", "old")
    old = event_log.read()
    event_log.rotate()

    fresh = event_log.read()
    assert fresh["events"] == []                                   # the retired rows are gone…
    assert fresh["gap"] is None

    # …and a reader still holding a cursor into them is TOLD, not silently resumed:
    # its events were never in this epoch.
    stale = event_log.read(old["next_seq"], after_boot=old["boot_id"])
    assert stale["gap"] is not None
    assert "boot_id changed" in stale["gap"]["reason"]


def test_rotate_never_clobbers_an_earlier_retirement(log_file):
    event_log.append("hook", "first life")
    first = event_log.rotate()
    event_log.append("hook", "second life")
    second = event_log.rotate()

    assert second["backup"] != first["backup"]
    assert Path(first["backup"]).exists()
    assert Path(second["backup"]).exists()


def test_rotate_on_a_missing_log_is_not_an_error(log_file):
    result = event_log.rotate()
    assert result["backup"] is None
    assert result["boot_id"]


def test_appends_after_a_rotate_carry_the_new_boot(log_file):
    event_log.append("hook", "old")
    boot = event_log.rotate()["boot_id"]

    rec = event_log.append("hook", "new")
    assert rec["boot_id"] == boot
    assert rec["seq"] == 2                                         # NOT reset — one seq space
    assert _seqs(event_log.read()["events"]) == [2]


# --- follow ------------------------------------------------------------------------

def test_follow_yields_new_events_in_order():
    event_log.append("t", "a")
    tail = event_log.follow(after_seq=0, interval=0, iterations=2)

    first = next(tail)
    assert _seqs(first["events"]) == [1]

    event_log.append("t", "b")
    second = next(tail)
    assert _seqs(second["events"]) == [2]              # only what is new, in order


# --- concurrency: the hammer -------------------------------------------------------

_HAMMER = (
    "import sys; from chela import event_log; "
    "[event_log.append('hammer', f'writer {sys.argv[1]} event {i}', "
    "{'writer': sys.argv[1], 'i': i}) for i in range(int(sys.argv[2]))]"
)


@pytest.mark.parametrize("writers,each", [(6, 20)])
def test_concurrent_writer_processes_produce_an_intact_log(log_file, writers, each):
    """N REAL processes appending at once: every line intact, every seq exactly once.

    This is the claim the whole log rests on, so it is proven against processes rather
    than threads — a threading test would share the interpreter's memory and could not
    catch the actual hazard, which is two independent writers allocating the same ``seq``
    from a read-modify-write on the shared sidecar. What makes it safe is the flock held
    across (allocate seq → bump the sidecar → write ONE short O_APPEND line), and nothing
    else: no network, no tmux, no second file.
    """
    env = {**os.environ, "CHELA_EVENTS_FILE": str(log_file)}
    procs = [
        subprocess.Popen([sys.executable, "-c", _HAMMER, str(w), str(each)], env=env)
        for w in range(writers)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0] * writers

    lines = log_file.read_text().splitlines()
    assert len(lines) == writers * each                # nothing interleaved into a lost line

    records = [json.loads(line) for line in lines]     # every line whole and parseable
    seqs = sorted(r["seq"] for r in records)
    assert seqs == list(range(1, writers * each + 1))  # no duplicates, no gaps
    assert len({(r["payload"]["writer"], r["payload"]["i"]) for r in records}) == writers * each
    assert len({r["boot_id"] for r in records}) == 1   # one epoch, shared by every writer
