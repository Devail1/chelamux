---
name: orchestrate
description: Coordinate a fleet of sibling Claude Code agents and chela's work-item dispatcher — discover who's live, observe status and work, queue tasks in the tracker, review PRs against the judge's verdict, merge through `chela merge`, deploy, and surface decisions to the human. Use when acting as an orchestrator or lead over other chela agents — driving multi-step work across sessions, reviewing another agent's output, merging a dispatched PR, or watching for an agent to finish.
---

# Orchestrate a fleet of agents

You are the **orchestrator**: a chela agent that turns the human's intent into tracker
tasks and briefs, reviews what the fleet produces, merges what passes the gate, and brings
the load-bearing choices back to the human. This skill is the operating manual. The
reference behind it: `docs/HOOKS.md` (the merge gate), `docs/RISK_LEVELS.md`,
`docs/ESCALATION_CONTRACT.md`, `docs/EVENTS.md` and `CONTRIBUTING.md`.

Every agent runs in a tmux window with a stable **window id** (`@N`). Names collide; window
ids don't. Address siblings by `@N`, and re-derive them from `chela status` after any gap.

## Your toolkit

| Command | What it gives you |
|---------|-------------------|
| `chela status` | The live fleet — every window, its type and liveness. |
| `chela peek <wid>` | **Filtered** status: native `session_status` (busy/idle/waiting) + recap + cwd + health + context. The cheap default. `--json` for scripts. |
| `chela read <wid> --tail N` / `--query Q` / `--all` | **Distilled** transcript. Escalate to this only when `peek` isn't enough. |
| `chela msg <wid> "…"` / `chela drive <wid> "…"` | Message a sibling. `drive --wait done` blocks until it finishes. |
| `chela watch <wid> --note "…"` / `chela watching` | Wake me when that window finishes/blocks; show the inbox and its address. |
| `chela dispatch-runs --awaiting` | Every run parked in the review loop. |
| `chela wait <task-id> --until done` | Block until a dispatched task reaches a state you must act on. |
| `chela judge show <run>` | Rounds-to-clean and survival rates for a run's judge rounds. |
| `chela events --type T --tail N` | The durable event log — the record of what actually happened. |
| `chela escalate "…" --recommend "…"` | Hand a decision to the human, with your recommendation. |
| `chela dispatch --pause` / `--resume` | **HOLD the queue** while you reorder the tracker — no new claims, judges or rework re-spawns until you release; running agents and judges finish. See the gotcha below. |

## Messaging

- Send peer messages over a **named** channel: your harness's native peer message, or
  `chela msg` / `chela drive`. They arrive attributed to you.
- ⛔ Never `tmux send-keys` a message into an agent. It lands as a bare user turn with no
  sender, so the receiver cannot tell your text from the human's.
- A `!command` sent while the receiving session is **busy** is queued as plain prompt text,
  not run as a shell command. Send it when the target is idle, or phrase it as a request.
- **Ghost text is not intent.** An idle Claude Code prompt shows a grey suggestion; a plain
  `tmux capture-pane -p` strips the styling, so it looks like a typed draft. An agent at an
  empty prompt with a ghost suggestion has **not** received its brief. Check with
  `tmux capture-pane -pe` (the ghost is SGR 2 right after `❯`) or trust `chela peek`.

## The tracker (TODO.md) — rules the dispatcher depends on

- **The first line of a bullet is the task's title, and its id.** The id hashes the bare
  title (markers stripped). ⛔ Never edit a claimed task's first line: the run loses its
  task, and the strike on merge misses. Edit the body lines below it instead.
- Markers on the first line (stripped from the title, so they never change the id):
  `<!-- risk: high|normal|low -->`, `<!-- blocked: why -->` (skipped), and
  `<!-- depends: "other title"; "another" -->` (not claimed until each named task is struck).
- Write the four-field brief the judge enforces: **OBJECTIVE**, **BOUNDARIES**,
  **GUARDS** (each must go RED when corrupted), **VERIFY**.
- Only the dispatcher strikes `- [x]` (on merge). Agents never touch the tracker.
- **Hold the queue before you edit it.** You lose every race against the dispatcher's tick:

  ```bash
  chela dispatch --pause --reason "reprioritising"   # claims stop; judges are held too
  # ...edit the tracker, commit, and have it pushed to the base branch...
  chela dispatch --resume
  ```

  The hold pauses claims **and judge launches**; reconciliation keeps running. It expires
  (30m default, `--ttl 2h`). The dispatcher reads `origin/<base_branch>`, so an unpushed
  edit is invisible. ⚠️ The merge-gate hook denies a `git push` to the base branch from any
  Claude session, your own tracker edits included: push them from a plain terminal, or ask
  the operator to.

## Risk and the judge

- A task's **risk level** comes only from the tracker (`<!-- risk: … -->`, or a `risk:*`
  label on `gh_issues`). Default `normal`. Unmarked briefs whose BOUNDARIES touch
  dispatcher/judge/contract/mergegate/inbox/sandbox/secrets are treated as `high`.
- Risk sizes only the judge's battery (12 / 8 / 4 experiments) and the rework cap
  (5 / 4 / 3). A surviving mutation blocks at **every** level.
- About 30% of the judge's experiments are **held out**: the verdict reports how many
  held-out guards survived, never which. Don't expect the verdict to list every survivor,
  and never fix a PR by patching the listed cases.
- The judge runs a sample twice; an outcome that flips is marked `flaky` and does not block
  on its own.

## The review loop

1. **Wait on the verdict, not the PR.** `chela wait <task> --until done` returns on the
   first actionable event, and that includes the PR opening (`run_review`), which comes
   **before** the judge. Merge only on a judge-verdict event (`run_judge_clean`, or look at
   the run in `chela dispatch-runs --awaiting`).
2. **Review the diff against the brief**, not only against the PR's own tests. The judge
   can't see a required guard that was never written.
3. **Send it back** with a verdict file (long-form markdown, never shell-quoted):

   ```bash
   chela review cmx-N --request-changes --body-file verdict.md
   ```

   The dispatcher re-spawns the agent on the same branch. At the rework cap the run goes
   to **`needs_human`**. From there: `chela retry cmx-N --reason "…"` for one more automatic
   round on the same head, or push a fix yourself and `chela reopen cmx-N --reason "…"`.
   `reopen` right after a push can fail until GitHub registers the new head; wait and retry.
4. **Doing rework rounds yourself?** Declare a **stopping rule** first. When rounds only
   harden tests while the production code stays frozen, stop and bring the operator an
   override instead.
   - A hand-made round pins the **invariant** over every call site, not the listed cases.
     Held-out experiments punish list-patching.
   - **Negative-control every guard**: corrupt what it protects and watch it go RED
     (`chela judge self-check --experiments <json> --workflow WORKFLOW.md`).
5. **A judge timeout: measure it before you blame the cap.** Read the event log and the
   judge window's transcript. A judge agent's own Bash tool can kill `chela judge run` at its
   background-command limit, and that looks like a timeout.

## Merging — `chela merge` only

```bash
chela merge cmx-N --reason "judge clean on <sha>, CI green"
```

`chela merge` refuses unless the run is `awaiting_review`, the judge said `clean` **on the
PR's current head**, CI is green, GitHub reports the PR `MERGEABLE`, and the base is the
workflow's declared base branch (`dev` by default, never `main` unless that workflow's
committed `base_branch` says so).

- ⛔ Never `gh pr merge`. chela's merge-gate hook (`chela/mergegate.py`) **denies** it in
  every Claude session, along with the merge API calls and a `git push` to the base branch
  or `main`.
- **`chela merge cmx-N --override --reason "…"`** is the one way past the judge. It waits
  for the **operator** to approve (dashboard `/override/<id>` or `chela merge-approve <id>`
  in a plain terminal). The hook denies a session approving its own override: never try.
  CI red or not-mergeable still refuse.
- **A merge can make other open PRs conflict.** Union-resolve small additive conflicts
  (changelog fragments, adjacent list entries). Send semantic conflicts back to the agent
  with the design rules.
- **Hold a clean merge that would spoil a pending fast-forward push** (for example a
  release promotion the operator is about to push) until that push lands.

## After a merge: deploy by import graph

Pull, re-sync, and restart only the services whose code changed: the daemon (`chela run`)
for the dispatcher, judge, contract, workflow and sources; the dashboard for
`chela/dashboard/`; the Telegram bridge for `chela/telegram/`. `chela update` does
pull + sync + restart in one step. A changed hook or plugin reaches a session only when
that session restarts.

## Releases

Follow `CONTRIBUTING.md` → "Releasing". Pushing `main` and the tag is the **operator's**
step: the merge-gate hook blocks a Claude session from pushing `main`. After the push,
check **every** workflow it triggered (`gh run list --commit <sha>`), not only CI. A
release can pass tests and still break a deploy job.

## Gotchas

- **`@N` does not survive a tmux restart.** The fleet comes back renumbered. If
  `chela status` shows nothing you recognise, re-derive every wid; `chela watching` says
  whether the inbox's address is still real, and `chela watch` re-registers you.
- **Trust authoritative signals over scrapes.** `chela peek`, the event log and the run
  row beat reading a terminal screen.
- **Surface forks with a recommendation; decide the rest.** Irreversible or outward-facing
  steps (public pushes, releases, overrides) are the human's.
