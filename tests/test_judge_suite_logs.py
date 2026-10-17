"""📄 CMX-163 — a non-green judge suite run is written to a failure log, and pointed to."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, dispatcher, judge
from chela import main as chela_main
from tests.test_judge import REAL_GUARD_TEST, TEST_CMD, _exp, _run_row, _workflow_repo

RED_TEST = '''\
from guard import chip

def test_deliberately_red():
    assert chip("on")["hue"] == "purple", "DELIBERATE-RED: the hue is not purple"
'''


@pytest.fixture(autouse=True)
def _own_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-dir")


def _judge(tmp_path, guard_test, task_id):
    repo = _workflow_repo(tmp_path, task_id, guard_test)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)
    return result, dispatcher.resolve_run(task_id), posted


def test_a_red_baseline_writes_its_log_and_every_surface_points_to_it(tmp_path, capsys,
                                                                      monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result, run, posted = _judge(tmp_path, RED_TEST, "red1")
    assert result["state"] == judge.J_CANNOT_VERIFY

    log = tmp_path / "chela-dir" / "judge-logs" / "red1" / "1-suite-1.log"
    text = log.read_text()
    assert "test_deliberately_red" in text
    assert "DELIBERATE-RED: the hue is not purple" in text
    assert f"command:   {TEST_CMD} --junitxml=" in text
    assert f"cwd:       {tmp_path / '.chela' / 'wts' / 'judge-red1'}\n" in text
    assert "exit code: 1\n" in text
    assert "  CHELA_DIR=" in text                       # the env diff vs the operator
    assert oct(log.stat().st_mode & 0o777) == "0o600"

    shown = "~/chela-dir/judge-logs/red1/1-suite-1.log"
    assert run["judge_detail"].startswith(f"📄 full suite output: {shown} — ")
    assert shown in posted[0] and str(tmp_path) not in posted[0]
    assert judge.load_private("red1")[-1]["suite_logs"] == [str(log)]
    chela_main.cmd_judge_show(argparse.Namespace(run="red1", held_out=False))
    assert f"📄 suite output: {log}" in capsys.readouterr().out


def test_a_green_baseline_writes_no_failure_log(tmp_path):
    result, run, posted = _judge(tmp_path, REAL_GUARD_TEST, "green1")
    assert result["state"] == judge.J_CLEAN
    assert not (tmp_path / "chela-dir" / "judge-logs" / "green1").exists()
    assert judge.load_private("green1")[-1]["suite_logs"] == []
    assert "full suite output" not in posted[0]


def test_run_suite_records_cmd_cwd_env_diff_times_and_output(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_OPERATOR_ONLY", "x")
    before = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    s = judge.run_suite("echo out; echo err >&2; exit 3", tmp_path, 30, {"EXTRA": "1"})
    assert s.exit_code == 3 and s.run["cmd"] == "echo out; echo err >&2; exit 3"
    assert s.run["cwd"] == str(tmp_path) and s.run["output"] == "out\nerr\n"
    assert s.run["env_diff"]["CHELA_OPERATOR_ONLY"] is None
    assert s.run["env_diff"]["EXTRA"] == "1" and "PATH" not in s.run["env_diff"]
    assert before <= s.run["started"] <= s.run["ended"]


def test_a_timed_out_or_unstartable_suite_still_keeps_what_it_can(tmp_path):
    t = judge.run_suite("echo partial; sleep 5", tmp_path, 1)
    assert not t.ok and t.run["output"] == "partial\n" and t.run["ended"] >= t.run["started"]
    s = judge.run_suite("true", tmp_path / "missing-cwd", 5)
    assert not s.ok and s.run["cmd"] == "true" and s.run["ended"]
    [path] = judge.write_suite_logs("gone", 2, [s])
    assert path.endswith("judge-logs/gone/2-suite-1.log")
    assert "command:   true\n" in Path(path).read_text()


def test_the_log_output_is_bounded_to_its_tail(tmp_path):
    s = judge.run_suite(f"python3 -c \"print('a' * {judge.SUITE_LOG_CHARS + 50})\"; "
                        "echo LAST-LINE", tmp_path, 30)
    assert len(s.run["output"]) == 200_000 and s.run["output"].endswith("LAST-LINE\n")


def test_an_unwritable_log_dir_never_raises(tmp_path):
    (tmp_path / "chela-dir").mkdir()
    (tmp_path / "chela-dir" / "judge-logs").write_text("a file, not a dir")
    s = judge.SuiteResult(True, 1, 0, 1, 0, "tail")
    assert judge.write_suite_logs("t", 1, [s]) == []


def _aged(path: Path, days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    t = time.time() - days * 86400
    os.utime(path, (t, t))
    return path


def test_logs_older_than_30_days_are_pruned_across_every_task(tmp_path):
    root = tmp_path / "logs"
    old = _aged(root / "a" / "1-suite-1.log", 31)
    fresh = _aged(root / "b" / "1-suite-1.log", 29)
    other = _aged(root / "a" / "notes.txt", 400)          # not a suite log — never touched
    assert judge.prune_suite_logs(root) == [old]
    assert not old.exists() and fresh.exists() and other.exists()


def test_only_the_newest_20_per_task_are_kept(tmp_path):
    root = tmp_path / "logs"
    logs = [_aged(root / "a" / f"{i}-suite-1.log", 1 - i / 100) for i in range(22)]
    keep_b = _aged(root / "b" / "1-suite-1.log", 2)
    assert sorted(judge.prune_suite_logs(root)) == sorted(logs[:2])
    assert all(f.exists() for f in logs[2:]) and keep_b.exists()


def test_writing_a_log_prunes(tmp_path):
    stale = _aged(tmp_path / "chela-dir" / "judge-logs" / "old" / "1-suite-1.log", 45)
    judge.write_suite_logs("new", 1, [judge.SuiteResult(True, 1, 0, 1, 0, "t")])
    assert not stale.exists()


@pytest.mark.parametrize("path, shown", [
    ("/h/me/.chela/x.log", "~/.chela/x.log"),
    ("/h/meX/.chela/x.log", "/h/meX/.chela/x.log"),
    ("/elsewhere/x.log", "/elsewhere/x.log"),
])
def test_display_path_hides_only_the_home_dir(monkeypatch, path, shown):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/h/me")))
    assert judge.display_path(path) == shown


def test_the_env_diff_lists_removals_by_name_only():
    assert judge._env_diff({"K": "1", "GONE": "s", "C": "a"}, {"K": "1", "C": "b"}) == {
        "C": "b", "GONE": None}
