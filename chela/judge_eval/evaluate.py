"""The eval loop: estimate → (confirm) → design → score → aggregate. Also the labelling pass
and the operator spot-check sheet.

⛔ COST CAP. :func:`estimate` is printed BEFORE any model call, always; without
``confirm=True`` (``--yes`` on the CLI) nothing is spent at all — the estimate IS the dry
run. A confirmed run still stops scheduling new cases once ``max_cost`` is reached, and
every call carries its own ``--max-budget-usd``.
"""
from __future__ import annotations

import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from chela import config
from chela.judge_eval import dataset as ds
from chela.judge_eval import design, grade, report

RISKS = ("high", "normal", "low")

# $/MTok (input, output), Anthropic list prices (cached 2026-09-25). An alias resolves to
# the model it names today; an unknown model is priced as Opus so the estimate errs HIGH.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0), "fable": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0), "opus": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0), "sonnet": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0), "haiku": (1.0, 5.0),
}
# Per-call token budgets for the estimate. The design call is agentic (it reads the diff and
# the files it targets), so its input is priced as if none of it were cached — an upper
# bound; the real per-call cost the CLI reports is in the results.
DESIGN_TOKENS = (150_000, 15_000)
GRADER_TOKENS = (15_000, 4_000)
GRADER_RUNS = 2          # grader consistency: every LLM-graded case is graded twice


def price(model: str, tokens: tuple[int, int]) -> float:
    pin, pout = PRICES.get(model, PRICES["opus"])
    return (tokens[0] * pin + tokens[1] * pout) / 1_000_000


@dataclass
class Estimate:
    design_calls: int
    grader_calls: int
    usd: float

    @property
    def calls(self) -> int:
        return self.design_calls + self.grader_calls

    def line(self) -> str:
        return (f"💰 {self.calls} model call(s) — {self.design_calls} design + "
                f"{self.grader_calls} grader (upper bound: every case needs the LLM grader, "
                f"twice) — estimated ≤ ${self.usd:.2f} at list price")


def estimate(n_cases: int, n_risks: int, model: str, grader_model: str,
             grader_runs: int = GRADER_RUNS) -> Estimate:
    d = n_cases * n_risks
    g = d * grader_runs
    return Estimate(d, g, d * price(model, DESIGN_TOKENS) + g * price(grader_model, GRADER_TOKENS))


def select_cases(cases: list[ds.Case], split: str, *, ids: list[str] | None = None,
                 limit: int | None = None) -> list[ds.Case]:
    """``split``'s cases — seeded first (they carry the recall targets), then newest PR
    first — narrowed by ``ids`` and ``limit``. ⛔ Never returns a case of the other split."""
    pool = [c for c in cases if c.split == split and ds.split_for(c.pr) == split]
    if ids:
        pool = [c for c in pool if c.id in set(ids)]
    pool.sort(key=lambda c: (c.kind != ds.SEEDED, -c.pr, c.id))
    return pool[:limit] if limit else pool


def score_case(repo: Path, case: ds.Case, risk: str, runner, grader_runner,
               template: str | None = None, wf=None) -> report.CaseResult:
    """⚖️🙈 CMX-395's ``held_out`` tag is IGNORED by every score here: a held-out experiment
    is still an experiment the live judge runs, so it counts toward the cap, recall,
    contrived rate and validity exactly like a visible one (it is only echoed in the detail)."""
    cap = config.judge_max_experiments(risk)
    r = report.CaseResult(case_id=case.id, pr=case.pr, kind=case.kind, split=case.split,
                          risk=risk, cap=cap)
    if not ds.ensure_commit(repo, case.head_sha, case.pr) or not ds.ensure_commit(repo, case.base_sha):
        r.error = f"head {case.head_sha[:12]} or base not reachable"
        return r
    full_diff = ds.diff(repo, case.base_sha, case.head_sha)
    d = design.run_design(repo, case, risk, runner, full_diff, template, wf)
    r.cost_usd += d.cost_usd
    if d.error:
        r.error = d.error
        return r
    r.proposed = len(d.experiments)
    considered = d.experiments[:cap]           # what the live judge would actually RUN
    r.considered = len(considered)
    contents: dict[str, str | None] = {}
    for p in {grade._norm_path(e.get("file", "")) for e in considered} | \
             {grade._norm_path(t.file) for t in case.targets}:
        contents[p] = ds.show_file(repo, case.head_sha, p) if p else None
    valid_exps = []
    detail = []
    grades, gcost, gerrors = grade.grade_contrived(considered, grader_runner, full_diff)
    r.cost_usd += gcost
    for exp, g in zip(considered, grades):
        ok, why = grade.check_validity(exp, contents.get(grade._norm_path(exp.get("file", ""))))
        r.valid += ok
        if ok:
            valid_exps.append(exp)
        r.contrived += g.label == grade.CONTRIVED
        r.realistic += g.label == grade.REALISTIC
        r.ungraded += g.label == grade.UNGRADED
        r.llm_graded += g.tier == "llm"
        r.flips += g.flipped
        detail.append({**{k: exp.get(k, "") for k in ("guard", "kind", "file", "before", "after")},
                       "held_out": bool(exp.get("held_out")),
                       "valid": ok, "validity": why, "label": g.label, "shape": g.shape,
                       "grade_reason": g.reason, "grade_tier": g.tier, "flipped": g.flipped})
    for t in case.targets:
        hit = grade.target_hit(t, valid_exps, contents)
        if t.source == ds.DECOY:            # the negative control — a hit here is a BUG
            r.decoy_targets += 1
            r.decoy_hits += hit
            continue
        if t.source == ds.SEEDED:
            r.seeded_targets += 1
            r.seeded_hits += hit
        elif t.is_real:
            r.real_targets += 1
            r.real_hits += hit
        else:
            continue
        if not hit:
            r.misses.append(t.id)
    r.experiments = detail
    if gerrors:
        r.experiments.append({"grader_errors": gerrors})
    return r


def run_eval(repo: Path, cases: list[ds.Case], *, split: str, risks: tuple[str, ...],
             runner, grader_runner, model: str, grader_model: str, confirm: bool,
             template: str | None = None, template_name: str = "live JUDGE_PROMPT",
             max_cost: float = 50.0, jobs: int = 3, out=print, wf=None) -> dict | None:
    """The whole eval over already-selected ``cases``. Returns the results payload, or None
    for a dry run (``confirm`` false) — in which case NO model call was made."""
    est = estimate(len(cases), len(risks), model, grader_model)
    out(f"📏 {len(cases)} {split} case(s) × {len(risks)} risk level(s) ({', '.join(risks)})")
    out(est.line())
    if not confirm:
        out("dry run — nothing was spent. Re-run with --yes to make these calls.")
        return None
    if not cases:
        out("no cases selected.")
        return None
    jobs_list = [(c, rk) for c in cases for rk in risks]
    results: list[report.CaseResult] = []
    spent = 0.0
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        pending = iter(jobs_list)
        futures = {}

        def _submit():
            nxt = next(pending, None)
            if nxt is not None:
                futures[pool.submit(score_case, repo, nxt[0], nxt[1], runner, grader_runner,
                                    template, wf)] = nxt
            return nxt is not None

        for _ in range(max(1, jobs)):
            _submit()
        done_n = 0
        while futures:
            fut = next(as_completed(futures))
            futures.pop(fut)
            res = fut.result()
            results.append(res)
            spent += res.cost_usd
            done_n += 1
            # ⛔ progress never names a test case (report.py's split rule)
            label = f"{res.case_id} @ {res.risk}" if split == ds.TRAIN else f"case @ {res.risk}"
            out(f"  [{done_n}/{len(jobs_list)}] {label}  ${res.cost_usd:.2f}  (total ${spent:.2f})")
            if spent < max_cost:
                _submit()
            elif not futures and done_n < len(jobs_list):
                out(f"⛔ stopped at the ${max_cost:.2f} budget: "
                    f"{len(jobs_list) - done_n} case-level(s) not run")
    summary = report.aggregate(results)
    meta = {"model": model, "grader_model": grader_model, "template": template_name,
            "risks": list(risks), "cases": len(cases), "estimate_usd": round(est.usd, 2),
            "spent_usd": round(spent, 4), "rubric": grade.RUBRIC_VERSION}
    out("")
    out(report.render(summary, split, results, meta))
    return report.results_payload(summary, split, results, meta)


# --- labelling historical findings -------------------------------------------------


def label_cases(cases: list[ds.Case], grader_runner, grader_model: str, *,
                out=print) -> tuple[int, float]:
    """Label every unlabelled historical target ``real``/``contrived`` with the rubric grader
    (programmatic tier, then one LLM call per case). Returns (labelled, cost)."""
    n, cost = 0, 0.0
    for c in cases:
        todo = [t for t in c.targets if t.source == ds.HISTORICAL and not t.label]
        if not todo:
            continue
        exps = [{"guard": t.guard, "file": t.file, "kind": t.kind, "before": t.before,
                 "after": t.after} for t in todo]
        grades, gc, errs = grade.grade_contrived(exps, grader_runner, "(historical finding — "
                                                 "judge the mutation on its own)", runs=1)
        cost += gc
        for t, g in zip(todo, grades):
            if g.label == grade.UNGRADED:
                continue
            t.label = ds.REAL if g.label == grade.REALISTIC else ds.CONTRIVED
            t.label_shape, t.label_rationale = g.shape, g.reason
            t.label_by = f"{grade.RUBRIC_VERSION}:{g.tier}" + (
                f":{grader_model}" if g.tier == "llm" else "")
            n += 1
        out(f"  {c.id}: {sum(1 for t in todo if t.label)}/{len(todo)} labelled"
            + (f" (grader errors: {errs})" if errs else ""))
    return n, cost


# --- the operator's spot-check -----------------------------------------------------

SPOT_FRACTION = 0.2
SPOT_SEED = 408
_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|.*\|\s*(real|contrived)\s*\|\s*([^|]*?)\s*\|\s*([^|]*)\|\s*$")


def _cell(s: str, limit: int = 160) -> str:
    s = s.replace("|", "\\|").replace("\n", " ⏎ ").replace("`", "'")
    return s if len(s) <= limit else s[:limit] + "…"


def spot_check_sheet(cases: list[ds.Case]) -> str:
    """A random SPOT_FRACTION of the labelled historical targets — TRAIN cases only, so the
    operator never reads a test case's answer — as a markdown table to fill in."""
    pool = [t for c in cases if c.split == ds.TRAIN for t in c.targets
            if t.source == ds.HISTORICAL and t.label]
    rng = random.Random(SPOT_SEED)
    k = max(1, round(len(pool) * SPOT_FRACTION)) if pool else 0
    sample = sorted(rng.sample(pool, k), key=lambda t: t.id) if k else []
    lines = [
        "| # | target | file | guard | before → after | Claude | operator | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(sample, 1):
        lines.append(f"| {i} | `{t.id}` | `{_cell(t.file, 60)}` | {_cell(t.guard, 120)} | "
                     f"'{_cell(t.before)}' → '{_cell(t.after)}' | {t.label} |  |  |")
    return "\n".join(lines)


def agreement(sheet: str) -> dict:
    """Agreement between the rubric labels and the operator's column, over filled rows."""
    rows = filled = agree = 0
    disagreements = []
    for line in sheet.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        rows += 1
        claude_label, op = m.group(3), m.group(4).strip().lower()
        if op not in (ds.REAL, ds.CONTRIVED):
            continue
        filled += 1
        if op == claude_label:
            agree += 1
        else:
            disagreements.append(m.group(2))
    return {"rows": rows, "filled": filled, "agree": agree,
            "rate": (agree / filled) if filled else None,
            "ci95": list(report.wilson(agree, filled) or []) or None,
            "disagreements": disagreements}


# --- grader controls: does the LLM grader agree with fixtures whose label is known? ---

CONTROLS_PATH = ds.DATA_DIR / "grader_controls.jsonl"


def load_controls(path: Path = CONTROLS_PATH) -> list[dict]:
    import json
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def run_controls(grader_runner, controls: list[dict], *, runs: int = GRADER_RUNS) -> dict:
    """Grade the fixture set ``runs`` times. Positive AND negative controls: each fixture's
    ``expect`` is realistic or contrived, and a grader that calls everything one way fails
    half of them."""
    exps = [{k: c[k] for k in ("guard", "file", "kind", "before", "after")} for c in controls]
    grades, cost, errors = grade.grade_contrived(exps, grader_runner, "(grader control fixture — "
                                                 "judge the mutation on its own)", runs=runs)
    rows = [{"id": c["id"], "expect": c["expect"], "got": g.label, "tier": g.tier,
             "flipped": g.flipped, "ok": g.label == c["expect"]} for c, g in zip(controls, grades)]
    ok = sum(r["ok"] for r in rows)
    return {"n": len(rows), "correct": ok, "ci95": list(report.wilson(ok, len(rows)) or []),
            "flips": sum(r["flipped"] for r in rows), "rows": rows, "cost_usd": round(cost, 4),
            "errors": errors}
