# The Linear tracker (`tracker: kind: linear`)

A workflow can read its queue from a Linear team instead of a `TODO.md` file or GitHub
issues. Briefs stay private (Linear issues are not public), blocking relations and manual
ordering are native, and Linear's GitHub integration can close an issue when its PR merges.
The adapter is `chela/sources/linear.py` (CMX-432).

## Configure it

1. **Create a personal API key** in Linear (Settings → Security & access → Personal API
   keys), scoped to the one team the workflow dispatches. chela needs read and write access:
   it marks merged tasks Done and archives them.
2. **Put the key in `$CHELA_DIR/chela.env`** (normally `~/.chela/chela.env`), exactly as:

   ```
   LINEAR_API_KEY=lin_api_…
   ```

   That file is the only place chela reads it from. A key in `WORKFLOW.md` (`api_key:`,
   or any key/token-shaped name under `tracker:`) is refused and the workflow claims no
   work, because a workflow file lives in a repo. chela never logs the key, and no process
   chela launches gets it: agents, judges, hooks and test suites all run on `child_env()`,
   which drops `LINEAR_API_KEY` (see [CONFIG.md](CONFIG.md), "Secrets stay out of the
   children chela launches").
3. **Point the workflow at the team:**

   ```yaml
   tracker:
     kind: linear
     team: ABC              # the team KEY, also the identifier prefix (ABC-12)
     ready_states: [Todo]   # optional: the state names chela may CLAIM from (default Todo)
     done_state: Done       # optional: the state chela sets on merge
                            # (default: the team's first `completed` state)
   ```

4. Restart `chela-daemon`. If the key is missing, only this workflow stops. It claims
   nothing, the daemon log shows one ERROR, and `chela doctor` reports the workflow as
   refusing to claim work.

## What chela reads

| Question | Answer |
|---|---|
| Which issues are **open** | Every issue in the team whose state *type* is not `completed` or `canceled`: backlog, unstarted and started. The open set must include In Progress and In Review, because the GitHub integration moves issues there on its own. An issue missing from the open set is how the dispatcher learns a task is finished. |
| Which issues are **claimable** | Only issues in a `ready_states` state (default `Todo`) whose blockers are all done. |
| **Claim order** | Priority first (Urgent, High, Medium, Low, then *No priority* last), then Linear's manual order (`sortOrder`, the order the board shows), then the issue number. |
| **Blocking** | Linear's *blocked by* relations. A task is held while any blocker is not in a `completed` state. A canceled blocker, or one chela cannot read, also holds it. This is the same fail-closed rule as markdown's `depends:`; remove the relation to release the task. |
| **The brief** | The issue's description becomes `Task.body`, rendered into the prompt as `{{task_body}}` and stored as the run's brief. |
| **Risk** | A `risk:high` / `risk:normal` / `risk:low` label, as with `gh_issues`. |
| **Identity** | The issue identifier (`ABC-12`) is the run's task id, Linear's number (12) is its `task_number`, and Linear's suggested `branchName` (`abc-12-tighten-top-row`) is both its branch and its tmux window name. |

`fetch_by_ids` (the ID-refresh read G1 adds) returns `None` for any failed read (network,
auth, rate limit, malformed response) and never `[]`. It includes archived issues and
reports an archived issue as **done**, never as missing.

An id that isn't one of this team's identifiers (another team's `ENG-5`, or a TODO.md-era
hex id) is not returned at all, and reconciliation reads an unreported id as gone. So if a
`TODO.md`-era run is still in flight when you switch a workflow to `kind: linear`, its run
closes on the first tick. Let those runs finish, or close them, before you flip the tracker.

A failed read never closes anything. On any error the tick's read is marked failed and
reconciliation does not treat absent issues as finished. A 429 or a `RATELIMITED` error
backs off per team, starting at 60 seconds and doubling up to 15 minutes (or using
`Retry-After` when given). Reads are cached for one tick only.

## Sharing a counter with an old `TODO.md` queue

If the team key equals the workflow's old `project_key` (chelamux's own case: `CMX`),
Linear's numbers start at 1 and overlap the `cmx-1` … `cmx-4xx` branches, PRs and runs from
the `TODO.md` era. That is safe:

- A Linear branch is `cmx-12-<slug>`, never the bare `cmx-12`, so it cannot equal an old
  branch. If the remote already has that exact name, chela takes `cmx-12-<slug>-2` (then
  `-3`, …) and never reuses it. A retry keeps the branch its first attempt took.
- `chela merge` / `peek` / `review` / `close` resolve `CMX-12` (the exact identifier) to the
  Linear run. A bare `cmx-12` that names both prefers the run that is still in flight, and
  among those, or when neither is, the Linear run. It never picks an old done or closed
  TODO.md-era run.
- Old merged or closed `cmx-N` PRs are not touched. Linear links a PR to an issue when the
  PR has new activity, so an old PR whose branch happens to read `cmx-12` is not re-linked
  to the new `CMX-12`.

## Done, and archiving (the free plan)

Linear's free plan caps a workspace at **250 non-archived issues**. Done and Canceled issues
count until they are archived. At ~19 tasks a day that cap arrives in about two weeks, and
Linear's own auto-archive runs monthly at most. So chela archives:

- **When a run's PR merges**, chela marks the issue Done if it is not already (the fallback
  for the GitHub integration) and calls `issueArchive`. This is idempotent: an issue that
  is already Done is only archived, and one that is already archived is left alone. A
  failure is logged and retried on the next tick. It never crashes the dispatcher.
- **A backstop sweep** runs every 15 minutes per team. It archives every completed or
  canceled issue that is not archived yet (at most 50 per sweep), whoever closed it: the
  integration, a human, or chela. It also publishes the team's non-archived count to
  `$CHELA_DIR/linear-issue-counts.json`.
- **`chela doctor`** reads that count (fact `tracker.linear_issue_cap`) and WARNs above
  **200**.

chela never creates sub-issues, and never creates issues at all. Issues are written by a
human in Linear.

## The GitHub integration

Connect Linear's GitHub integration for the repo. It moves an issue to In Progress or In
Review when a PR that names it opens, and to Done when it merges. chela reads all of those
states as open until Done.

**PRs merge into `dev`, not the default branch.** By default the integration only treats a
merge into the default branch as done. Either:

- in the team's settings (Workflow → Git automations), add a **target-branch rule** for
  `dev` that moves the issue to Done on merge, or
- do nothing. When chela reconciles a merged PR it marks the issue Done itself and archives
  it, so the queue stays correct either way. The rule only makes Done appear sooner.

## Migrating a `TODO.md` queue

Merging this adapter changes nothing for an existing workflow. To move one over: create the
team and key, connect the integration, copy the open briefs into issues (description = the
brief), recreate the `depends:` edges as *blocked by* relations, then switch
`tracker: kind:` to `linear`. That switch is an operator step, done separately.
