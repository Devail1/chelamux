"""Aggregate per-case scores into per-risk-level baselines with confidence intervals, and
render them — without ever letting a TEST case's details out.

⛔ THE SPLIT RULE, enforced here and nowhere else: a result set is rendered and saved at the
granularity its split allows. ``train`` shows every case, every experiment, every miss —
that is what tuning reads. ``test`` shows AGGREGATES ONLY: no case id, no experiment, no
miss. Whoever tunes the prompt must never see which test case failed or why, or the test
set stops measuring generalisation and starts being trained on.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chela.judge_eval import dataset as ds


@dataclass
class CaseResult:
    case_id: str
    pr: int
    kind: str
    split: str
    risk: str
    cap: int = 0
    proposed: int = 0              # experiments the model proposed
    considered: int = 0            # the first `cap` of them — what the live judge would RUN
    valid: int = 0
    contrived: int = 0
    realistic: int = 0
    ungraded: int = 0
    flips: int = 0                 # LLM-graded experiments whose two grader runs disagreed
    llm_graded: int = 0
    seeded_targets: int = 0
    seeded_hits: int = 0
    real_targets: int = 0
    real_hits: int = 0
    decoy_targets: int = 0         # the negative control (dataset.DECOY) — must stay ~0 hits
    decoy_hits: int = 0
    cost_usd: float = 0.0
    error: str = ""
    experiments: list[dict] = field(default_factory=list)   # train only; see module doc
    misses: list[str] = field(default_factory=list)          # target ids not reached


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for k/n — well-behaved at 0/n and n/n, unlike the normal one."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def cluster_bootstrap(pairs: list[tuple[int, int]], iters: int = 2000,
                      seed: int = 408) -> tuple[float, float] | None:
    """95% CI for sum(k)/sum(n) resampling CASES, not items: experiments within one case are
    correlated (one model call wrote them all), so an item-level interval would be too tight."""
    pairs = [(k, n) for k, n in pairs if n > 0]
    if not pairs:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(iters):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        stats.append(sum(k for k, _ in sample) / sum(n for _, n in sample))
    stats.sort()
    return stats[int(0.025 * iters)], stats[min(iters - 1, int(0.975 * iters))]


def _rate(k: int, n: int, ci) -> dict:
    return {"k": k, "n": n, "rate": (k / n) if n else None,
            "ci95": list(ci) if ci else None}


def aggregate(results: list[CaseResult]) -> dict:
    """Per risk level: recall (seeded, historical-real), contrived rate, validity, grader flips."""
    out = {}
    for risk in sorted({r.risk for r in results}):
        rs = [r for r in results if r.risk == risk and not r.error]
        sk, sn = sum(r.seeded_hits for r in rs), sum(r.seeded_targets for r in rs)
        rk, rn = sum(r.real_hits for r in rs), sum(r.real_targets for r in rs)
        dk, dn = sum(r.decoy_hits for r in rs), sum(r.decoy_targets for r in rs)
        graded = [(r.contrived, r.contrived + r.realistic) for r in rs]
        valid = [(r.valid, r.considered) for r in rs]
        out[risk] = {
            "cases": len(rs),
            "errors": sum(1 for r in results if r.risk == risk and r.error),
            "recall_seeded": _rate(sk, sn, wilson(sk, sn)),
            "recall_real": _rate(rk, rn, cluster_bootstrap(
                [(r.real_hits, r.real_targets) for r in rs])),
            "decoy_hits": _rate(dk, dn, wilson(dk, dn)),
            "contrived_rate": _rate(sum(k for k, _ in graded), sum(n for _, n in graded),
                                    cluster_bootstrap(graded)),
            "validity": _rate(sum(k for k, _ in valid), sum(n for _, n in valid),
                              cluster_bootstrap(valid)),
            "ungraded": sum(r.ungraded for r in rs),
            "grader_flips": {"flips": sum(r.flips for r in rs),
                             "llm_graded": sum(r.llm_graded for r in rs)},
            "mean_proposed": (sum(r.proposed for r in rs) / len(rs)) if rs else None,
            "cost_usd": round(sum(r.cost_usd for r in results if r.risk == risk), 4),
        }
    return out


def _fmt(m: dict) -> str:
    if not m["n"]:
        return "   n/a (n=0)"
    ci = m["ci95"]
    ci_s = f" [{ci[0]:.0%}–{ci[1]:.0%}]" if ci else ""
    return f"{m['rate']:>5.0%}{ci_s} ({m['k']}/{m['n']})"


def render(summary: dict, split: str, results: list[CaseResult], meta: dict) -> str:
    lines = [f"📏⚖️ judge eval — split={split}, model={meta.get('model')}, "
             f"grader={meta.get('grader_model')}, template={meta.get('template')}", ""]
    for risk, m in summary.items():
        flips = m["grader_flips"]
        lines += [
            f"risk {risk}: {m['cases']} case(s)" + (f", {m['errors']} errored" if m["errors"] else ""),
            f"  recall (seeded regressions)   {_fmt(m['recall_seeded'])}",
            f"  recall (historical real)      {_fmt(m['recall_real'])}",
            f"  negative control (decoy hits) {_fmt(m['decoy_hits'])}   must stay ~0%",
            f"  contrived rate                {_fmt(m['contrived_rate'])}   ungraded: {m['ungraded']}",
            f"  validity (applies cleanly)    {_fmt(m['validity'])}",
            f"  grader consistency            {flips['flips']} flip(s) over {flips['llm_graded']} LLM-graded",
            f"  cost                          ${m['cost_usd']:.2f}",
            "",
        ]
    if split == ds.TRAIN:
        lines.append("per case:")
        for r in sorted(results, key=lambda r: (r.case_id, r.risk)):
            if r.error:
                lines.append(f"  {r.case_id:<34} {r.risk:<6} ERROR {r.error[:90]}")
                continue
            lines.append(
                f"  {r.case_id:<34} {r.risk:<6} proposed {r.proposed:>2} valid {r.valid}/{r.considered} "
                f"contrived {r.contrived}/{r.contrived + r.realistic} "
                f"seeded {r.seeded_hits}/{r.seeded_targets} real {r.real_hits}/{r.real_targets}"
                + (f"  missed: {', '.join(r.misses)}" if r.misses else ""))
    else:
        lines.append("⛔ test split: aggregates only — per-case results are never shown for test "
                     "cases (docs/JUDGE_EVAL.md → The split).")
    return "\n".join(lines)


def results_payload(summary: dict, split: str, results: list[CaseResult], meta: dict) -> dict:
    payload = {"meta": {**meta, "split": split}, "summary": summary}
    if split == ds.TRAIN:
        payload["cases"] = [asdict(r) for r in results]
    return payload


def save(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
