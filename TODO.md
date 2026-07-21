# TODO

The chela dispatcher claims each unchecked `- [ ]` item under an **Open** section, runs
it as an isolated git-worktree agent (adversarially reviewed by the judge), and strikes
it on merge. This file is the **live queue, not an archive** — completed-task history
lives in `git log`.

Each item is a four-field brief the judge can enforce mechanically:

- **OBJECTIVE** — what to build and why.
- **BOUNDARIES** — files/scope it may touch; what not to regress.
- **GUARDS** — tests that must go **RED** when their invariant is corrupted (a guard that
  survives its own corruption is decoration).
- **VERIFY** — how to confirm the result, including anything that needs a manual check.

## Open — CI drives the loop

_No open tasks._

## Backlog

Rough ideas, not yet dispatchable — each becomes a full four-field brief when picked up.

- **Planner / decomposer persona** — split an epic-sized item into N small guarded
  sub-tasks (each with corrupt-each-→-RED guards) for worker agents to pick up. The
  load-bearing risk is brief quality, so it needs guard-discipline in its prompt plus a
  critic/human checkpoint on the generated children before dispatch — not fire-and-forget.
- **Gate-unify** — the review-gate path and the merge path are separate authorities;
  reconcile them to one.
- **Auto-orchestrator teardown on lease expiry** — kill its window when the attended
  lease lapses, so the merge action-gate isn't the sole post-expiry stop.
- **Settings view — editable toggles** — in-UI write-back + daemon restart.
- **Cost view** — transcript tokens × price → cost per agent / run / fleet.
- **Fleet loose ends** — Wall terminal addressing, explicit agent-kill (`/kill` + close
  topic).
