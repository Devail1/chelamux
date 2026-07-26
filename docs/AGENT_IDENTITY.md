# Agent identity — design doc

Status: **proposed, nothing built** · 2026-07-26 · author: orchestrator. Written after a day
in which four apparently unrelated bugs turned out to be one. Read this before touching
`agent_manager.session_status_map`, `transcripts.newest_transcript`, `sessions._claude_pid`,
or anything that answers "which agent is this window".

## The thesis

**chela records the window→agent binding in exactly one direction, and re-derives the reverse
from mutable ambient OS state on every query.** Agents learn their own window (`CHELA_WID` is
exported into the environment, `spawn.py`), but chela never records which *session* it just
started. So every consumer that later asks "what is this window doing" or "where is its
transcript" reconstructs the answer by scraping `/proc`, matching on `cwd` strings, or ranking
files by recency.

Those derived keys are not merely fragile. On a single-user box — the normal case — they are
**provably ambiguous**, because every agent runs with the same `cwd`.

The stable key exists at every layer and is discarded at every layer.

## Ground truth — measured 2026-07-26, do not re-derive

- **`claude agents --json` returns a `sessionId` for every entry.** Full key set:
  `['cwd', 'kind', 'name', 'pid', 'sessionId', 'startedAt']`. `kind` is `interactive` or
  `background`.
- **`chela/agent_manager.py` never references `sessionId`.** `grep -n "sessionId\|session_id"`
  over that file returns nothing. It builds three maps, all keyed on the unstable fields:
  `by_pid[int(pid)]`, `cwd_by_pid[int(pid)]`, `by_cwd[cwd]` (`:314`–`:318`).
- **Transcript files are named `<session-id>.jsonl`** — `transcripts.py`'s own docstring says
  `$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/<session-id>.jsonl`. But resolution
  (`transcripts.py:268`) does `glob("*.jsonl")` and returns `max(found, key=_key)`, ranking by
  newest *record* timestamp. **The exact filename is derivable from a key chela could have
  recorded, and instead it guesses by recency.**
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
- **The ambiguity, on this box:** four live `claude` pids all report `cwd=/home/liavedunix`, so
  `by_cwd` has one slot for four processes and the transcript glob sees four agents' files in one
  directory.
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
| **1** | **Honest fallback** — ambiguous `by_cwd` returns `None` | `agent_manager`: build `by_cwd` only for a `cwd` with exactly ONE live pid; ambiguous → omit. Surface "ambiguous, not idle" through the existing `status_health` marker. | given a feed with 2+ pids sharing a `cwd`, that `cwd` is absent from `by_cwd`; a unique `cwd` still resolves. Invert → RED. | the Wall stops asserting `idle` for panes it cannot actually resolve. |
| **2** | **Pin + record the session id** | `spawn.py` appends `--session-id <uuid>` (inside the allowlist boundary); `runs.session_id` column + the window-binding store; recorded on spawn, cleared on window death. | uuid generated once and identical in the sent command and the stored row; a window chela did not spawn stores nothing. | a dispatched agent's `runs.session_id` matches the pid's `sessionId` in the live feed. |
| **3** | **Join status on `sessionId`** | `session_status_map` gains `by_session`; `status_by_wid` prefers recorded session → `by_pid` → unique-`by_cwd` → `None`. | precedence order, and that a recorded-but-absent session degrades to the next tier rather than to a wrong answer. | `@1` reads what it is actually doing; `busy` panes read `busy`. |
| **4** | **Resolve transcripts by name** | `transcripts`: recorded session → direct path; glob/recency only as documented fallback. | recorded id opens that exact file even when a newer sibling exists in the same dir — the case the recency rank gets wrong. | `@22`/`@76`-class windows relay again. |
| **5** | **Surface windowless sessions** | `kind: background` agents have no pane; give them a place (a dashboard row, not a fake wall tile). | — | this orchestrator session becomes visible as itself. |

Slice 5 is the one with a real product decision in it — where windowless agents belong — and
should be settled with the owner before it is dispatched. Slices 1 and 2 are independent and
safe to file now; 1 is worth doing even if nothing else here is ever built.

## Verification (whole workstream)

- Per slice: guards go **RED** under corruption (read the assertions, not the pass count);
  CI green on 3.11 + 3.12; **and** `--color=no` when parsing pytest output yourself, because
  `FORCE_COLOR=3` is set in the live daemon environment.
- **Confirm the baseline is green before believing a corruption.** A pre-existing
  environment-dependent failure makes every corruption look caught.
- No regression to adoption (`discovery.py`), the ttyd transport, the 4 s poll, or SSE.

## Open decisions

- **Where windowless (`kind: background`) agents appear** — slice 5; owner's call.
- **Whether a recorded session id should be authoritative or merely a hint.** Authoritative is
  simpler and faster; a hint that is always validated against the live feed is safer against the
  stale-binding case above. Leaning: **hint, validated** — the cost is one lookup in a feed
  chela already fetches.
- **Whether `by_cwd` should survive slice 3 at all.** If recorded sessions plus `by_pid` cover
  every case that matters, an ambiguous-by-construction key may be worth deleting rather than
  repairing.
