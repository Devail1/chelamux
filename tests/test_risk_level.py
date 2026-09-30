"""⚖️🎚️ CMX-405 — a per-task RISK level (high / normal / low) that scales ONLY the judge's
SEARCH (how many experiments it runs, and where it aims them) and the rework budget.

⛔ The operator constraint these tests pin: the judge ALWAYS runs, and a surviving mutation
STAYS a blocking finding at every level — risk never downgrades a proven survivor to a note.

* parsing — the `<!-- risk: ... -->` marker / `risk:<level>` label, the `normal` default,
  and a marker that changes neither the bare title nor the task id;
* the BOUNDARIES fallback — an unmarked brief touching `judge.py` (etc.) is `high`;
* the knobs — per-level experiment and rework caps (checked in a subprocess against a temp
  `CHELA_DIR`, so no real `~/.chela/config.json` can leak in);
* the judge — the cap differs by level, and at `low` a survivor still BLOCKS;
* the rework loop — `needs_human` after 3 rounds at `low`, after 5 at `high`;
* the claim — the level lands on the run row, from the tracker.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, dispatcher, judge
from chela.sources import Task, apply_risk, infer_risk
from chela.sources.gh_issues import GhIssuesSource
from chela.sources.markdown import MarkdownSource
from chela.workflow import WorkflowDef
from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf
from tests.test_judge import FAKE_GUARD_TEST, REAL_GUARD_TEST, _exp, _workflow_repo

_KNOB_ENVS = (
    "CHELA_MAX_REWORKS", "CHELA_MAX_REWORKS_HIGH", "CHELA_MAX_REWORKS_NORMAL",
    "CHELA_MAX_REWORKS_LOW", "CHELA_JUDGE_EXPERIMENTS_HIGH",
    "CHELA_JUDGE_EXPERIMENTS_NORMAL", "CHELA_JUDGE_EXPERIMENTS_LOW",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    for env in _KNOB_ENVS:
        monkeypatch.delenv(env, raising=False)


def _md_tasks(tmp_path: Path, text: str) -> list[Task]:
    (tmp_path / "TODO.md").write_text(text)
    wf = WorkflowDef(path=tmp_path / "WORKFLOW.md",
                     config={"tracker": {"kind": "markdown", "path": "TODO.md"}},
                     prompt_template="")
    return MarkdownSource(wf).list_open_tasks()


# --- parsing ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["high", "normal", "low"])
def test_each_marker_level_is_read(tmp_path, level):
    [t] = _md_tasks(tmp_path, f"- [ ] ship it <!-- risk: {level} -->\n")
    assert t.risk == level
    assert t.risk_reason == "marker"


def test_no_marker_defaults_to_normal(tmp_path):
    [t] = _md_tasks(tmp_path, "- [ ] ship it\n")
    assert (t.risk, t.risk_reason) == ("normal", "default")


def test_the_marker_changes_neither_the_bare_title_nor_the_id(tmp_path):
    [plain] = _md_tasks(tmp_path, "- [ ] ship it\n")
    [marked] = _md_tasks(tmp_path, "- [ ] ship it <!-- risk: low -->\n")
    assert marked.title == plain.title == "ship it"
    assert marked.id == plain.id
    assert marked.risk == "low"


def test_the_marker_coexists_with_depends_and_is_not_one(tmp_path):
    [dep, t] = _md_tasks(
        tmp_path,
        "- [ ] first thing\n"
        '- [ ] second thing <!-- depends: "first thing" --> <!-- risk: high -->\n',
    )
    assert t.depends == (dep.id,)          # the risk marker did not leak into the payload
    assert t.risk == "high"
    assert t.title == "second thing"
    assert dep.risk == "normal"            # and the depends marker is not a risk marker


def test_a_marker_quoted_in_inline_code_is_prose_not_a_marker(tmp_path):
    [t] = _md_tasks(tmp_path, "- [ ] document `<!-- risk: low -->` syntax\n")
    assert t.risk == "normal"


def test_an_unknown_level_is_ignored_not_guessed(tmp_path):
    [t] = _md_tasks(tmp_path, "- [ ] ship it <!-- risk: extreme -->\n")
    assert (t.risk, t.risk_reason) == ("normal", "default")


def _gh_tasks(tmp_path: Path, labels: list[str]) -> list[Task]:
    wf = WorkflowDef(path=tmp_path / "WORKFLOW.md",
                     config={"tracker": {"kind": "gh_issues", "repo": "o/r",
                                          "require_label": "ready"}},
                     prompt_template="")
    issues = [{"number": 1, "title": "t", "url": "u", "createdAt": "2026-01-01",
               "labels": [{"name": n} for n in ["ready", *labels]], "body": ""}]

    class R:
        returncode = 0
        stdout = json.dumps(issues)
        stderr = ""

    with patch("chela.sources.gh_issues.subprocess.run", return_value=R()):
        return GhIssuesSource(wf).list_open_tasks()


def test_gh_issues_reads_a_risk_label(tmp_path):
    [t] = _gh_tasks(tmp_path, ["risk:low"])
    assert (t.risk, t.risk_reason) == ("low", "label")
    [t] = _gh_tasks(tmp_path, [])
    assert t.risk == "normal"
    [t] = _gh_tasks(tmp_path, ["risk:low", "risk:high"])
    assert t.risk == "high"                 # several labels → the highest stakes


# --- the BOUNDARIES fallback -------------------------------------------------------------

_BRIEF = """\
**Do the thing.**

**OBJECTIVE.** Make it better; mention `judge.py` only here.

**BOUNDARIES.** {boundaries}

**GUARDS.** A guard.
"""


def test_an_unmarked_brief_touching_judge_py_is_high(tmp_path):
    body = _BRIEF.format(boundaries="`chela/judge.py`, tests.")
    t = apply_risk(Task(id="x", title="t", file="", line_number=1, raw="", body=body),
                   None, "marker")
    assert t.risk == "high"
    assert t.risk_reason.startswith("inferred") and "judge.py" in t.risk_reason


def test_the_fallback_reads_only_the_boundaries_paragraph():
    # `judge.py` appears in OBJECTIVE, not BOUNDARIES → no inference.
    assert infer_risk(_BRIEF.format(boundaries="`chela/dashboard/static/js/work.js`.")) is None
    assert infer_risk(None) is None
    assert infer_risk("no boundaries here, judge.py") is None


@pytest.mark.parametrize("heading", ["WHY", "OBJECTIVE", "GUARDS", "VERIFY", "NOTE", "NOTES"])
@pytest.mark.parametrize("form", ["**{h}.**", "{h}."])
def test_the_fallback_stops_at_the_next_heading_after_boundaries(heading, form):
    # The END of the paragraph: a high-risk path named only in a heading that FOLLOWS
    # BOUNDARIES (real GUARDS name the files they pin) must not be read as a boundary.
    # Each heading, in both the bold and the bare line-start form, is its own terminator.
    head = form.format(h=heading)
    safe = f"**BOUNDARIES.** `chela/dashboard/static/js/work.js`.\n\n{head} Pin `judge.py`.\n"
    assert infer_risk(safe) is None
    # Positive control: the same brief with the path moved INTO BOUNDARIES does infer, so
    # the None above is the terminator working, not the regex missing the path altogether.
    hot = f"**BOUNDARIES.** `chela/judge.py`.\n\n{head} Pin `work.js`.\n"
    assert infer_risk(hot) == "judge.py"


def test_an_explicit_marker_beats_the_fallback(tmp_path):
    body = _BRIEF.format(boundaries="`chela/judge.py`.")
    t = apply_risk(Task(id="x", title="t", file="", line_number=1, raw="", body=body),
                   "low", "marker")
    assert (t.risk, t.risk_reason) == ("low", "marker")


def test_the_fallback_reaches_a_markdown_bullet_end_to_end(tmp_path):
    [t] = _md_tasks(
        tmp_path,
        "- [ ] **Harden it.**\n\n"
        "  **BOUNDARIES.** `chela/judge.py`, tests.\n\n"
        "  **GUARDS.** One.\n",
    )
    assert t.risk == "high"


# --- the knobs, in a subprocess against a temp CHELA_DIR ---------------------------------

def _knobs_in_subprocess(tmp_path: Path, **env) -> dict:
    code = (
        "import json\n"
        "from chela import config\n"
        "print(json.dumps({lv: [config.judge_max_experiments(lv), config.max_reworks_for(lv)]"
        " for lv in ('high', 'normal', 'low', None)}))\n"
    )
    child_env = {k: v for k, v in os.environ.items() if k not in _KNOB_ENVS}
    child_env.update(CHELA_DIR=str(tmp_path / "chela"), **env)
    out = subprocess.run([sys.executable, "-c", code], env=child_env, capture_output=True,
                         text=True, check=True, cwd=str(Path(__file__).parent.parent))
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_caps_differ_by_level(tmp_path):
    caps = _knobs_in_subprocess(tmp_path)
    assert caps["high"] == [12, 5]
    assert caps["normal"] == [8, 4]
    assert caps["low"] == [4, 3]
    assert caps["null"] == caps["normal"]      # a pre-CMX-405 row reads as normal


def test_the_global_ceiling_still_bounds_every_level(tmp_path):
    caps = _knobs_in_subprocess(tmp_path, CHELA_MAX_REWORKS="0")
    assert [caps[lv][1] for lv in ("high", "normal", "low")] == [0, 0, 0]
    caps = _knobs_in_subprocess(tmp_path, CHELA_MAX_REWORKS_LOW="1")
    assert caps["low"][1] == 1 and caps["high"][1] == 5


# --- the judge -----------------------------------------------------------------------------

def _judge_run_at(tmp_path, guard_test, experiments, risk, task_id="abc123"):
    repo = _workflow_repo(tmp_path, task_id, guard_test)
    fields = {
        "task_id": task_id, "workflow_path": str(repo / "WORKFLOW.md"), "title": "do a thing",
        "status": "awaiting_review", "window_name": "test-1", "branch_name": "test-1",
        "worktree_path": str(repo), "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/91", "pr_state": "open",
        "pr_checks": dispatcher.CI_PASSING, "pr_head_sha": "cafe1234", "rework_count": 0,
        "risk": risk,
    }
    with dispatcher._db() as conn:
        conn.execute(f"INSERT INTO runs ({', '.join(fields)}) "
                     f"VALUES ({', '.join('?' * len(fields))})", tuple(fields.values()))
        conn.commit()
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": experiments}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)
    return result, dispatcher.resolve_run(task_id), posted


def test_at_low_the_judge_runs_only_the_low_cap(tmp_path):
    """Six proposals at `low` → four run, two dropped OUT LOUD, and the header says so."""
    result, run, posted = _judge_run_at(tmp_path, REAL_GUARD_TEST, [_exp()] * 6, "low")
    assert len(result["outcomes"]) == config.judge_max_experiments("low") == 4
    assert result["state"] == judge.J_CLEAN
    assert "risk: low — 4 experiments" in posted[0]
    assert "2 further experiment(s)" in posted[0]


def test_at_low_a_surviving_mutation_STILL_BLOCKS(tmp_path):
    """⭐ The case that must be ACCEPTED: risk narrows the search, never the verdict."""
    result, run, posted = _judge_run_at(tmp_path, FAKE_GUARD_TEST, [_exp()], "low")
    assert result["state"] == judge.J_BLOCKED
    assert result["blocking"] == 1
    assert run["status"] == "changes_requested"          # sent back — not a note
    assert run["judge_state"] == judge.J_BLOCKED
    verdict = dispatcher.latest_verdict(run)
    assert "SURVIVED DELIBERATE CORRUPTION" in verdict
    assert "risk: low — 4 experiments" in verdict


def test_the_judge_prompt_differs_by_level(tmp_path):
    wf = _wf(tmp_path)
    prompts = {}
    with dispatcher._db() as conn:
        for lv in ("high", "low"):
            row = _row(conn, task_id=f"t-{lv}", workflow_path=str(wf.path), risk=lv)
            v = dispatcher._judge_vars(wf, row, tmp_path, "cafe1234")
            prompts[lv] = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT, v)
    assert "risk low — at most 4 experiments" in prompts["low"]
    assert "risk high — at most 12 experiments" in prompts["high"]
    assert judge.RISK_GUIDANCE["low"] in prompts["low"]
    assert judge.RISK_GUIDANCE["low"] not in prompts["high"]
    # ⛔ Every level is told a survivor still blocks.
    for p in prompts.values():
        assert "blocks this PR at every risk level" in p


# --- the rework cap ----------------------------------------------------------------------

def _tick_changes_requested(tmp_path, risk, rework_count) -> str:
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             rework_count=rework_count, risk=risk)
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_respawn_rework", return_value=True), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        dispatcher.tick(wf.path)
    return dispatcher.resolve_run("abc123")["status"]


@pytest.mark.parametrize("risk,spent,expected", [
    ("low", 2, "changes_requested"),
    ("low", 3, "needs_human"),
    ("high", 4, "changes_requested"),
    ("high", 5, "needs_human"),
    ("normal", 3, "changes_requested"),
    ("normal", 4, "needs_human"),
])
def test_the_rework_cap_is_the_runs_own_risk_level(tmp_path, risk, spent, expected):
    assert _tick_changes_requested(tmp_path, risk, spent) == expected


# --- the claim: the level lands on the run row, from the tracker -------------------------

def test_the_claim_records_the_trackers_risk_on_the_run_row(tmp_path):
    wf = _wf(tmp_path)
    task = Task(id="fresh", title="a new thing", file=str(tmp_path / "TODO.md"),
                line_number=1, raw="- [ ] a new thing <!-- risk: low -->",
                risk="low", risk_reason="marker")
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn, \
         patch.object(dispatcher, "ensure_worktree", return_value=(wt, True)), \
         patch.object(dispatcher, "_launch_agent"), \
         patch.object(dispatcher, "_run_critic"):
        assert dispatcher._spawn(wf, task, 1, conn) is True
    run = dispatcher.resolve_run("fresh")
    assert (run["risk"], run["risk_reason"]) == ("low", "marker")
    assert dispatcher._rework_cap(run) == 3
