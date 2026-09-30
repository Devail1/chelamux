# The judge eval (CMX-408)

`chela judge eval` measures **how well the judge designs its experiments**, so that a change
to the judge prompt can be measured instead of felt.

The judge has two halves. The mechanical half applies each mutation, proves it applied,
runs the suite, and blocks on a survivor. It is deterministic and already has its own tests
(`chela/judge.py`). The LLM half decides **which** mutations to try, and until this eval
nothing measured it. The operator's sense that the judge was "too harsh" (#529, #546 and
#547 each went 4–5 rounds on test-only findings) and CMX-405's per-level prompt
(`docs/RISK_LEVELS.md`) both change that half. This eval is how to tell whether a change
helped.

⛔ **What it never does:** run a test suite, change `chela/judge.py`'s behaviour, read or
write `~/.chela`, or talk to the daemon or dashboard. It runs **only the design step**:
the live `JUDGE_PROMPT` plus `RISK_GUIDANCE`, rendered for one risk level, with the model
returning its experiments JSON.

The method follows the claude.dev post "Automating eval design and hillclimbing": build the
cases from real history, use the cheapest grader that works, check that the grader is
consistent, split train/test, and never paste test-set failures into the thing being tuned.

## Quick start

```bash
chela judge eval                     # dry run: prints the call count and the cost estimate
chela judge eval --limit 6 --yes     # score 6 train cases at all three risk levels
chela judge eval --risk low --limit 3 --yes
chela judge eval --judge-prompt candidate.md --yes   # measure a candidate prompt
chela judge eval --split test --yes  # the held-out score: aggregates only
```

Without `--yes` the command spends nothing. It prints the number of model calls and an
upper-bound cost estimate, and then stops. The estimate is always printed before the first
call. A confirmed run also stops scheduling new cases once it reaches `--max-cost` (default
$50), and each call is capped by its own `--per-call-budget` (default $3). One design call
plus two grader calls cost about **$1 per case per risk level** with the default Opus
models, so a full train run (60 cases × 3 levels) costs about $180.

Results go to `.judge-eval/results-<split>-<utc>.json`, which is gitignored. Pass `--out` to
save them somewhere else.

## What it scores

For each case, at each risk level, the model proposes experiments. **Only the first *cap*
of them are scored**, where the cap is the level's `judge_experiments_<level>` setting
(12 / 8 / 4 by default). Those are the experiments the live judge would actually run.

| score | grader | what counts |
|---|---|---|
| **recall, seeded** | programmatic | a *valid* experiment that **changes a line** the seeded regression changes, in the same file |
| **recall, historical real** | programmatic | the same test, run against every surviving historical finding labelled `real` |
| **negative control** | programmatic | the same test, run against a **decoy**: a line in a file the PR never touches. The rate must stay near 0%; if it doesn't, the matcher is broken |
| **contrived rate** | programmatic first, then an LLM with checkable claims | the share of graded experiments that the rubric labels contrived |
| **validity** | programmatic: the judge's own `apply_mutation` and `parse_check` | the experiment would apply: its anchor occurs exactly once, the edit changes something, and the file still parses |
| **grader consistency** | — | every LLM-graded case is graded **twice**. A label that differs between the two runs counts as a flip, and flips are reported |

Recall is a line-overlap test (`grade.changed_lines`), not a string match. An experiment on
the line next to the weakness is a miss, the same edit in another file is a miss, and an
experiment the judge would refuse (an ambiguous anchor, a parse break) can never score a
hit.

**Held-out tags (CMX-395) do not change any score.** The live prompt asks the judge to tag
about `held_out_pct`% of its experiments `"held_out": true`. A held-out experiment is still
one the live judge runs, so the eval scores it exactly like a visible one: it counts toward
the cap, recall, the contrived rate and validity. The tag is only echoed in the per-case
detail. (`test_a_held_out_tag_changes_no_score` is the guard.)

**The prompt variables are the live judge's, by construction.** The eval renders the design
prompt through `dispatcher.judge_prompt_vars`, the same builder `dispatcher._judge_vars`
calls, and reads the `judge.*` knobs (e.g. `held_out_fraction`) from the repo's own
`WORKFLOW.md`. A variable added to the live prompt reaches the eval without a second edit.

Every rate comes with a 95% interval. Seeded recall uses a Wilson interval, since each case
has one target. The pooled rates use a cluster bootstrap that resamples **cases**, because
experiments written in the same model call are correlated.

### The contrived grader

The rubric is `chela/judge_eval/rubric.md`. The grader reads it verbatim, and it is also
the standard the operator spot-checks against. In short, a mutation is **realistic** when a
plausible future edit could produce the same breakage, and **contrived** when only a
targeted corruption aimed at one test would.

- **Tier 1, programmatic** (`grade.classify_programmatic`) decides the shapes that settle
  the question on their own. A single flipped operator or negation, an emptied value, a
  commented-out statement, and a short-circuited guard are realistic. A new comparison
  against a literal is `special_case_injection`, which is contrived.
- **Tier 2, LLM** gets everything else, batched as one call per case. The grader never
  gives a 1–5 score. For each experiment it answers `shape` (an enum), `ordinary_edit`,
  `requires_test_knowledge` and `main_path`, and quotes the code it judged. **The quote is
  checked against `before`**, and an answer whose quote isn't there counts as *ungraded*,
  not guessed. The label is then derived from those claims by code
  (`grade.label_from_claims`), not by the model.
- **Grader controls** (`chela judge eval controls --yes`): 8 fixtures with known labels,
  4 realistic and 4 contrived (`data/grader_controls.jsonl`), each graded twice. This is
  the grader's own positive and negative control. The baseline result was **8/8 correct,
  0 flips, $0.09**.

## The dataset

`chela/judge_eval/data/cases.jsonl` holds 89 cases:

- **85 historical cases** (`chela judge eval mine`). Every PR from #400 on that has a
  blocked or clean judge verdict gets one case, pinned to the **first** verdict's head.
  That head is the PR's newest commit at or before the verdict comment. It comes before any
  rework round could reshape the code around the findings. Each surviving mutation quoted
  in a blocked verdict becomes a target, and the miner parses it back out of
  `judge.block_body`'s own rendering. Of those, 61 cases were blocked, carrying 174
  findings, and 24 were clean.
- **4 seeded cases** (`data/seeds.jsonl`). Each is a merged PR's squash commit with one
  known real regression from history as its target, plus a decoy:

  | seed | PR | the regression |
  |---|---|---|
  | `canvas-grid-row` | #529 | `.canvas { … grid-row: 2 }`, which put the Wall off-screen |
  | `transcript-pr-url-over-row` | #544 | trusting a transcript's `pr_url` over the run row's (CMX-391) |
  | `hook-launch-child-env` | #542 | a workflow hook launched without `env=child_env()` (CMX-390) |
  | `sandbox-check-cached` | #554 | caching the sandbox verdict instead of re-checking it (CMX-403) |

Diffs and file contents are **not stored**. They are rebuilt from git when the eval runs:
`git archive` of the head, and `git diff base...head`. If a squash-merged branch's commits
are no longer local, the eval fetches `pull/<N>/head` (into FETCH_HEAD only; no ref is
created). The model reads a copy of the tree that has no `.git` and no shell. "What the PR
claims" comes from its **commit messages up to the judged head**. It deliberately does not
use the PR body or comments, since either can be edited after the verdict and would leak the
answer.

⛔ **Public repo.** The dataset holds only this repo's own public PR titles, file paths and
code excerpts. The miner **drops** any finding that looks private (a home-directory path, a
session link, a token-shaped string) rather than scrubbing it. A test checks the shipped
files for all of these.

### Labels

`chela judge eval label --yes` labels every historical finding **real** or **contrived**
with the same rubric grader (one call per case; about $2.30 for all 174). The result:
**158 real, 15 contrived, 1 ungraded.** 46 findings were decided programmatically and 127 by
the LLM. By the rubric, about 91% of the findings the judge has blocked on were realistic
regressions, not contrived ones. That is worth knowing before tuning for "too harsh". The
cost of those rounds may come from how many findings the judge produced, not from their
realism.

#### Label agreement

`docs/judge_eval/SPOT_CHECK.md` is a random 20% sample of the train-split labels: 26 rows,
seed 408. The operator fills in the `operator` column, and `chela judge eval agreement`
scores the sheet.

**Agreement: pending.** The operator has not filled in the sheet yet. Record the result
here when they do.

## The split

`dataset.split_for(pr)` sends about 30% of PRs to `test` using a salted hash **of the PR
number**, so every round and every seed built on one PR lands on the same side. The split
is stored on each row for readability. A test checks that every stored split matches the
computed one, and `select_cases` ignores the stored value anyway. The current split is 60
train and 29 test cases, and 3 of the 4 seeds are in train.

⛔ **A test run shows aggregates only.** It prints no case id, no experiment and no miss, in
the progress lines, the summary or the results file. Whoever tunes the prompt tunes against
train, and must never see which test case failed or why. The spot-check sheet draws from
train only for the same reason. Don't open test rows in `cases.jsonl` while tuning. And
don't change `SPLIT_SALT` after anyone has seen test results: that re-deals the split and
contaminates it.

## Baseline

The live `JUDGE_PROMPT` at `056ca48` (CMX-405), before CMX-395 added the held-out ask to
it, with `opus` for design and
`claude-opus-5-5` as the grader. The runs used `--limit`, so they are **small samples and
the intervals are wide**. A full run is the operator's call.

**Train, `--limit 6`** (3 seeds and PRs #554, #558, #559, each at three levels; 18 design
calls; **$13.17** spent against a ≤ $21.24 estimate). Per-case detail is in
`docs/judge_eval/baseline-train.json`.

| risk | seeded recall | historical-real recall | decoy hits (neg. control) | contrived rate | validity | grader flips |
|---|---|---|---|---|---|---|
| high (cap 12) | 67% [21–94] (2/3) | 93% (13/14) | 0/3 | 9% [0–20] (6/70) | 100% (72/72) | 3 / 53 |
| normal (cap 8) | 67% [21–94] (2/3) | 14% (2/14) | 0/3 | 2% [0–7] (1/46) | 100% (46/46) | 1 / 39 |
| low (cap 4) | 67% [21–94] (2/3) | 7% (1/14) | 0/3 | 0% (0/23) | 96% (22/23) | 0 / 21 |

**Test, `--limit 4`** (aggregates only; 12 design calls; **$5.94**).
`docs/judge_eval/baseline-test.json` carries no per-case rows.

| risk | seeded recall | historical-real recall | decoy hits | contrived rate | validity | grader flips |
|---|---|---|---|---|---|---|
| high | 1/1 | 3/3 | 0/1 | 5% [0–15] (2/44) | 100% (44/44) | 4 / 43 |
| normal | 1/1 | 2/3 | 0/1 | 7% [0–21] (2/29) | 100% (29/29) | 0 / 28 |
| low | 0/1 | 2/3 | 0/1 | 0% (0/15) | 100% (15/15) | 1 / 14 |

Grader consistency across both samples is **9 flips over 198 LLM-graded experiments
(4.5%)**. That noise floor is worth knowing before reading a contrived-rate difference of a
few points as real.

What the sample shows, with small-sample caveats:

- **The risk level works as intended on the contrived rate.** It falls from 9% at `high` to
  2% at `normal` to 0% at `low`.
- **Recall of historical real findings drops much further.** It goes from 93% to 14% to 7%.
  12 of the 14 real targets belong to one PR (#554, 12 findings), and a cap of 4 or 8 simply
  cannot reach 12 separate weak spots. So at `normal` and `low`, the judge misses most of
  the real gaps its `high` battery would have found. That trade-off is the one to watch
  when tuning. Because the sample is dominated by that one PR, its bootstrap interval is
  degenerate and is left out of the table; see the results file.
- **Seeded recall is the same at every level.** The sandbox-recheck and transcript-`pr_url`
  seeds are hit at all three levels. The `.canvas grid-row` seed is missed at all three.
  At every level the plans mutated the `.app` grid template (and at `normal`, `.sidebar`'s
  grid row), but never the `.canvas` placement itself.
- **Validity is almost perfect.** 140 of 141 experiments applied cleanly, so experiment
  design is not what causes `INVALID` rounds.

## Assumptions and limits

- The model call goes through `claude -p`, the same harness the live judge runs in, locked
  down with `--restricted --tools Read,Grep,Glob`, strict MCP and no session persistence.
  It does not use the Anthropic SDK, so chela gains no new dependency or key handling.
  Consequence: `claude` has to be installed and logged in.
- The design step sees a tree **without** the base branch merged in. The live judge first
  merges `origin/<base>` (CMX-176), so the trees can differ slightly for PRs that were
  behind their base.
- A seeded case's tree is the merged head, so it includes the fix and any defeat-shape entry
  that describes it. The seed measures whether the plan **targets** the weak spot, not
  whether the plan could discover the bug.
- A historical target is real only by the rubric label. The recall denominator excludes
  contrived-labelled findings, so rubric errors move it. The operator spot-check is the
  independent check on that.
- **No prompt change is made here.** This task builds the yardstick and the baseline.
  Tuning is a follow-up for the operator to decide on.
