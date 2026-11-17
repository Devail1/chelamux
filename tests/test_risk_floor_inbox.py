"""⚖️🪜 CMX-396 — the inbox names the risk floor, and reads tiers through it."""
from __future__ import annotations

import pytest

from chela import inbox, judge
from tests.test_inbox_cmx86 import _isolated, _queued, _run  # noqa: F401 — autouse fixture

UI = ["chela/dashboard/app.py", "chela/dashboard/static/app.js", "web/src/App.tsx"]


# ----------------------------------------------------------------------------


@pytest.mark.parametrize("floor, note", [
    ("risk:high", " — CORE tier, floor risk:high — "), (None, None)])
def test_the_clean_notice_names_the_floor(floor, note):
    run = _run("awaiting_review", pr_head_sha="abc", judge_state="")
    inbox.tick({}, runs=[run])
    inbox.tick({}, runs=[{**run, "judge_state": judge.J_CLEAN, "judge_sha": "abc",
                          "risk_tier": "core", "risk_tier_floor": floor}])
    (event,) = _queued("run_judge_clean")
    assert event["payload"]["risk_tier_floor"] == floor
    if note:
        assert f"clean and MERGEABLE{note}" in event["summary"]
    else:
        assert "floor" not in event["summary"] and "CORE" not in event["summary"]


def test_review_tiers_floors_a_risk_high_run(monkeypatch, tmp_path):
    wfp = tmp_path / "WORKFLOW.md"
    wfp.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: dev\n---\nbody\n")
    base = _run("awaiting_review", pr_head_sha="f00d", judge_state="", workflow_path=str(wfp))
    monkeypatch.setattr(judge, "_changed_files", lambda *a: list(UI))
    high = {**base, "task_id": "H1", "risk": "high"}
    normal = {**base, "task_id": "N1", "risk": "normal"}
    assert inbox.review_tiers([high, normal]) == {"H1": "core", "N1": "standard"}
