# Task risk levels (CMX-405)

Every dispatched task carries a **risk level**: `high`, `normal` or `low`. The level
changes **how widely the judge searches** and **how many rework rounds** the run gets —
and nothing else.

⛔ **What risk never changes:** the judge always runs, it always runs the full suite, every
experiment it does run is adjudicated exactly as before, and a mutation that **SURVIVES
blocks the PR at every level**. Risk is never a way to downgrade a proven survivor to a
note — it only sizes the battery.

## Setting it

The level comes **only from the tracker** — never from the agent working the task.

| Tracker | How |
|---|---|
| markdown (`TODO.md`) | a `<!-- risk: high -->` / `<!-- risk: normal -->` / `<!-- risk: low -->` marker on the bullet line |
| `gh_issues` | a `risk:high` / `risk:normal` / `risk:low` label (several → the highest) |

Like every `<!-- ... -->` marker it is stripped from the bare title, so adding, changing
or removing it **does not change the task id** (CMX-384) and never breaks a `depends:`
reference to the task. A marker quoted inside inline code is prose, not a marker. An
unrecognised level is ignored (logged), never guessed.

**Default:** `normal`. **Fallback when unmarked:** a brief whose `BOUNDARIES` paragraph
touches `dispatcher.py`, `judge.py`, `contract.py`, `mergegate.py`, `inbox.py`, anything
`sandbox`, or anything `secret`/`token` is treated as `high`. The dispatcher logs the
inference at claim time, and the run row records why (`risk_reason`: `marker`, `label`,
`inferred: …`, or `default`). An explicit marker always wins over the inference.

The level is copied onto the run row when the task is claimed, and shown on the Work board
card, in the task modal, and in the judge's verdict header (`risk: low — 4 experiments`).

## What each level buys

| Level | Judge experiments run | Judge is told to aim at | Rework cap |
|---|---|---|---|
| `high` | 12 (the pre-CMX-405 battery) | every guard, adversarial edge cases included | 5 |
| `normal` | 8 | realistic regressions and wiring | 4 |
| `low` | 4 | only regressions a plausible future edit would introduce | 3 |

Proposals past the cap are dropped **out loud** (the verdict says how many). Reaching the
rework cap escalates to `needs_human` exactly as before.

## Knobs

All are Settings → Dispatch knobs (env var, then `~/.chela/config.json`, then default),
read per call:

| Knob | Env | Default |
|---|---|---|
| `judge_experiments_high` / `_normal` / `_low` | `CHELA_JUDGE_EXPERIMENTS_HIGH` / `_NORMAL` / `_LOW` | 12 / 8 / 4 |
| `max_reworks_high` / `_normal` / `_low` | `CHELA_MAX_REWORKS_HIGH` / `_NORMAL` / `_LOW` | 5 / 4 / 3 |
| `max_reworks` | `CHELA_MAX_REWORKS` | 5 — the **ceiling** over every level; `0` still disables the rework loop |

A run with no recorded level (claimed before this existed, or adopted with `chela adopt`)
is `normal`.

## The judge wall scales with the battery (CMX-431)

A bigger battery takes longer, so the daemon's judge watchdog no longer uses a flat
60-minute wall for a run that has started. The wall is

```
base + experiments × per-experiment budget + confirmations × full-suite budget
```

clamped between the old flat 60 minutes (a small battery's wall is exactly what it was) and
a hard ceiling. A *confirmation* is a full-suite re-run of a subset survivor (CMX-407); its
budget is 1.5 × the run's own measured baseline. The run reports its progress (`k/N`, when
it last moved, its baseline duration, its confirmations) to
`$CHELA_DIR/judge-logs/<task>.json`, and the watchdog reads it:

- inside the wall, a run is never reaped;
- past the wall, a run is **stuck, not thinking** only if its progress has not moved for
  the per-experiment budget (or one full-suite run, if longer); a run still moving is kept;
- a run that has finished every experiment gets a grace period on top of the wall to run
  its consistency check and publish;
- at the hard ceiling a run is stopped even while moving, and the reason says it ran out of
  budget, not that it was stuck.

| Knob | Env | Default |
|---|---|---|
| `judge_wall_base_seconds` | `CHELA_JUDGE_WALL_BASE_S` | 600 (10 min) |
| `judge_wall_per_experiment_seconds` | `CHELA_JUDGE_WALL_PER_EXPERIMENT_S` | 360 (6 min) |
| `judge_wall_ceiling_seconds` | `CHELA_JUDGE_WALL_CEILING_S` | 10800 (3 h) |
| `judge_wall_grace_seconds` | `CHELA_JUDGE_WALL_GRACE_S` | 900 (15 min) |

Before the run starts (the judge agent is still designing its experiments), the agent is
bounded by the flat 60 minutes from its spawn, as before.
