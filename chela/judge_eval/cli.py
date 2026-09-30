"""``chela judge eval`` — the command surface. See ``docs/JUDGE_EVAL.md``.

    chela judge eval [--split train|test] [--risk high,normal,low] [--limit N] [--yes] …
    chela judge eval mine        # rebuild data/cases.jsonl from the repo's PR history
    chela judge eval label       # rubric-label the historical findings (costs model calls)
    chela judge eval controls    # grade the known-label grader fixtures (costs model calls)
    chela judge eval spot-check  # write the operator's 20% spot-check sheet
    chela judge eval agreement   # score a filled-in spot-check sheet
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from chela.judge_eval import dataset as ds

DEFAULT_MODEL = "opus"            # dispatcher.DEFAULT_JUDGE_MODEL — what the live judge runs on
DEFAULT_GRADER_MODEL = "claude-opus-5-5"
SPOT_CHECK_PATH = Path("docs/judge_eval/SPOT_CHECK.md")


def add_parser(judge_sub) -> None:
    p = judge_sub.add_parser(
        "eval",
        help="📏 Offline eval of the judge's experiment DESIGN: seeded/real-regression recall, "
             "contrived-mutation rate and validity per risk level (no suite runs)",
    )
    p.add_argument("--split", choices=(ds.TRAIN, ds.TEST), default=ds.TRAIN,
                   help="Which side of the by-PR split to score (default train). A test run "
                        "prints aggregates ONLY")
    p.add_argument("--risk", default="high,normal,low",
                   help="Comma-separated risk levels to render the judge prompt at")
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="Score only the first N cases (seeded first, then newest PR)")
    p.add_argument("--case", action="append", default=None, metavar="ID",
                   help="Score only this case id (repeatable; must be in --split)")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Model for the design step (default {DEFAULT_MODEL}, the live judge's)")
    p.add_argument("--grader-model", default=DEFAULT_GRADER_MODEL,
                   help=f"Model for the contrived-rate grader (default {DEFAULT_GRADER_MODEL})")
    p.add_argument("--judge-prompt", metavar="PATH",
                   help="A candidate judge prompt template to measure instead of the live "
                        "JUDGE_PROMPT (same {{vars}})")
    p.add_argument("--cases", default=str(ds.CASES_PATH), metavar="PATH",
                   help="Dataset JSONL (default: the shipped chela/judge_eval/data/cases.jsonl)")
    p.add_argument("--out", metavar="PATH",
                   help="Results JSON (default .judge-eval/results-<split>-<utc>.json, gitignored)")
    p.add_argument("--max-cost", type=float, default=50.0, metavar="USD",
                   help="Stop scheduling new cases once this much has been spent (default 50)")
    p.add_argument("--per-call-budget", type=float, default=3.0, metavar="USD",
                   help="--max-budget-usd for each model call (default 3)")
    p.add_argument("--jobs", type=int, default=3, help="Concurrent cases (default 3)")
    p.add_argument("--yes", action="store_true",
                   help="Actually make the model calls. Without it the cost estimate is "
                        "printed and nothing is spent")
    sub = p.add_subparsers(dest="eval_cmd")
    pm = sub.add_parser("mine", help="Rebuild the dataset from this repo's PR history (gh)")
    pm.add_argument("--min-pr", type=int, default=400)
    pm.add_argument("--since", default="2026-07-01T00:00:00Z")
    pl = sub.add_parser("label", help="Rubric-label unlabelled historical findings")
    pl.add_argument("--yes", action="store_true", dest="label_yes")
    pc = sub.add_parser("controls", help="Grade the known-label fixtures (grader positive/"
                                         "negative control) twice and report accuracy + flips")
    pc.add_argument("--yes", action="store_true", dest="controls_yes")
    sub.add_parser("spot-check", help=f"Write the operator's spot-check sheet ({SPOT_CHECK_PATH})")
    pa = sub.add_parser("agreement", help="Score a filled-in spot-check sheet")
    pa.add_argument("--sheet", default=str(SPOT_CHECK_PATH))


def _risks(raw: str) -> tuple[str, ...]:
    from chela.judge_eval.evaluate import RISKS
    levels = tuple(r.strip() for r in raw.split(",") if r.strip())
    bad = [r for r in levels if r not in RISKS]
    if bad or not levels:
        sys.exit(f"--risk: unknown level(s) {bad or raw!r}; choose from {', '.join(RISKS)}")
    return levels


def main(args) -> None:
    from chela.judge_eval import evaluate, mine
    from chela.judge_eval.runner import ClaudeCLIRunner

    repo = ds.repo_root(".")
    cases_path = Path(args.cases)
    cmd = getattr(args, "eval_cmd", None)
    if cmd == "mine":
        cases = mine.mine(repo, min_pr=args.min_pr, since=args.since,
                          existing=ds.load_cases(cases_path))
        ds.save_cases(cases, cases_path)
        print(f"wrote {len(cases)} case(s), {sum(len(c.targets) for c in cases)} target(s) → {cases_path}")
        return
    cases = ds.load_cases(cases_path)
    if cmd == "label":
        todo = [c for c in cases if any(t.source == ds.HISTORICAL and not t.label for t in c.targets)]
        est = evaluate.Estimate(0, len(todo), len(todo) * evaluate.price(args.grader_model,
                                                                          evaluate.GRADER_TOKENS))
        print(est.line())
        if not args.label_yes:
            print("dry run — nothing was spent. Re-run with `label --yes`.")
            return
        grader = ClaudeCLIRunner(args.grader_model, max_budget_usd=args.per_call_budget)
        n, cost = evaluate.label_cases(todo, grader, args.grader_model)
        ds.save_cases(cases, cases_path)
        print(f"labelled {n} finding(s) for ${cost:.2f} → {cases_path}")
        return
    if cmd == "controls":
        controls = evaluate.load_controls()
        est = evaluate.Estimate(0, evaluate.GRADER_RUNS,
                                evaluate.GRADER_RUNS * evaluate.price(args.grader_model,
                                                                      evaluate.GRADER_TOKENS))
        print(f"📏 {len(controls)} grader control fixture(s)")
        print(est.line())
        if not args.controls_yes:
            print("dry run — nothing was spent. Re-run with `controls --yes`.")
            return
        grader = ClaudeCLIRunner(args.grader_model, max_budget_usd=args.per_call_budget)
        res = evaluate.run_controls(grader, controls)
        for r in res["rows"]:
            print(f"  {'✓' if r['ok'] else '✗'} {r['id']:<26} expect {r['expect']:<9} got "
                  f"{r['got']:<9} ({r['tier']}{', FLIPPED' if r['flipped'] else ''})")
        print(f"grader controls: {res['correct']}/{res['n']} correct, {res['flips']} flip(s), "
              f"${res['cost_usd']:.2f}" + (f"; errors: {res['errors']}" if res["errors"] else ""))
        return
    if cmd == "spot-check":
        SPOT_CHECK_PATH.parent.mkdir(parents=True, exist_ok=True)
        header = SPOT_CHECK_PATH.read_text().split("| # |", 1)[0] if SPOT_CHECK_PATH.is_file() else ""
        SPOT_CHECK_PATH.write_text(header + evaluate.spot_check_sheet(cases) + "\n")
        print(f"wrote {SPOT_CHECK_PATH}")
        return
    if cmd == "agreement":
        a = evaluate.agreement(Path(args.sheet).read_text())
        rate = f"{a['rate']:.0%}" if a["rate"] is not None else "n/a"
        print(f"spot-check: {a['filled']}/{a['rows']} row(s) filled, agreement {rate} "
              f"({a['agree']}/{a['filled']}); disagreements: {', '.join(a['disagreements']) or 'none'}")
        return

    risks = _risks(args.risk)
    selected = evaluate.select_cases(cases, args.split, ids=args.case, limit=args.limit)
    template, template_name = None, "live JUDGE_PROMPT"
    if args.judge_prompt:
        template, template_name = Path(args.judge_prompt).read_text(), args.judge_prompt
    runner = ClaudeCLIRunner(args.model, max_budget_usd=args.per_call_budget)
    grader = ClaudeCLIRunner(args.grader_model, max_budget_usd=args.per_call_budget)
    payload = evaluate.run_eval(
        repo, selected, split=args.split, risks=risks, runner=runner, grader_runner=grader,
        model=args.model, grader_model=args.grader_model, confirm=args.yes, template=template,
        template_name=template_name, max_cost=args.max_cost, jobs=args.jobs,
        out=lambda line: print(line, flush=True),
    )
    if payload is None:
        return
    from chela.judge_eval import report
    out = Path(args.out) if args.out else (
        repo / ".judge-eval" / f"results-{args.split}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json")
    report.save(payload, out)
    print(f"\nresults → {out}")
