"""⛔ CMX-456: a self-check experiment that never applied must never be journalled as a
mutation, and a stale journal entry (its bytes are the clean file's) must never refuse a push
of a clean, committed file — while a real leftover mutation still does."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import dispatcher, judge, main, sweep
from tests.test_dispatcher_rework import _row
from tests.test_judge_kill_recovery import AFTER, BEFORE, FEAT, _git, _repo

REAL_MUTATION_GATE = True            # the conftest stub must not hide the gate under test

MUTATED = FEAT.replace(BEFORE, AFTER, 1)
LEGACY_NOOP = {"file": "feat.py", "line": 6, "blob": sweep._blob_id(FEAT.encode()),
               "text": "(no such line)"}         # exactly what CMX-455's journal held
HINT = "chela judge journal --prune-stale <run>"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _entries(root: Path) -> list[dict]:
    path = sweep.journal_path(root)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines()]


def _write_journal(root: Path, *entries: dict) -> None:
    sweep.journal_path(root).write_text("".join(json.dumps(e) + "\n" for e in entries))


def _running(root: Path) -> None:
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(root), pr_url=None)


# --- 1. never journal an experiment that changed nothing -----------------------------------


def _self_check(root: Path, *experiments: dict) -> judge.Report:
    return judge.self_check(root, f'"{sys.executable}" -c "pass"',
                            {"experiments": list(experiments)}, select_tests=False)


def test_a_self_check_experiment_anchored_past_eof_leaves_no_journal_entry(tmp_path):
    root = _repo(tmp_path / "r")
    report = _self_check(root, {"guard": "g", "file": "feat.py",
                                "before": "    if x == 999:", "after": "    if False:"})
    assert [o.verdict for o in report.outcomes] == [judge.INVALID]
    assert _entries(root) == []
    assert not sweep.marker_path(root).exists()
    assert sweep.mutation_blockers(root) == []


def test_an_ambiguous_anchor_leaves_no_journal_entry(tmp_path):
    root = _repo(tmp_path / "r")
    (root / "feat.py").write_text(FEAT + FEAT)        # BEFORE now occurs twice
    _self_check(root, {"guard": "g", "file": "feat.py", "before": BEFORE, "after": AFTER})
    assert _entries(root) == []


def test_negative_control_an_experiment_that_applied_is_journalled(tmp_path):
    root = _repo(tmp_path / "r")
    _self_check(root, {"guard": "g", "file": "feat.py", "before": BEFORE, "after": AFTER})
    assert _entries(root) == [{"file": "feat.py", "line": 2,
                               "blob": sweep._blob_id(MUTATED.encode()),
                               "text": "if False and (x == 3):",
                               "orig": sweep._blob_id(FEAT.encode())}]


def test_write_marker_of_a_no_op_writes_neither_marker_nor_journal(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, FEAT)
    assert not sweep.marker_path(root).exists()
    assert _entries(root) == []


@pytest.mark.parametrize("before, marked", [
    ("    if x == 3:", True),             # exactly once → marked
    ("    if x == 999:", False),          # never → INVALID, not marked
    ("    return", False),                # twice → INVALID, not marked
])
def test_mark_mutation_marks_only_an_anchor_that_occurs_exactly_once(tmp_path, before, marked):
    root = _repo(tmp_path / "r")
    exp = judge.Experiment(guard="g", file="feat.py", before=before, after="    pass")
    judge._mark_mutation(root, root / "feat.py", exp)
    assert sweep.marker_path(root).exists() is marked
    assert bool(_entries(root)) is marked


# --- 2. a stale entry never blocks; a real mutation still does -----------------------------


def test_a_legacy_no_op_entry_equal_to_head_does_not_block_request_push(tmp_path, capsys):
    """CMX-455 exactly: the file is clean and committed, `git diff HEAD` is empty, and the
    journal holds the clean blob under `(no such line)`."""
    root = _repo(tmp_path / "r")
    _write_journal(root, LEGACY_NOOP)
    assert _git(root, "diff", "HEAD") == ""
    _running(root)
    result = dispatcher.request_push("t1", pr_title="CMX-1: x", pr_body="b")
    assert result["ok"] is True, result
    assert (root / ".chela-push-request.json").exists()
    assert _entries(root) == []                                  # dropped …
    assert ("chela: dropped a stale mutation-journal entry — feat.py:6 (`(no such line)`) "
            "never changed the file\n") in capsys.readouterr().err   # … and logged


def test_an_entry_whose_blob_equals_its_original_never_blocks(tmp_path):
    root = _repo(tmp_path / "r")
    blob = sweep._blob_id(FEAT.encode())
    _write_journal(root, {"file": "feat.py", "line": 2, "blob": blob, "text": "x",
                          "orig": blob})
    assert sweep.mutation_blockers(root) == []
    assert sweep.mutation_blockers(root, head_only=True) == []


def test_the_daemon_push_does_not_refuse_a_stale_entry(tmp_path):
    root = _repo(tmp_path / "r")
    _write_journal(root, LEGACY_NOOP)
    assert sweep.mutation_blockers(root, head_only=True) == []


def test_negative_control_a_real_leftover_mutation_still_blocks_request_push(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    sweep.marker_path(root).unlink()
    (root / "feat.py").write_text(MUTATED)
    _running(root)
    result = dispatcher.request_push("t1", pr_title="CMX-1: x", pr_body="b")
    assert result["ok"] is False
    assert "feat.py:2 is a sweep/self-check MUTATION (`if False and (x == 3):`) in the " \
           "working tree — restore it" in result["error"]
    assert not (root / ".chela-push-request.json").exists()
    assert len(_entries(root)) == 1                              # never pruned


def test_negative_control_a_committed_real_mutation_still_blocks(tmp_path):
    """Its blob equals HEAD and `git diff HEAD` is empty too — but its `orig` differs, so it
    is a mutation that LANDED, which is exactly what CMX-398 exists to stop."""
    root = _repo(tmp_path / "r")
    sweep._write_marker(root, "feat.py", FEAT, MUTATED)
    sweep.marker_path(root).unlink()
    (root / "feat.py").write_text(MUTATED)
    _git(root, "commit", "-qam", "oops")
    assert _git(root, "diff", "HEAD") == ""
    blockers = sweep.mutation_blockers(root, head_only=True)
    assert blockers[0] == ("feat.py:2 is a sweep/self-check MUTATION (`if False and "
                           "(x == 3):`) COMMITTED in HEAD — revert it before pushing")
    assert sweep.prune_stale(root) == ([], "")
    assert len(_entries(root)) == 1


@pytest.mark.parametrize("disk, head, stale", [
    ("clean", "clean", True),         # the CMX-455 case: git diff HEAD empty
    ("clean", "other", False),        # HEAD differs: not provable
    ("other", "clean", False),        # the working tree differs: not provable
])
def test_a_legacy_entry_is_stale_only_when_disk_and_head_both_hold_its_blob(tmp_path, disk,
                                                                            head, stale):
    blob = sweep._blob_id(FEAT.encode())
    pick = {"clean": blob, "other": "0" * 40}
    assert sweep._is_stale(sweep._entry(LEGACY_NOOP), pick[disk], pick[head]) is stale


def test_a_legacy_entry_naming_a_real_line_is_never_stale(tmp_path):
    blob = sweep._blob_id(FEAT.encode())
    entry = sweep._entry({**LEGACY_NOOP, "line": 2, "text": "if x == 3:"})
    assert sweep._is_stale(entry, blob, blob) is False


# --- 3. the refusal points at a chela command, not at the journal --------------------------


def test_the_refusal_names_the_prune_command_and_never_the_journal_path(tmp_path):
    root = _repo(tmp_path / "r")
    sweep._journal(root, "feat.py", 2, MUTATED, FEAT)
    (root / "feat.py").write_text(MUTATED)
    blockers = sweep.mutation_blockers(root)
    assert HINT in blockers[-1]
    assert str(sweep.journal_path(root)) not in "; ".join(blockers)
    assert "remove its entry" not in "; ".join(blockers)


def test_prune_stale_drops_only_stale_entries_and_keeps_the_rest_verbatim(tmp_path):
    root = _repo(tmp_path / "r")
    real = {"file": "feat.py", "line": 2, "blob": sweep._blob_id(MUTATED.encode()),
            "text": "if False and (x == 3):", "orig": sweep._blob_id(FEAT.encode())}
    sweep.journal_path(root).write_text(
        json.dumps(LEGACY_NOOP) + "\n" + json.dumps(real) + "\n" + '{"torn')
    dropped, err = sweep.prune_stale(root)
    assert err == ""
    assert dropped == [sweep._entry(LEGACY_NOOP)]
    assert sweep.journal_path(root).read_text() == json.dumps(real) + "\n"


def test_prune_stale_with_nothing_stale_writes_nothing(tmp_path):
    root = _repo(tmp_path / "r")
    assert sweep.prune_stale(root) == ([], "")                  # no journal at all
    real = json.dumps({"file": "feat.py", "line": 2, "blob": "a" * 40, "text": "t"}) + "\n"
    sweep.journal_path(root).write_text(real)
    assert sweep.prune_stale(root) == ([], "")
    assert sweep.journal_path(root).read_text() == real


def test_prune_stale_fails_closed_on_an_unreadable_journal(tmp_path):
    root = _repo(tmp_path / "r")
    sweep.journal_path(root).write_text("{not json\n")
    dropped, err = sweep.prune_stale(root)
    assert dropped == []
    assert err.startswith(f"the mutation journal {sweep.journal_path(root)} is unreadable (")
    assert sweep.journal_path(root).read_text() == "{not json\n"


def test_prune_stale_fails_closed_on_an_unreadable_head(tmp_path):
    root = tmp_path / "plain"
    (root / ".git").mkdir(parents=True)
    _write_journal(root, LEGACY_NOOP)
    dropped, err = sweep.prune_stale(root)
    assert dropped == []
    assert err.startswith("could not read HEAD's tree to check it for a mutation: ")
    assert len(_entries(root)) == 1


def test_prune_stale_reports_a_journal_it_cannot_rewrite(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    _write_journal(root, LEGACY_NOOP)

    def boom(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(sweep.os, "replace", boom)
    dropped, err = sweep.prune_stale(root)
    assert dropped == []
    assert err == (f"could not rewrite the mutation journal {sweep.journal_path(root)}: "
                   "read-only")


def test_judge_journal_prune_stale_cli(tmp_path, capsys):
    root = _repo(tmp_path / "r")
    _write_journal(root, LEGACY_NOOP)
    _running(root)
    main.cmd_judge_journal(SimpleNamespace(run="t1", prune_stale=True))
    assert capsys.readouterr().out == (
        "t1: dropped 1 stale mutation-journal entry:\n"
        "  feat.py:6 (`(no such line)`) — never changed the file\n")
    main.cmd_judge_journal(SimpleNamespace(run="t1", prune_stale=True))
    assert capsys.readouterr().out == "t1: no stale mutation-journal entry — nothing dropped\n"


def test_judge_journal_cli_refusals(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main.cmd_judge_journal(SimpleNamespace(run="t1", prune_stale=False))
    assert e.value.code == 2
    assert capsys.readouterr().out == "judge journal: nothing to do — pass --prune-stale\n"
    with pytest.raises(SystemExit) as e:
        main.cmd_judge_journal(SimpleNamespace(run="nope", prune_stale=True))
    assert e.value.code == 1
    assert capsys.readouterr().out == (
        "judge journal: no run matches 'nope' (task id, branch, or window name)\n")


def test_prune_mutation_journal_without_a_worktree_refuses(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, task_id="t2", status="running", worktree_path=None, pr_url=None)
    assert dispatcher.prune_mutation_journal("t2") == {
        "ok": False, "task_id": "t2", "error": "no worktree on record for t2"}


def test_prune_mutation_journal_surfaces_a_prune_error(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    _running(root)
    monkeypatch.setattr(sweep, "prune_stale", lambda wt: ([], "boom"))
    assert dispatcher.prune_mutation_journal("t1") == {
        "ok": False, "task_id": "t1", "error": "boom"}


def test_the_cli_parser_wires_judge_journal(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "cmd_judge_journal", lambda a: seen.append(a))
    monkeypatch.setattr(sys, "argv", ["chela", "judge", "journal", "cmx-9", "--prune-stale"])
    main.main()
    assert seen[0].run == "cmx-9" and seen[0].prune_stale is True


