"""CMX-382: chelamux's own WORKFLOW.md gives the NIGHTLY sweep 3 h / 400 mutations, and leaves
the per-PR judge sweep on its own 2400 s.

Loads the committed ``WORKFLOW.md`` — not a fixture — so dropping or mistyping the ``sweep:``
block fails here, not silently on the next night.
"""
from __future__ import annotations

import re
from pathlib import Path

from chela import sweep, sweep_nightly
from chela.workflow import load_workflow, parse_workflow

REPO_WORKFLOW = Path(__file__).resolve().parent.parent / "WORKFLOW.md"

_SWEEP_BLOCK = re.compile(r"^sweep:\n(?:[ \t]+.*\n)+", re.M)


def _without_sweep_block() -> str:
    text = REPO_WORKFLOW.read_text()
    stripped, n = _SWEEP_BLOCK.subn("", text)
    assert n == 1, "the committed WORKFLOW.md must carry exactly one top-level sweep: block"
    return stripped


def test_repo_workflow_gives_the_nightly_sweep_3h_and_400_mutations():
    budget = sweep_nightly.nightly_budget(load_workflow(REPO_WORKFLOW))
    assert (budget.seconds, budget.max_mutations) == (10800.0, 400)


def test_the_per_pr_judge_sweep_budget_is_unchanged():
    wf = load_workflow(REPO_WORKFLOW)
    assert wf.get("judge", "sweep_budget_seconds") == 2400
    budget = sweep.resolve_budget(wf)   # the per-PR path: judge.sweep_* only, never sweep.*
    assert (budget.seconds, budget.max_mutations) == (2400.0, 60)


def test_without_the_block_the_nightly_falls_back_to_the_judge_budget(tmp_path):
    """Stripping the block from THIS file: the nightly reads judge.sweep_budget_seconds (2400)
    and the default 60 — the sweep.* block is the only thing raising it."""
    wf = parse_workflow(tmp_path / "WORKFLOW.md", _without_sweep_block())
    assert wf.get("sweep", "budget_seconds") is None
    budget = sweep_nightly.nightly_budget(wf)
    assert (budget.seconds, budget.max_mutations) == (2400.0, 60)


def test_without_the_block_or_a_judge_budget_the_nightly_runs_900s_and_60(tmp_path):
    text = re.sub(r"^  sweep_budget_seconds: 2400\n", "", _without_sweep_block(),
                  flags=re.M)
    wf = parse_workflow(tmp_path / "WORKFLOW.md", text)
    assert wf.get("judge", "sweep_budget_seconds") is None
    budget = sweep_nightly.nightly_budget(wf)
    assert (budget.seconds, budget.max_mutations) == (900.0, 60)
