"""⚖️🪜 CMX-288 — the `standard` risk tier: a judge round with NO held-out experiments.

* `_spawn_judge` stamps the PR's `chela.risktier` tier on the run row and asks the judge for a
  0% held-out quota on a standard one;
* `chela judge run` plans 0 held-out experiments on a standard run — any `"held_out": true`
  the judge wrote anyway runs as visible — while a core (or unstamped) run keeps today's quota;
* `chela judge show` names the tier.
"""
from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import pytest

from chela import config, dispatcher, judge, toolconfig, workflow
from chela import main as chela_main
from tests.test_judge import FAKE_GUARD_TEST, REAL_GUARD_TEST, _run_row, _wf, _workflow_repo
from tests.test_judge_held_out import _add_sentinel_module, _glyph, _sentinel


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-dir")
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")


def _tagged(n_held: int, n_visible: int) -> list[dict]:
    return ([_glyph(guard=f"held {i}", held_out=True) for i in range(n_held)]
            + [_glyph(guard=f"visible {i}") for i in range(n_visible)])


@pytest.mark.parametrize("held_out, expected", [(False, 0), (True, 2)])
def test_the_planned_list_holds_out_nothing_only_when_told(held_out, expected):
    planned, dropped = judge.plan_experiments(_tagged(2, 3), 4, toolconfig.DEFAULT_GLOBS,
                                              held_out=held_out)
    assert dropped == 1 and len(planned) == 4
    assert sum(1 for e in planned if e.get("held_out") is True) == expected
    assert [e["guard"] for e in planned] == ["held 0", "held 1", "visible 0", "visible 1"]


def test_a_malformed_item_survives_planning_untouched():
    planned, _ = judge.plan_experiments(["junk", _glyph(held_out=True)], 12,
                                        toolconfig.DEFAULT_GLOBS, held_out=False)
    assert planned[0] == "junk" and planned[1]["held_out"] is False


@pytest.mark.parametrize("tier, judge_sha, held, quota", [
    ("standard", "cafe1234", 0, 0), ("core", "cafe1234", 1, 1), ("human", "cafe1234", 1, 1),
    (None, "cafe1234", 1, 1),
    ("standard", "0ld5ha", 1, 1),     # the head moved since the stamp: full judge
    ("standard", None, 1, 1),         # no classified head at all: full judge
])
def test_judge_run_schedules_held_out_by_tier(tmp_path, tier, judge_sha, held, quota):
    """The REAL `chela judge run`, three experiments (one tagged held-out) on a row stamped
    with ``tier``: only `standard` on the head it was classified on runs the tagged one as
    visible, with a 0 quota."""
    task_id = f"tier-{tier}-{judge_sha}"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    wt = judge.judge_worktree_path(workflow.load_workflow(repo / "WORKFLOW.md"), task_id)
    _add_sentinel_module(wt)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, risk_tier=tier, judge_sha=judge_sha)
    exp = tmp_path / "experiments.json"
    exp.write_text(json.dumps({"experiments": [
        _glyph(), _sentinel(), _glyph(guard="a second visible one")]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp, cleanup=False)
    assert (result.get("held_out") or {}).get("total", 0) == held
    rec = judge.load_private(task_id)[-1]
    assert rec["held_out"]["total"] == held and rec["held_out"]["quota"] == quota
    assert rec["visible"]["total"] == 3 - held
    assert rec["tier"] == (tier or "")


@pytest.mark.parametrize("files, merge, tier", [
    (["web/src/App.tsx"], {}, "standard"), (["chela/judge.py"], {}, "core"), (None, {}, "core"),
    (["web/src/App.tsx"], {"core": ["web/**"]}, "core"),     # the workflow's own core paths
])
def test_spawn_judge_stamps_the_tier_and_hands_it_to_the_prompt(tmp_path, files, merge, tier):
    wf = _wf(tmp_path, merge=merge)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, "abc123", workflow_path=str(wf.path), branch_name="test-abc")
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        seen: list = []
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "_refresh_judge_worktree", return_value=""), \
             patch.object(judge, "_changed_files", return_value=files), \
             patch.object(judge, "docs_only_diff", return_value=None), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars",
                          side_effect=lambda *a: (seen.append(a[-1]), {})[1]), \
             patch.object(dispatcher, "_launch_agent"):
            # CMX-296: a STANDARD head gets no judge round — stamped, never launched.
            assert dispatcher._spawn_judge(wf, row, "cafe1234", conn) is (tier != "standard")
    run = dispatcher.resolve_run("abc123")
    assert run["risk_tier"] == tier
    assert seen == ([] if tier == "standard" else [tier, tier])
    assert (run["judge_state"] == "standard_no_judge") is (tier == "standard")


@pytest.mark.parametrize("tier, pct", [("standard", 0), ("core", 30), ("", 30)])
def test_the_judge_prompt_asks_for_no_held_out_on_standard(tmp_path, tier, pct):
    repo = _workflow_repo(tmp_path, "tp", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "tp")
    wf = workflow.load_workflow(repo / "WORKFLOW.md")
    jv = dispatcher._judge_vars(wf, dispatcher.resolve_run("tp"),
                                judge.judge_worktree_path(wf, "tp"), "cafe", None, tier)
    assert jv["held_out_pct"] == pct
    rendered = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT, jv)
    assert ("hold NOTHING out" in rendered) is (tier == "standard")
    assert ("about\n     30% of your" in rendered) is (tier != "standard")


@pytest.mark.parametrize("row_tier, rec_tier, shown", [
    ("standard", "core", "standard"), (None, "core", "core"), (None, "", "unknown"),
])
def test_judge_show_names_the_tier(tmp_path, capsys, row_tier, rec_tier, shown):
    repo = _workflow_repo(tmp_path, "ts", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "ts", risk_tier=row_tier)
    judge.record_private("ts", judge.Report(), {}, tier=rec_tier)
    chela_main.cmd_judge_show(argparse.Namespace(run="ts", held_out=False))
    assert f"risk tier:              {shown}\n" in capsys.readouterr().out
