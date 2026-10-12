# A dispatched run, from claim to merge

What happens to a task after the dispatcher picks it up, and every command an operator uses
on the way. Flags here are copied from `chela <command> --help`; the design behind the merge
gate is in [TRUST_AND_APPROVAL_POSTURE.md](TRUST_AND_APPROVAL_POSTURE.md).

## The lifecycle

1. **Claim.** A task in the tracker's ready state (`TODO.md` `## Open` line, a labelled
   GitHub issue, or a Linear issue in a `ready_states` state) is claimed. With a Linear
   tracker, chela moves the issue to *In Progress*.
2. **Worktree + agent.** A git worktree is created under `workspace.root`, forked from
   `workspace.base_branch`, and an agent is spawned in it with the `WORKFLOW.md` body as its
   brief.
3. **Push + PR.** The agent never runs `git push` or `gh pr create`. It writes a request with
   `chela request-push <task> --pr-title … --pr-body-file …`, and the daemon pushes the branch
   and opens the PR from its own process (`chela/dispatcher.py`, `request_push`).
4. **`chela task-finished <task>`** asks the daemon to mark the run `awaiting_review` and
   close the agent's window. With `--self-check-experiments <file>` it first re-runs the
   agent's own guard corruptions and refuses if one survives; `--no-new-guards` is the
   opt-out for a run that added no guard. With neither, it warns. It then runs
   `chela judge sweep` — a mechanical mutation of every conditional, comparison and return
   code the branch changed vs its base — and refuses while a mutation survives, unless the
   agent names it in `--sweep-equivalent '<file:line>: <why>'` (recorded in
   `runs.sweep_equivalents`, shown to the judge and appended to the PR body). `--handoff "<note>"`
   records the agent's note for its reviewer on the run (`runs.handoff`); the inbox notice
   and the Dispatch board show it sanitised. It is informational and never gates anything.
5. **The judge.** One judge per head commit corrupts each guard the PR claims and re-runs
   the suite (`chela/judge.py`). A guard that survives sends the PR back; see
   [Risk and the judge](#risk-and-the-judge).
6. **Rework.** A run sent back (by the judge, or by `chela review --request-changes`) is
   re-spawned in its original worktree and branch with the verdict as its prompt, so the open
   PR just updates. Past the rework cap the run stops at `needs_human`.
7. **Merge.** `chela merge <run>` merges only when the judge said `clean` on the PR's current
   head, CI is green, the PR is mergeable, and the base is the workflow's own `base_branch`
   (`chela/contract.py::merge`). A direct `gh pr merge` in a chela-managed repo is denied by
   the hooks plugin's PreToolUse hook.
8. **Done.** After the merge, the dispatcher marks the task done in its tracker: it strikes the
   `TODO.md` line on the base branch (it is that file's only writer), or moves the Linear
   issue to its `done_state`.

A PR that changes only documentation (`docs/`, `changelog.d/`, top-level `*.md`) is not
judged — there is nothing to mutate — and is parked once at `needs_human` for a human OK or
an override.

## Risk and the judge

Each task carries a risk level from its tracker: a `<!-- risk: … -->` marker on a `TODO.md`
line, or a `risk:high|normal|low` label on a GitHub or Linear issue. Unmarked is `normal`
(or `high` when the brief's boundaries touch the dispatcher, judge, merge gate, sandbox or
secrets). The level changes only how many experiments the judge runs and how many rework
rounds the run gets. The judge always runs, and a surviving guard blocks at every level.

| Knob (Settings → Dispatch) | high | normal | low | Env |
|---|---|---|---|---|
| Judge experiments run | 12 | 8 | 4 | `CHELA_JUDGE_EXPERIMENTS_{HIGH,NORMAL,LOW}` |
| Rework rounds | 5 | 4 | 3 | `CHELA_MAX_REWORKS_{HIGH,NORMAL,LOW}` |

`max_reworks` (`CHELA_MAX_REWORKS`, default 5) is a ceiling over all three. Defaults are in
`chela/config.py` (`DISPATCH_KNOBS`); the full rules are in [RISK_LEVELS.md](RISK_LEVELS.md).

## When a run stops at `needs_human`

| You want to… | Command |
|---|---|
| Give it one more automatic rework round on the same head | `chela retry <run> --reason "…"` (the reason is required, and posted on the PR) |
| Re-verify after **you** pushed a fix | `chela reopen <run> [--reason "…"]` — back to `awaiting_review`, so the judge re-runs |
| Send it back with your own verdict | `chela review <run> --request-changes --body-file verdict.md` |
| Merge past the judge | `chela merge <run> --override --reason "…"` — see below |
| Abandon it | `chela close <run> --reason "…"` |

## Overriding the judge

`chela merge <run> --override --reason "…"` (or `chela override request <run> --reason "…"`)
runs every merge check except the judge, records **one request bound to the PR's head
commit**, prints its id and returns. Nothing merges yet.

The operator approves or denies it in one of three places:

- the **Approve / Deny** buttons on the message posted to the orchestrator's Telegram topic;
- the dashboard's confirm page, `/override/<id>`;
- `chela merge-approve <id>` (or `--deny`) in a plain terminal. With no id it lists pending
  requests.

**Approving completes the merge by itself**, through the normal merge path, after re-checking
that the head is unchanged, CI is green and the PR is mergeable. If any of those fail, nothing
merges, and the request is marked failed with the reason. A new push to the PR expires a
pending request, as does `CHELA_OVERRIDE_WAIT_S` (default 24 h). `chela override list [--all]`
shows requests. Approval is human-only: `merge-approve` refuses inside a Claude Code session or
the orchestrator's process, and the merge-gate hook denies a session's attempts to reach it
(`chela/overrides.py`).

## Closing and requeueing

`chela close <run> --reason "…"` marks a run `closed`: it is never claimed or retried again,
its slot is freed, and the Work card reads *Closed — reason*. The branch is kept, and so is the
worktree unless you pass `--remove-worktree`. An open PR gets the reason as a comment;
`--close-pr` also closes it and `--keep-pr` leaves it open without asking. A running run is
refused unless its agent is idle or its window is gone (`--force` overrides).

Closing is terminal, so a task whose run is closed but which is still in the tracker's ready
state is **stalled**: it will never be picked up. `chela close <run> --requeue` makes it
claimable again. The next tick dispatches a fresh attempt on a new `-r2`, `-r3`, … branch and
worktree, and the old PR is closed. Moving the issue back to *Todo* by hand does not requeue
it. `chela doctor` reports a stalled task as `dispatch.closed_run_stalls`, and its Work-board
card carries a **Requeue** button (**Close & requeue** on a run still in flight).

## Holding the queue

`chela dispatch --pause [--reason "…"] [--ttl 30m]` stops new claims, judges and rework
re-spawns without stopping anything already running. `--resume` releases it, and
`--hold-status` shows it. A hold expires on its own after `--ttl` (default 30 m, max 4 h).
