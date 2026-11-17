"""⚖️🪜 CMX-396 — the tracker's risk level is a FLOOR on the merge tier.

``risk:high`` ⇒ at least CORE (a full judge round), whatever the PR's files are;
``risk:normal`` / ``risk:low`` / none ⇒ the file-based tier. The level comes from the run row
(copied from the tracker at claim), and every surface that names the tier says why:
``CORE (floor: risk:high)``.
"""
from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import pytest

from chela import contract, dispatcher, event_log, judge, overrides, risktier
from chela import main as chela_main
from chela.dispatcher import CI_PASSING, CIStatus
from tests.test_contract import _seed_run
from tests.test_judge import _run_row, _wf

D = risktier.DEFAULT_ALWAYS_HUMAN
UI = ["chela/dashboard/app.py", "chela/dashboard/static/app.js", "web/src/App.tsx"]
HEAD = "cafe1234"


@pytest.fixture(autouse=True)
def _own_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")
    monkeypatch.setattr(event_log, "LOG_PATH", tmp_path / "events.jsonl", raising=False)


# --- the rule ------------------------------------------------------------------------------


@pytest.mark.parametrize("risk, tier, why", [
    ("high", "core", "risk:high"),           # the acceptance: UI-only files, risk:high ⇒ CORE
    ("HIGH ", "core", "risk:high"),          # the tracker's spelling, normalised
    ("normal", "standard", None),            # the acceptance's other half
    ("low", "standard", None),
    (None, "standard", None),                # a row from before the level was recorded
    ("", "standard", None),
    ("extreme", "standard", None),           # garbage is no floor (and never an error)
    (3, "standard", None),
    ("highish", "standard", None),
])
def test_a_ui_only_pr_is_floored_only_by_risk_high(risk, tier, why):
    assert risktier.explain(UI, D, risk=risk) == (tier, None, why)
    assert risktier.classify(UI, D, risk=risk) == (tier, None)


def test_without_the_label_the_ui_only_pr_is_standard():
    """Negative control: the same files with the label ignored are STANDARD."""
    assert risktier.classify(UI, D) == ("standard", None)


@pytest.mark.parametrize("files, risk, expected", [
    (["docs/guide.md"], "high", ("core", None, "risk:high")),         # prose is lifted too
    (["docs/guide.md"], "normal", ("prose", None, None)),
    (["chela/dispatcher.py"], "high", ("core", None, None)),          # already core: no floor
    (["chela/contract.py"], "high", ("human", "chela/contract.py", None)),   # never lowered
    (None, "high", ("core", None, None)),                             # unknown is core anyway
])
def test_the_floor_never_lowers_and_only_names_itself_when_it_lifted(files, risk, expected):
    assert risktier.explain(files, D, risk=risk) == expected


@pytest.mark.parametrize("tier, why, shown", [
    ("core", "risk:high", "CORE (floor: risk:high)"),
    ("standard", None, "STANDARD"),
    ("human", None, "HUMAN"),
    (None, None, "UNKNOWN"),
])
def test_describe(tier, why, shown):
    assert risktier.describe(tier, why) == shown


@pytest.mark.parametrize("path", ["chela/dashboard/identity.py", "chela/share_requests.py"])
def test_trust_boundary_files_are_core_without_a_label(path):
    assert risktier.classify(["web/src/App.tsx", path], D) == ("core", None)


# --- the dispatcher: the no-judge stamp, the judge spawn, the hand-in ----------------------


def _stamp(tmp_path, files, risk):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), risk=risk)
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    with patch.object(judge, "_changed_files", return_value=files), \
         patch.object(judge, "live_judge_run", return_value=None):
        stamped = dispatcher._stamp_prose_only(wf, row)
    return stamped, dispatcher.resolve_run("abc123")


@pytest.mark.parametrize("risk, stamped", [("high", False), ("normal", True), ("low", True),
                                           (None, True)])
def test_a_risk_high_ui_pr_is_never_stamped_no_judge(tmp_path, risk, stamped):
    got, run = _stamp(tmp_path, UI, risk)
    assert got is stamped
    assert run["judge_state"] == ("standard_no_judge" if stamped else None)


@pytest.mark.parametrize("files, risk, launched, tier, floor", [
    (UI, "high", True, "core", "risk:high"),
    (UI, "normal", False, "standard", None),
    (["chela/dispatcher.py"], "high", True, "core", None),
])
def test_spawn_judge_judges_a_risk_high_run_and_records_why(tmp_path, files, risk, launched,
                                                            tier, floor):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), branch_name="test-abc",
                 risk=risk, risk_tier_floor="stale")
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "_refresh_judge_worktree", return_value=""), \
             patch.object(judge, "_changed_files", return_value=files), \
             patch.object(judge, "docs_only_diff", return_value=None), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}), \
             patch.object(dispatcher, "_launch_agent") as launch:
            assert dispatcher._spawn_judge(wf, row, HEAD, conn) is launched
    assert launch.called is launched
    run = dispatcher.resolve_run("abc123")
    assert (run["risk_tier"], run["risk_tier_floor"]) == (tier, floor)


def _insert(task_id, worktree, workflow, risk):
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number, risk) "
            "VALUES (?, ?, 't', 'running', 'w', ?, 'b', '2026-11-17T00:00:00+00:00', 1, 1, ?)",
            (task_id, str(workflow), str(worktree), risk))


@pytest.mark.parametrize("risk, tier", [("high", "core"), ("normal", "standard"),
                                        (None, "standard")])
def test_hand_in_tier_is_floored_by_the_runs_risk(tmp_path, risk, tier):
    wfp = tmp_path / "WORKFLOW.md"
    wfp.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: dev\n---\nbody\n")
    _insert("t1", tmp_path, wfp, risk)
    with patch.object(judge, "_changed_files", return_value=UI):
        assert dispatcher.hand_in_tier("t1") == tier
    assert dispatcher.hand_in_checks(tier, experiments=False) == \
        (("sweep",) if risk == "high" else ("lint", "suite"))


# --- chela merge ---------------------------------------------------------------------------


@pytest.fixture
def wfrepo(tmp_path):
    (tmp_path / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\nworkspace:\n  base_branch: dev\n---\nbody\n")
    return tmp_path


def _merge(wfrepo, judge_state, risk, files=UI, override=False):
    _seed_run(wfrepo, judge_state=judge_state)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET risk=? WHERE task_id='t1'", (risk,))
    with patch.object(contract, "_read_pr_files", return_value=files), \
         patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(CI_PASSING, head_sha=HEAD)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge", return_value={"ok": True}) as squash:
        result = contract.merge("t1", override=override, reason="r")
    return result, squash.called


@pytest.mark.parametrize("risk, merged", [("high", False), ("normal", True), (None, True)])
def test_merge_refuses_a_no_judge_stamp_on_a_risk_high_run(wfrepo, risk, merged):
    result, squashed = _merge(wfrepo, "standard_no_judge", risk)
    assert result["ok"] is squashed is merged, result
    if not merged:
        assert "its live tier is CORE (floor: risk:high)" in result["error"]
        assert (result["risk_tier"], result["risk_tier_floor"]) == ("core", "risk:high")


@pytest.mark.parametrize("risk, shown, floor", [("high", "CORE (floor: risk:high)", "risk:high"),
                                                ("low", "STANDARD", None)])
def test_a_merge_says_why_the_tier_is_what_it_is(wfrepo, capsys, risk, shown, floor):
    result, squashed = _merge(wfrepo, "clean", risk)
    assert result["ok"] and squashed, result
    assert result["tier_shown"] == shown
    payload = event_log.read(types=["orchestrator.merge"])["events"][-1]["payload"]
    assert payload["risk_tier_floor"] == floor
    with patch.object(contract, "merge", return_value=result):
        chela_main.cmd_merge(argparse.Namespace(run="t1", reason="", override=False))
    assert f"  risk tier: {shown}\n" in capsys.readouterr().out


def test_cmd_merge_prints_no_tier_line_when_the_gate_named_none(capsys):
    result = {"ok": True, "task_id": "t1", "base": "dev", "merge_commit_sha": "abc",
              "event_seq": 1, "override": None}
    with patch.object(contract, "merge", return_value=result):
        chela_main.cmd_merge(argparse.Namespace(run="t1", reason="", override=False))
    assert "risk tier" not in capsys.readouterr().out


@pytest.mark.parametrize("risk, says", [("high", True), ("normal", False)])
def test_the_override_question_names_the_floor(wfrepo, monkeypatch, tmp_path, risk, says):
    monkeypatch.setenv("CHELA_OVERRIDE_WAIT_S", "600")
    created: list = []
    monkeypatch.setattr(overrides, "create",
                        lambda meta: (created.append(meta), {"id": "r1", "ttl": 600})[1])
    monkeypatch.setattr(overrides, "pending", lambda: [])
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)
    result, _ = _merge(wfrepo, "blocked", risk, override=True)
    assert result.get("pending"), result
    assert ("Tier: CORE (floor: risk:high)." in created[0]["question"]) is says


def test_judge_show_names_the_floor(tmp_path, capsys):
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, "ts", risk_tier="core", risk_tier_floor="risk:high")
    judge.record_private("ts", judge.Report(), {}, tier="core")
    chela_main.cmd_judge_show(argparse.Namespace(run="ts", held_out=False))
    assert "risk tier:              core (floor: risk:high)\n" in capsys.readouterr().out


def test_the_floor_is_stamped_only_by_the_spawn_and_cleared_by_a_no_judge_stamp(tmp_path):
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, "t9", risk_tier_floor="risk:high")
    assert dispatcher._stamp_tier_verdict("t9", "standard", HEAD) is True
    assert dispatcher.resolve_run("t9")["risk_tier_floor"] is None
    json.dumps(dispatcher.resolve_run("t9"))   # the row stays serialisable


@pytest.mark.parametrize("risk, merged", [("high", False), ("normal", True)])
def test_merge_refuses_a_prose_only_stamp_on_a_risk_high_run(wfrepo, risk, merged):
    result, squashed = _merge(wfrepo, "prose_only", risk, files=["docs/guide.md"])
    assert result["ok"] is squashed is merged, result
    if not merged:
        assert "stamped prose-only, but its live tier is CORE (floor: risk:high)" \
            in result["error"]
