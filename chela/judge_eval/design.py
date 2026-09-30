"""Run ONLY the judge's experiment-design step for one case at one risk level.

The prompt is the LIVE judge prompt — ``dispatcher.JUDGE_PROMPT`` (or a candidate template
passed in, which is how a prompt change gets measured before it ships) rendered with the
same variables ``dispatcher._judge_vars`` supplies, including CMX-405's per-level
``risk_guidance`` and experiment cap. Only the parts that would reach outside an offline
run are swapped, and each swap is named in :data:`OFFLINE_ADDENDUM`:

* the diff and "what the PR claims" are files in the checkout (``.judge-eval/``) instead of
  ``git diff`` / ``gh pr view --comments`` — the PR's comments would hand the model the very
  findings being scored;
* the experiments come back as the call's structured output instead of a file + ``chela
  judge run``.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from chela.judge_eval import dataset as ds
from chela.workflow import render_prompt

EVAL_DIR = ".judge-eval"

OFFLINE_ADDENDUM = """

## ⚙️ OFFLINE EVAL MODE — this replaces steps 3 and 4 above, nothing else

This is an offline measurement of how you DESIGN experiments; nothing you propose will be
run. The checkout is a read-only copy of this PR's head and you have only Read, Grep and
Glob — there is no shell, so read the diff and the PR's commit messages from the files
named in step 1. Do not try to write a file or run a command: return the experiments object
(the exact `{"experiments": [...], "notes": [...]}` shape from step 3) as your final
structured output. Everything else above — the stakes, the cap, what a good experiment is —
applies unchanged.
"""

EXPERIMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "experiments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "guard": {"type": "string"},
                    "kind": {"type": "string", "enum": ["mutation", "wiring"]},
                    "file": {"type": "string"},
                    "before": {"type": "string"},
                    "after": {"type": "string"},
                    # CMX-395's tag. Accepted so the live prompt's ask can be honoured, and
                    # IGNORED by every score (see evaluate.py) — a held-out experiment is
                    # still an experiment for recall, contrived rate and validity.
                    "held_out": {"type": "boolean"},
                },
                "required": ["guard", "file", "before", "after"],
            },
        },
        "notes": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"title": {"type": "string"}, "body": {"type": "string"}}},
        },
    },
    "required": ["experiments"],
}


def live_template() -> str:
    from chela import dispatcher
    return dispatcher.JUDGE_PROMPT


def design_vars(case: ds.Case, risk: str, wf=None) -> dict:
    """The live judge's variables via ``dispatcher.judge_prompt_vars`` — the SAME builder
    ``dispatcher._judge_vars`` calls, so every key the live prompt renders (CMX-405's cap
    and guidance, CMX-395's held-out quota, anything added later) is here too. Only the
    values that would reach outside an offline run are swapped. ``wf`` is the workflow whose
    ``judge.*`` knobs to read (the repo's own WORKFLOW.md in a CLI run); None = defaults."""
    from chela import dispatcher
    return dispatcher.judge_prompt_vars(
        wf=wf,
        risk=risk,
        task_id=case.id,
        task_title=case.title,
        task_body="",
        branch_name=f"pr-{case.pr}",
        base_branch="dev",
        workspace_path=".",
        repo_path=".",
        project_key="chelamux",
        task_number=case.pr,
        pr_url=f"#{case.pr} ({case.title})",
        head_sha=case.head_sha,
        experiments_path="(offline eval — return the JSON as your structured output)",
        judge_cmd="(offline eval — nothing to run; return the JSON as your structured output)",
        test_cmd="(offline eval — no suite runs)",
        diff_cmd=f"the Read tool on `{EVAL_DIR}/PR_DIFF.patch` (this PR's full diff)",
        pr_view_cmd=f"the Read tool on `{EVAL_DIR}/PR.md` (its title and commit messages)",
    )


def render_design_prompt(case: ds.Case, risk: str, template: str | None = None,
                         wf=None) -> str:
    return render_prompt(template or live_template(), design_vars(case, risk, wf)) + OFFLINE_ADDENDUM


@dataclass
class Design:
    experiments: list[dict] = field(default_factory=list)
    notes: list = field(default_factory=list)
    cost_usd: float = 0.0
    error: str = ""


def prepare_tree(repo: Path, case: ds.Case, dest: Path, full_diff: str) -> None:
    ds.materialize(repo, case.head_sha, dest)
    d = dest / EVAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "PR_DIFF.patch").write_text(full_diff)
    (d / "PR.md").write_text(f"# {case.title}\n\n## Commit messages\n\n"
                             f"{ds.commit_messages(repo, case.base_sha, case.head_sha)}")


def run_design(repo: Path, case: ds.Case, risk: str, runner, full_diff: str,
               template: str | None = None, wf=None) -> Design:
    """One design call. The tree is a throwaway extraction, deleted afterwards."""
    tmp = Path(tempfile.mkdtemp(prefix=f"chela-judge-eval-{case.id}-"))
    try:
        prepare_tree(repo, case, tmp, full_diff)
        res = runner.run(render_design_prompt(case, risk, template, wf), cwd=tmp,
                         schema=EXPERIMENTS_SCHEMA)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if res.error:
        return Design(cost_usd=res.cost_usd, error=res.error)
    raw = res.structured or {}
    exps = [e for e in raw.get("experiments") or [] if isinstance(e, dict)]
    return Design(experiments=exps, notes=raw.get("notes") or [], cost_usd=res.cost_usd)
