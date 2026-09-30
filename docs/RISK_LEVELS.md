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
