# Agent identity — design doc

Status: **slices 1 + 2a shipped · slice 5 CLOSED (won't build) · 3 + 4 RE-SCOPED 2026-07-27
(slice 3 needs an owner tie-break) · 2b open** · written
2026-07-26, updated 2026-07-27 · author: orchestrator. Written after a day in which four
apparently unrelated bugs turned out to be one. Read this before touching
`agent_manager.session_status_map`, `transcripts.transcript_for_cwd`, `sessions.resolve_window`,
or anything that answers "which agent is this window". (Earlier drafts named
`transcripts.newest_transcript` here — **that function no longer exists**.)

## The thesis

**chela records the window→agent binding in exactly one direction, and re-derives the reverse
from mutable ambient OS state on every query.** Agents learn their own window (`CHELA_WID` is
exported into the environment, `spawn.py`), but chela never records which *session* it just
started. So every consumer that later asks "what is this window doing" or "where is its
transcript" reconstructs the answer by scraping `/proc`, matching on `cwd` strings, or ranking
files by recency.

The stable key exists at every layer and is discarded at every layer.

### ⚠️ Correction (2026-07-26, owner) — `cwd` DOES identify a dispatched run

An earlier draft of this doc claimed `cwd` is ambiguous for every agent. **That is wrong for
dispatched agents and the correction matters, because it changes which slice is urgent.**

Every dispatched run gets its own worktree, and that is structural, not incidental — a run
cannot share a `cwd` with another, because a same-`cwd` claim forces a new `git worktree add`.
Measured: **58 runs, 58 distinct worktree paths, zero reuse.** So for the dispatched fleet,
`cwd` → *which run* is a sound key, and `by_cwd` was never ambiguous for those agents.

Two real ambiguities survive that correction:

1. **`cwd` does not identify a SESSION.** A rework re-spawn reuses the run's worktree and
   starts a *fresh* claude session, so one project dir accumulates several
   `<session-id>.jsonl` files and `transcripts.py` picks among them by recency. Measured:
   cmx-181's worktree holds **3** transcripts, cmx-182's holds **2** — one per round. Unique
   `cwd`, ambiguous session.
2. **Interactive windows genuinely do collide.** They are not worktree-isolated: `@1` and
   `@78` both run in `/home/liavedunix`, which is why the live relay reports
   *"the cwd fallback is REFUSED … a relay into the wrong agent's topic is worse than
   silence"* — **36 `relay.transcript_missing` events in the ring** (⚠️ a 2026-07-26 reading of
   an older ring; the ring on 07-27 held **5**, newest 06:00:50Z — ⛔ don't quote 36 as
   current, and see the re-scope section for why neither number proves much). This is the ambiguity
   with a demonstrated, user-visible cost, and it is the one to fix first.

So the honest framing: **the status join was never broken for dispatched agents; transcript
resolution is ambiguous at the session level everywhere, and at the window level only for
interactive windows.**

## Ground truth — measured 2026-07-26, do not re-derive

- **`claude agents --json` returns a `sessionId` for every entry.** Full key set:
  `['cwd', 'kind', 'name', 'pid', 'sessionId', 'startedAt']`. `kind` is `interactive` or
  `background`.
- **⚠️ A pinned `--session-id` is NOT always the id the session runs as — measured
  2026-07-27.** A live `background` agent's argv reads
  `--session-id 36358c6b-… --fork-session --resume …/949143ad-….jsonl`, while the feed reports
  `sessionId = 29b3560b-…` and the transcript that is actually growing is
  `29b3560b-….jsonl` (214 KB, live); `36358c6b-….jsonl` is a stale 14 MB file from two days
  earlier. **Under `--fork-session` the pin is superseded by a freshly-minted id.** chela's own
  slice-2a spawns pass `--session-id` *without* `--fork-session`, so the pin is expected to hold
  there — but this settles the "authoritative or hint" open decision below in favour of
  **hint, always validated against the live feed**. ⛔ Never open a transcript path built from a
  recorded id without confirming that id against `claude agents --json` first.
- ~~**`chela/agent_manager.py` never references `sessionId`.**~~ **SUPERSEDED by CMX-184
  (re-checked 2026-07-27).** It now keeps a fourth map, `session_by_pid` (`:172`), holding the
  `sessionId` the feed reports beside `status` and `cwd` — read by `session_and_cwd_for_pid`,
  which is what `sessions.resolve_window`'s tier 3 consults. The three original maps
  (`by_pid`, `cwd_by_pid`, `by_cwd`) are unchanged and still keyed on the unstable fields.
  ⚠️ **Relevant to slice 3:** the pid→session half already exists, so slice 3 is the
  *reverse* view (`{sessionId: status}`) plus a `status_by_wid` that resolves a window's
  session instead of its pid — not a new data source.
- **Transcript files are named `<session-id>.jsonl`** — `transcripts.py`'s own docstring says
  `$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/<session-id>.jsonl`. ⚠️ **PARTLY SUPERSEDED —
  re-checked 2026-07-27.** The claim "resolution globs and ranks by recency" was true of the
  whole codebase when written; it is now true only of `transcript_for_cwd` (still at `:268`,
  still `glob("*.jsonl")` → `max(found, key=_key)`) and of the **four callers that reach it
  without a window id**. The relay path resolves by session id and refuses when ambiguous
  (`sessions.resolve_window`). See "Re-scope of slices 3 and 4" for the surviving call sites —
  ⛔ don't scope slice 4 off this bullet alone.
- **`spawn.py` does not pin a session.** `spawn_window(cwd, *, command=None)` (`:99`) opens the
  window, pins the name, exports `CHELA_WID`, and `send-keys` the command (`:163`). Note it
  *sends* the command into a shell rather than running it as the window command, deliberately,
  "so the pane survives the command exiting" (`:120`).
- **Nothing persists a session id.** The `runs` table has no session column; the only
  `session`-ish column in `scheduler.db` is `context_snapshots.session_name`.
- **`sessions.py` carries ~7 functions of pid heuristics** to answer "which pid is the claude in
  this pane": `_comm`, `_children`, `_sh_children`, `_cmdline_argv`, `_looks_like_claude`,
  `_claude_pid(pane_pid)`, `_proc_cwd`. `sessions.py:433` bounds resolution by the claude
  process's start time so "a recycled window id cannot inherit a" dead agent's session — a
  correct defence, built because there is no recorded binding to consult.
- **Blast radius.** The derived status is read by 8 modules (`rooms`, `inbox`, `dashboard/app`,
  `orchestrator`, `notify`, `messenger`, `dispatcher`, `collab`); derived transcripts by 8
  (`orchestrator`, `sessions`, `okf`, `hooks`, `telegram/{reconcile,monitor,bindings}`, `inbox`).
- **The ambiguity, on this box — INTERACTIVE windows only** (see the correction above; dispatched
  runs are worktree-isolated): four live `claude` pids all report `cwd=/home/liavedunix`, so
  `by_cwd` had one slot for four processes and the transcript glob sees four agents' files in one
  directory. CMX-180 made `by_cwd` omit that key rather than guess; the transcript side still
  refuses, which is what the 36 `relay.transcript_missing` events are.
- **✅ The precedent already exists in-repo.** `~/.chela/telegram-bindings.json` records
  `bindings` (wid→topic) *and* `epochs` — the recycled-window-id problem solved by **recording**
  rather than re-deriving. This doc proposes extending that instinct to session identity.

## The four symptoms, and why they are one bug

| symptom | the derivation that broke |
|---|---|
| **The Wall reads `idle` while work is happening.** `status_by_wid()` → `{'@29':'idle','@1':'idle','@78':'busy'}`, stable across 4 forced refreshes over a minute. | Status is joined on pid/cwd. `@1`'s pane really does hold an idle 2-day-old claude; the *busy* process is a `--fork-session` background job that belongs to **no tmux window** (verified: its pid appears in no pane's process tree). It has a `sessionId`, so it is addressable by session — and by nothing else. |
| **`@22`/`@76` "resolve to NO transcript — nothing can be relayed"** (live telegram warning; the log even says "inbound still works, which is exactly why this is invisible"). | The recency-glob guess failed, or was refused because the pid could not be read ("no `/proc`, or a wrapper too deep"). A recorded session id needs neither `/proc` nor a guess. |
| **CMX-179: 12 days of a fleet-wide dead status feed** (`_STATUS_CMD_TIMEOUT = 10.0` vs a 12–18 s command). | Not a separate bug — **the price of derivation**. Re-deriving state through a subprocess requires a timeout, and every timeout is an unmeasured guess against a dependency that drifts. |
| **`gh` at 15–25 s wedged the merge gate** (`contract.py:109`, `timeout=15`). | Same shape, different dependency — WSL2's Windows PATH made `gh`'s startup PATH scan hit 9p, so `gh --version` alone took 23 s. The gate then reported a *policy-shaped* refusal ("a target nobody could read is never assumed safe") for what was a timeout. |

Each was fixed on its own today. None of the fixes touched the reason there were four.

## What derivation is legitimately for

**Not all derivation is gratuitous, and this doc does not propose deleting it.** chela
deliberately adopts windows a human opened and ran `claude` in — `discovery.py` exists for
that — and for those chela never had a chance to pin anything. Some sessions are
*structurally* underivable: a harness-created `--fork-session` background job (this
orchestrator session is one) has no window at all.

So the target is not "stop deriving". It is:

> **Record identity for the agents chela spawns. Derive only as an explicitly-degraded
> fallback, and make the degradation loud.**

That split puts certainty exactly where correctness matters most — the dispatched fleet, which
drives the judge, the merge bookkeeping, and the Telegram relay.

## Design

1. **Pin the session at spawn.** chela generates a UUID, passes `--session-id <uuid>` in the
   command it launches, and records `wid → session_id` durably. The `claude` CLI accepts this
   flag — verified from a live process's argv (`--session-id 36358c6b-…`).
2. **Record it where the binding lives.** Two writers, both already present: the `runs` table
   gains a `session_id` column (dispatched agents), and the window-binding store gains the same
   mapping (interactive/telegram windows), alongside the `epochs` it already keeps.
3. **Turn every derivation into a lookup.**
   - Status: join `claude agents --json` rows on `sessionId`. `by_pid`/`by_cwd` demote to
     fallbacks.
   - Transcripts: open `<encoded-cwd>/<session_id>.jsonl` directly — no glob, no recency rank.
   - Background/forked sessions become addressable, because a `sessionId` is all they have.
4. **Make the surviving fallback honest.** An ambiguous `by_cwd` hit — more than one live pid
   sharing that `cwd` — must return `None`, not an arbitrary sibling's status. Same principle as
   CMX-179's `ok` requiring a real successful fetch: **a confident wrong answer is worse than a
   declared unknown.**

## Constraints & risks

- **⛔ The `send-keys` launch is load-bearing.** `spawn.py` sends the command into a shell rather
  than running it as the window command, on purpose (`:120`), and the caller validates
  user-supplied commands against a `claude`-only allowlist *before* calling. Appending
  `--session-id` must happen on chela's side of that boundary and must not become a way to smuggle
  arguments past the allowlist.
- **⛔ Do not break adoption.** A window chela did not spawn has no recorded session and must keep
  working exactly as it does today, via the existing heuristics.
- **⛔ Do not weaken `sessions.py:433`'s start-time bound** while a recorded binding is absent —
  it is the only thing stopping a recycled window id from inheriting a dead agent's session.
- **⚠️ A recorded binding can go stale too.** If the agent exits and the human starts a new
  `claude` in the same window by hand, the recorded session id is wrong. Recorded identity needs
  the same epoch/liveness discipline `telegram-bindings.json` already applies — record, then
  *validate*, and fall back loudly when validation fails.
- **⚠️ 16 call sites across 12 modules** read these two derivations. The migration must be
  additive: add the lookup, keep the fallback, then remove callers' dependence on ambiguity.

## Slices (→ dispatch briefs)

Sequenced so the first slice is independently valuable and the risky decision comes early.

| # | Slice | Scope | Pure-logic guards | Manual verify |
|---|-------|-------|-------------------|---------------|
| **1** ▶ | **Honest fallback** — ambiguous `cwd` omitted **and** a distinct `unknown` tile state | `agent_manager`: include a `cwd` in `by_cwd` only if every live pid sharing it agrees on a status; an unknown/`None` status counts as **disagreement**. `wallmodel.tileState`: absent status → `? unknown`, not `○ idle`. ⛔ Ranking unchanged; ⛔ do NOT reuse CMX-179's outage marker (the feed *is* answering). | disagreement omits · agreement keeps (pins out the rejected "omit whenever >1 pid" rule) · `None` counts as disagreement · sole occupant unaffected · `unknown` ≠ `idle` in `tileState` · `rankOrder` output unchanged. | a pane that cannot be resolved reads `? unknown`; idle/busy panes unchanged; ordering visually identical. **FILED as a dispatch brief 2026-07-26.** |
| **2a** ⬆ | **Pin + record the session id for INTERACTIVE windows** — *promoted, this is where the live failure is* | `spawn.py` appends `--session-id <uuid>` to the command it sends (inside the caller's `claude`-only allowlist boundary) and records `wid → session_id` beside the existing bindings. Interactive windows share `$HOME`, so this is the ambiguity costing 36 `relay.transcript_missing` events. | uuid generated once and identical in the sent command and the stored row; an override (`--session-id`/`--resume`/`--continue`) records NULL rather than a fabricated id. | a `/new` window's recorded id matches the `sessionId` the live feed reports for its pid; the relay stops refusing. |
| **2b** ⬇ | **Pin + record for DISPATCHED runs** — *demoted: no present bug* | `dispatcher.py` (`resolve_agent_cmd` at `:3302`, `send-keys` at `:3322`), `runs.session_id` via the additive-migration list at `:951`. ⚠️ **Not urgent on its own** — `cwd` already identifies a dispatched run (58/58 unique, worktree-enforced). Its value is as a **slice 4 prerequisite**: it disambiguates *which session within the run*, the 2–3 transcripts a reworked run leaves behind. Do it when slice 4 is built, not before. | id sent == id stored; persisted BEFORE `send-keys` (never launch an agent chela cannot identify); override → NULL; migration additive + idempotent. | `runs.session_id` matches the pid's `sessionId` in the live feed. |
| **3** ⚠ | **RE-SCOPED 2026-07-27 — one identity per window, shared by status and transcript** (was: "join status on `sessionId`") | `status_by_wid` must derive a window's session the way `sessions.resolve_window` already does, then join the feed on `sessionId` — **not** on the pane's pid. ⚠️ **Carries an owner decision** (which agent `@1` *means*) — read "Re-scope of slices 3 and 4" below before dispatching. | that status and `sessions.resolve_window` name the SAME session for a window, or the window reads `unknown` — never two confident answers that disagree. | `@1`'s tile and `@1`'s Telegram topic agree about which agent is on the other end. |
| **4** ⚠ | **RE-SCOPED 2026-07-27 — route the 4 surviving cwd-glob callers through the refusing resolver** (was: "resolve transcripts by name" — mostly already built) | `inbox.py:596`, `orchestrator.py:79` + `:188`, `context.py:278`, `dispatcher.py:1582` still reach `transcripts.transcript_for_cwd`'s `glob`+recency rank, which cannot refuse. Give them `sessions.resolve_window`'s answer so they inherit the refusal. ⛔ Do NOT delete `transcript_for_cwd` — it is `resolve_window`'s own last-resort tier. | a caller handed an ambiguous cwd gets `None`, not a sibling's transcript — corrupt by making the sibling newer. | context bars / cost / completion detection stop attributing one home-dir agent's work to another. |
| **5** ⛔ | **~~Surface windowless sessions~~ — CLOSED, won't build (owner, 2026-07-27)** | `kind: background` agents stay invisible to chela. See "Why slice 5 is closed" below before re-proposing it. | — | — |

Slice 1 is shipped (CMX-180) and slice 2a is shipped (CMX-185). Slice 5 was the one with a
real product decision in it and **the owner closed it on 2026-07-27 — see below.** 2b waits
for slice 4, per the correction above.

### Re-scope of slices 3 and 4 — measured 2026-07-27, do not re-derive

Both slices were written on 2026-07-26, **before** CMX-184/188/189 shipped. Their stated
premises are now stale in different directions: slice 4 is *mostly already built*, and slice 3
turns out to be pointing at a **different bug than the one it describes**. Measured on this
box at 07:5x UTC, with the status cache warmed the way `chela telegram` warms its own
(CMX-188) so the reading is representative of production rather than hand-warmed:

**Ground state — the failure both slices were written for is not currently firing.**

- All three live windows resolve through the **strongest** tier: `@29`, `@1`, `@118` all
  `source=event_log`, all with a real path. Zero refusals. The plugin repair restored hooks,
  and `@78` — the window that generated most of the noise — no longer exists.
- `relay.transcript_missing`: **5 in the ring, newest 06:00:50Z**, none since. ⚠️ **Do not read
  that as "fixed"**: the ring spans only ~2 h (920 events, 05:40→07:46Z), so this is *no
  failures in the last ~1 h 45 m*, not a clean history. The doc's "36 events" figure is from a
  different, older ring — ⛔ don't quote it as current.

**Slice 3 — the real bug is a DISAGREEMENT, not a missing join.**

Right now, live, chela gives two different answers for the same window:

| consumer | how it resolves `@1` | answer |
|---|---|---|
| `agent_manager.status_by_wid` | pane pid (`claude_pid('@1')` = **1405503**) → `by_pid` | **`idle`** |
| `sessions.resolve_window('@1')` | event log → session **`29b3560b`** | that session is pid **2447758**, which the feed reports **`busy`** |

Pid 2447758 is a **windowless `--fork-session` job** whose hooks fire carrying the
`CHELA_WID=@1` it inherited from the window it was forked out of. So `@1`'s **Wall tile reads
`idle` while `@1`'s Telegram topic relays that fork's live output** — the doc's symptom #1,
still live, and untouched by anything shipped so far.

⛔ **The original slice-3 scope would not have fixed it.** It says "prefer the *recorded*
session id" — but nothing is recorded for `@1` (slice 2a records only what `spawn.py` starts,
and `sessionids.session_id_for` still has **zero readers**). The correct id here comes from the
**event log**, which is exactly what the transcript resolver already uses. The fix is to make
status resolve through that same identity, not to add a fifth source.

⚠️ **This carries an owner decision, and the two answers have opposite user-visible effects:**

1. **Status follows the event-log session** (status matches the relay). `@1` reads `busy`
   — but the tile then contradicts *its own visible terminal*, which shows an idle interactive
   pane. Consistent with Telegram, inconsistent with what you can see.
2. **The relay follows the pane** (relay matches status). Structurally tidier — one window, one
   pane, one agent — but it **breaks the Telegram topic**: the fork's output is what the owner
   is actually reading in that topic today, and this would silence it.

⛔ Do not pick one while writing the brief. Given slice 5 is closed — a fork gets no surface of
its own — option 1 is the coherent default, but it makes a tile disagree with its own pane and
that is the owner's call. **A defensible third path: ship the disagreement as an observable
first** (when a window's event-log session ≠ its pane pid's session, the window is hosting two
agents — say so rather than confidently reporting either), in slice 1's spirit, and settle the
tie-break with data.

**Slice 4 — largely built; what remains is four unrefused callers.**

The premise "`transcripts.py:268` globs and ranks by recency" no longer describes the relay
path: `sessions.resolve_window` resolves **by session id** through four tiers and returns
`None` rather than guessing. `newest_transcript` no longer exists. What survives is
`transcript_for_cwd`'s glob, reached by callers that never hand over a window id:

| call site | consequence today |
|---|---|
| `context.py:278` (`agent_context_from_transcript`, no `window_id`) | context bars / cost for a `$HOME` agent can read a sibling's transcript |
| `inbox.py:596` (`last_assistant_activity(cwd)`) | completion detection can credit another agent's turn |
| `orchestrator.py:79`, `:188` (`transcript_for_cwd(win["cwd"])`) | same collision on the orchestrator read path |
| `dispatcher.py:1582` (`agent_transcript_summary(window_name)`, no `window_id`) | worktrees are unique, so this is safe *between* runs but still picks by recency **within** a reworked run's 2–3 transcripts — this is the call site that makes slice **2b** worth doing |

Already correct, for contrast: `dashboard/app.py:207` passes `window_id=`, and
`telegram/{monitor,reconcile}` call `sessions.transcript_for_window` — both inherit the
refusal. ⛔ `transcript_for_cwd` itself must stay: `sessions.py:692` is `resolve_window`'s own
documented last-resort tier.

**Sequence:** slice 4 is now the *lower-risk, better-evidenced* of the two and carries no
product decision — do it first. Slice 3 needs the owner's tie-break before a brief can be
written. Slice 2b remains a slice-4 prerequisite only for the `dispatcher.py:1582` row.

### Why slice 5 is closed — measured 2026-07-27, do not re-derive

The decision was "don't build", and these are the facts it rested on. Re-open only if one of
them stops being true.

- **The population is one, and it is the orchestrator's own fork.** `claude agents --json` on
  this box: 5 agents, **1** with `kind: background` — this orchestrator session, which the
  owner already watches through Telegram and its own terminal. A surface would have had a
  single occupant that was already observable elsewhere.
- **They are unreachable by construction, not merely unwired.** No pane means no ttyd
  terminal, no `send-keys` (so no message / trigger / broadcast), no rooms, no rename, no
  restart. Only kill-by-pid is technically available. Anything built here is a **read-only**
  surface for an agent you cannot answer.
- **chela can never *record* them — only discover them.** chela does not spawn background
  agents; the harness forks them. So slice 2a's record-don't-derive instinct has no purchase
  here, and (per the `--fork-session` finding in Ground truth) even their own argv pin
  disagrees with the id they run as.
- **They are absent, not misreported.** `grep` over `chela/*.py` finds **zero** references to
  the feed's `kind` field, and `/api/agents` — which Feed, Wall, Agents and live Cost all
  derive from — iterates `discovery.get_all_windows()`, i.e. tmux windows only. So there is no
  *wrong* answer on screen today to correct; this would be new surface area, not a repair.
  ⛔ That is the distinction to hold onto: slice 1 fixed a confident wrong answer, which is
  worth building. Slice 5 would have added a true answer nobody had asked a question for.
- **Cost attribution was ruled out with it (owner, same call).** Their transcripts carry **no
  cost field**, and `context.live_snapshot` reads spend from the statusLine cache keyed by tmux
  window name — which never fires for a paneless agent. So windowless spend is currently
  *underivable*, not merely unshown; attributing it needs a token-sum estimator whose output
  would sit beside statusLine-authoritative figures. Standing position: **leave it
  unattributed**, and if it is ever built, mark it visibly as an estimate.

**Re-open when:** background agents stop being a population of one — e.g. the dispatcher grows
a windowless worker kind, or forked agents start doing work whose spend or failure is not
visible anywhere else. At that point the cheapest honest surface is a read-only row in the
Agents view; recap needs no new machinery, because a background agent's transcript opens
directly from its feed `sessionId` (verified: `~/.claude/projects/-home-liavedunix/29b3560b-….jsonl`,
live and growing).

⚠️ **A brief for 2b was written and withdrawn on 2026-07-26 before it produced anything**
(filed as cmx-183, killed in flight, worktree reaped, no PR). It justified itself with the
`$HOME` collision — which is *interactive-window* evidence — while scoping itself to the
dispatcher, where `cwd` is already unique. **Check which population your evidence comes from
before scoping a fix to a different one.**

⚠️ **Slice 1 needs both halves or it is a no-op** — found while writing its brief, and worth
recording because the same trap will recur in slices 3 and 4. `tileState` (`wallmodel.js:49`)
falls through to `○ idle` whenever `session_status` is absent, so omitting an ambiguous `cwd`
on the server merely swaps a wrong `busy` for a wrong `idle`. Honesty in the data model buys
nothing until the surface can *express* "unknown". Whenever a slice here replaces a guess with
a declared unknown, check what the consumer renders for the absent case before calling it done.

## Verification (whole workstream)

- Per slice: guards go **RED** under corruption (read the assertions, not the pass count);
  CI green on 3.11 + 3.12; **and** `--color=no` when parsing pytest output yourself, because
  `FORCE_COLOR=3` is set in the live daemon environment.
- **Confirm the baseline is green before believing a corruption.** A pre-existing
  environment-dependent failure makes every corruption look caught.
- No regression to adoption (`discovery.py`), the ttyd transport, the 4 s poll, or SSE.

## Open decisions

- ~~**Where windowless (`kind: background`) agents appear**~~ — **SETTLED 2026-07-27: nowhere.**
  Slice 5 closed, won't build. See "Why slice 5 is closed".
- ~~**Whether a recorded session id should be authoritative or merely a hint.**~~ — **SETTLED
  2026-07-27 by measurement, not preference: a HINT, always validated against the live feed.**
  The `--fork-session` evidence in Ground truth shows a pinned id can be superseded by the id
  the session actually runs as, so an authoritative read can open a stale transcript that
  exists and looks plausible. Slices 3 and 4 must validate before they trust.
- **Whether `by_cwd` should survive slice 3 at all.** If recorded sessions plus `by_pid` cover
  every case that matters, an ambiguous-by-construction key may be worth deleting rather than
  repairing.
