# Mods spike — chela's hook layer as a Claude Code Mod (CMX-157)

> **Verdict: a split GO.**
> **GO** for the roadmap's *in-session UI* items (status line, timestamps band, approvals
> band, classifier-denial reporting), shipped as an **optional** Mod on top of the shell
> plugin.
> **NO-GO, for now,** on *replacing* the shell plugin's event posting and merge gate. The
> Mod was faster and lost nothing. Two things block the swap. First, it raises the Claude
> Code floor to **2.1.301**. Second, `classic.*` hooks **do not run in an org-managed
> install** unless that org lists the Mod in managed settings, so the event log would go
> quiet there without saying anything.

⚠️ **Read this first: it was not a full day.** The brief asked for a day of side-by-side running on one
non-orchestrator session. This spike ran unattended, so what it measured was a **scripted
side-by-side run of about 13 minutes**: 11 sessions, 17 session starts (8 of them `--resume`
restarts), one interactive session that took a hot reload, and **696 hook POSTs** in all.
Every number below comes from that run. None is an estimate. A day of real use would add
idle time, compactions, subagents and long sessions, and this run had none of those
(§6 lists what the run did not cover).

## 1. What was built

`mods/chela-hooks/` is a Mod (`register.ts`, ~90 lines). It covers the two things the brief
scoped:

| Concern | Shell plugin (`plugin/`) | Mod |
|---|---|---|
| Event posting | one `curl` process per hook (`command` type), or Claude Code's own `http` hook | `$.http.fetch` inside the hooks worker, **not awaited**, so no process is spawned and the agent never waits for the daemon |
| Body shape | the settings hook's stdin | **the same bytes.** `classic.<Event>` hands over the settings hook's stdin verbatim, so the Mod posts `e` as is. PreToolUse is the one exception (§4.1) |
| Events | all 15 | SessionStart, SessionEnd, Stop, PreToolUse, PostToolUse, PermissionDenied (the brief's scope) |
| `$CHELA_WID` gate (CMX-319) | `[ -n "$CHELA_WID" ] &&` in each command | `$.env.get('CHELA_WID')`. Without it nothing is posted, and the merge gate still runs |
| Merge gate | `python3 -I -S mergegate.py` as a PreToolUse `command` hook | `tool.call` on `Bash` runs **the same script** through `$.process.run` and relays its `permissionDecisionReason` verbatim |

**Why the gate decision was not ported to TypeScript.** `mergegate.py` is an 809-line
shell parser that handles chains, `$(…)`, `bash -c`, heredocs, `git -C`, worktrees and
tmux sockets. Writing it a second time would give two parsers that drift apart. The Mod
runs the same script as the shell hook, so it has the same deny set and the same message
by construction, and pytest proves both against the real script (§5). The cost is that
the Mod's Bash hook still pays for a Python start-up (§2).

## 2. Latency per hook

Two sources. **Shell:** Claude Code's own debug log line,
`Hooks: <Event> [cmd] (plugin chela@inline) finished … (N ms)`. **Mod:** the debug log's
`hooks module chela-hooks-mod@inline <event> settled in N ms`, taken from **Mod-only**
sessions. With both layers loaded, the Mod's `settled` time includes `next()`, and below
`next()` sit the shell hooks. For `tool.call`, `settled` also includes the tool's own run,
so the Mod sends `X-Chela-Mod-Pre-Ms` instead: the time from hook entry until it hands off
to the tool, which is what the agent waits for.

Single-layer sessions: shell-only ×3 and Mod-only ×3, each with a restart leg.

| Hook | Shell plugin: median / p95 (n) | Mod: median / p95 (n) |
|---|---|---|
| PreToolUse, **non-Bash** tool | 7.0 / 17.0 ms (57, curl)¹ | **0.5 / 1.0 ms** (12) |
| PreToolUse, **Bash** (post + gate) | 26.0 / 32.0 ms (45, gate)¹ | 26.0 / 33.0 ms (46) |
| PostToolUse | 7.5 / 13.0 ms (42) | **2.8 / 5.0 ms** (41) |
| PermissionDenied | 14.0 / 17.0 ms (3) | 4.6 / 9.7 ms (5) |
| SessionStart | 28.5 / 57.0 ms (6) | 3.2 / 3.7 ms (6) |
| Stop | `http` hook, no spawn² | 1.4 / 2.1 ms (6) |
| SessionEnd | `http` hook, no spawn² | 1.4 / 2.3 ms (6) |

¹ Claude Code runs a tool's matching hooks in parallel, so a shell Bash call costs
max(curl, gate), which is about the gate's 26 ms. A non-Bash call costs the curl alone.
The brief's earlier "~6.8 ms each" for the curl hook reproduces: 7.0 ms median here.
² Stop and SessionEnd already ride Claude Code's in-process `http` client. The debug log
gives no duration for them.

**How to read it.** For a non-Bash tool call, the agent's wait drops from about **7 ms to
under 1 ms**, and the classic events drop from 7–28 ms to 1–5 ms. **The Bash gate gains
nothing (26 ms vs 26 ms).** Python start-up dominates both, and the Mod still starts the
same script. Only a TypeScript port of `mergegate.py` would make the gate cheaper, and §1
gives the reason not to do one.

Posts are not awaited, so the agent never waits for delivery. From hook entry to the
recorder receiving the POST took **2.3 ms median, 7.8 ms p95, 48 ms max** over 336 posts.

With both layers loaded, one outlier stood out: a Bash pre-`next()` time of 1435 ms. That
was a `$.process.run` start under load while another session ran. The Mod-only maximum was
38 ms.

## 3. Delivery: events delivered vs. missed

Six sessions loaded **both** layers: five scripted, plus the interactive one with the hot
reload. Each POST was recorded with its source, and for every session the two streams were
compared as multisets keyed by `(event, tool_use_id | source | reason)` over the six events
both layers post:

| | Shell plugin | Mod |
|---|---|---|
| events (the 6 shared types) | 192 | 214 |
| present in the shell stream, **missing from the Mod's** | — | **0** |
| present in the Mod's stream only | — | 22 PreToolUse, **all** of them gate denies |
| PermissionDenied | 9 | 9 |
| SessionStart `startup` / `resume` | 9 / 8 | 9 / 8 (all 17 sessions) |

The 22 Mod-only events are exactly the 22 gate denies that `mergegate.log` recorded for
those sessions. They come from how the two layers stack, and no event was lost. A
`tool.call` deny ends the call **above** the classic hook chain, so the shell plugin's
PreToolUse hook never ran for a call the Mod had already denied. On its own, each layer
posts denied calls. The Mod does it deliberately: PreToolUse is posted from `tool.call`,
not from `classic.PreToolUse`, so that the denied calls are posted too.

## 4. Deny correctness, restart and hot reload

* **Deny correctness.** The workload sent the same five commands every gate must refuse to
  every leg: `gh pr merge 5 --squash`, `git push origin HEAD:dev`, `git push origin main`,
  `gh pr merge 6` on the resume leg, and `gh api -X PUT …/merge`. It also sent the near
  misses: `gh pr view`, a feature-branch push, and a heredoc that only *mentions* a merge.
  `mergegate.log` shows the **same four-command deny set** in each of the 11 legs: shell
  only, Mod only, and both. There were 0 `undecided` entries.
  `gh api -X PUT …/pulls/5/merge` is not in that set because, in this fixture, the
  auto-mode classifier refused it first ("Merge Without Review"). Its gate verdict is
  checked against the real script in §5 instead.
  Every gate command ran against a fixture repo: a local bare remote, a stub `gh` on
  `PATH`, and a scratch `CHELA_DIR`. **No real PR was touched.**
* **Restart.** Eight `--resume` legs produced `SessionStart(source=resume)` on both layers,
  and the gate held after each restart (`gh pr merge 6` was denied every time).
* **Hot reload.** Editing the Mod mid-session reloaded it in **24 ms** (debug:
  `hooks module chela-hooks-mod@inline reloaded in 24.0ms`). After the reload the next
  turn's events reached both streams, with 0 lost, and the gate denied
  `git push origin HEAD:dev`. The reload re-runs `register`, but **`classic.SessionStart`
  does not fire again**, so module state that was filled at session start is gone until
  another event refills it. That is how the transcript-path cache in §4.1 empties.

### 4.1 API gaps found

1. **`classic.PreToolUse` carries none of the base fields.** Its `e` is the tool envelope
   only, with no `session_id`, `cwd`, `transcript_path` or `permission_mode`. The Mod
   builds them from `$.session.id()`, `$.session.cwd()` and the last `transcript_path` any
   other event carried. Two things differ from the shell payload as a result.
   **`permission_mode` is absent.** ingest does not need it, but it keeps it in the
   payload. And **`$.session.cwd()` is the session's directory, not the Bash tool's
   current directory.** A `cd` that persists across Bash calls moves the shell payload's
   `cwd` but not the Mod's, so the gate resolves the repo from a different directory than
   the shell hook would. A migration would need Claude Code to add the base fields to
   `classic.PreToolUse`, or to expose the Bash tool's working directory.
2. **`classic.*` hooks are skipped in org-managed installs.**
   `claude plugin validate` says so: *"hooks on classic.SessionStart … do not run in a
   plugin a person installs where an organization manages Claude Code: there, these events
   pass over the plugins you install."* For an adopter whose Claude Code is
   managed-settings controlled, the event log would stop **without saying so**. This is
   the main blocker.
3. **`PermissionDenied` IS exposed** (`classic.PermissionDenied`, with `reason`), and it
   fires in both layers on an auto-mode classifier denial (9 = 9 above). Under
   `--permission-mode dontAsk` a refused call fires **no** PermissionDenied in either
   layer. That is a Claude Code fact, not a Mod gap. The classifier-denial roadmap item can
   therefore be read directly in a Mod.
4. **`claude plugin test` cannot run real processes or a real network.** Every `$` call is
   answered by the test's own hooks. So the Mod's tests prove that it hands the gate the
   right payload and relays the answer, and pytest proves that the answer is right (§5).
   The validator also constrains how the code is written: `on()` needs a literal event
   name and a literal function, and any helper that takes `$` must be a top-level function.
5. **No `.catch`.** A Mod hook that throws is skipped, which fails OPEN. That matches the
   shell hook's `|| true`, so it was kept on purpose. A guard that must fail closed would
   need `.catch(() => ({ deny }))`.

## 5. Guards (tests)

* `mods/chela-hooks/hooks/register.test.ts`: 29 `claude plugin test` cases. The deny fires
  for each of the 9 deny-set commands, and **not** for the 6 near misses (`gh pr view`,
  `gh pr create`, a push of `cmx-12`, a heredoc, `chela merge`, a private-socket tmux
  kill). It fails OPEN in all 4 undecidable shapes. Each of the 5 events is posted
  verbatim to `/hooks/<Event>`. Nothing is posted without `$CHELA_WID`, and the URL
  defaults to `127.0.0.1:5001`.
  **Each was checked RED under its mutation:** 12 of 12 mutations of `register.ts` killed
  against a green baseline.
* `tests/test_mods_spike.py` is the contract shared with the existing plugin's tests. It
  reads the **same** `cases.ts` fixture and does three things. It runs every gate case
  through the real `plugin/hooks/mergegate.py`, fed the exact payload the Mod builds. It
  runs every posted body through `chela.hooks.ingest`, checking the type and the summary.
  And it runs the Mod's own suite whenever a Claude Code ≥ 2.1.301 is on `PATH`; CI has
  none, so there it skips.

## 6. Version floor, cost of adoption, and what was not measured

* **Floor: Claude Code 2.1.301**, the first build with Mods, `classic.*` hooks and
  `$.http.fetch`. The shell plugin works from the 2.1.152-era hook set, with MessageDisplay
  as its newest dependency. Migrating would break every adopter on an older Claude Code.
  Keeping both layers means maintaining two implementations of the same hooks.
* **Not covered by this run:** idle hours, compaction (`PreCompact`/`PostCompact`),
  subagents, PermissionRequest's 120 s gate wait (the Telegram answer path),
  SessionStart's recap `additionalContext`, MessageDisplay timestamps, an org-managed
  install (gap 2 comes from the validator's statement, not from an experiment), and a real
  chela-managed pane.

## 7. Recommendation

1. **Keep `plugin/` as the baseline.** It works on every Claude Code chela supports and in
   managed installs.
2. **GO for the roadmap's UI items as an opt-in Mod**, loaded only where
   `claude --version ≥ 2.1.301`. That covers `$.ui.status` replacing the statusLine
   installer, a band for the terminal-wall timestamps (retiring the CMX-277/283
   `displayContent` workaround), the approvals / "needs you" band in the orchestrator, and
   classifier-denial reporting read straight from `classic.PermissionDenied` (CMX-155).
   None of these has a shell-hook equivalent, so their only cost is the version floor, and
   that cost falls only on whoever opts in.
3. **Revisit the event-posting swap** once two conditions hold. Claude Code must add base
   fields to `classic.PreToolUse` (or expose the Bash tool's working directory), and chela
   must have an answer for managed installs. The 6–7 ms saved on each non-Bash tool call is
   real, but it is too small to justify a silent outage.
4. **Do not port the gate to TypeScript** for speed. In both layers its cost is Python
   start-up, and a second parser is the drift the gate's single source of truth exists to
   prevent.

## 8. Status line Mod (CMX-181) and the long soak

CMX-181 shipped item 1 of §7.2: the status line through `$.ui.status`, with the dashboard's
context cache written through the same `scripts/cache-statusline.sh`
([`mods/chela-hooks/README.md`](../mods/chela-hooks/README.md)).

### 8.1 Soak: NOT RUN yet

⛔ **The ≥ 2 h side-by-side soak has not been run.** CMX-181 was a dispatched run, and the
merge gate denies a dispatched run any nested `claude` launch (CMX-190: `$CHELA_RUN_ID` is
set in its pane). The soak needs exactly that: an interactive session with both layers
loaded. A gate denial is final, and routing around it would defeat the guard, so the soak
was left for a session that is not a dispatched run (the orchestrator or a human). **No
numbers below are measured.** Until the table is filled in, §6's "not covered" list
still stands for idle periods, compaction and subagents.

**Protocol.** Use the appendix's harness: the recorder on port 5099, the shell plugin
rendered with `hooks.render_plugin(dir, port=5099)`, and
`CHELA_MOD_HOOKS_URL=http://127.0.0.1:5099/mod`. Run it in a private tmux socket
(`tmux -L chelatest-soak`) with `CHELA_WID` set and `CHELA_DIR` pointing at a scratch
directory, and pass `--debug-file`. Over at least 2 h:

1. a few tool-using turns (Bash, Read, Edit);
2. **idle ≥ 30 min**, twice;
3. a `/compact`, then a turn;
4. a turn that spawns a **subagent** (the Task/Agent tool) that runs tools of its own;
5. a `--resume` restart, then a turn.

Every 5 min, capture the pane with `tmux capture-pane -pe` and record the status line.
Afterwards, compare the two recorder streams as in §3. Also check that
`$CHELA_DIR/context/by-session/<sid>.json` updated after every turn and that
`statusline-errors.log` is empty.

| Check | Expected | Measured |
|---|---|---|
| events in the shell stream missing from the Mod's | 0 | — |
| Mod-only events (not gate denies) | 0 | — |
| status line after idle | unchanged, no flicker or blanking | — |
| status line after `/compact` | context field omitted until the next response, then back | — |
| subagent turns | the line still shows the main session's window and run | — |
| cache file after each turn | rewritten, `session_id` of the main session | — |

### 8.2 What the build found (from tests, not the soak)

* **After a compaction the context field is left out until the next response.**
  `$.session.usage()` has no percent then (its doc says so). The Mod omits the field
  rather than drawing 0%, and the cache it writes then has no `used_percentage`, so the
  reader serves no statusLine snapshot and the transcript estimate takes over.
  `tests/test_mods_status.py` pins that negative control.
* **`messages_k` is not available from a Mod.** `$.session.usage()` reports the total
  input tokens but not the uncached split (`current_usage.input_tokens`), so a
  Mod-written snapshot has no messages segment. Every other field the dashboard reads
  matches the native statusLine exactly (`tests/test_mods_status.py`).
* **Event posting had to become opt-in by URL.** The spike defaulted to
  `127.0.0.1:5001`. With the Mod loaded for its status line in a chela pane, that
  default would have filed every event twice, once from `plugin/` and once from the Mod.

## Appendix: reproducing the run

The harness is a recorder that writes one JSONL line per POST, as `shell` for `/hooks/*`
and `mod` for `/mod/hooks/*`. Pointed at it are the shell plugin, rendered with
`hooks.render_plugin(dir, port=5099)`, and the Mod, with
`CHELA_MOD_HOOKS_URL=http://127.0.0.1:5099/mod`. They run in a session that loads **no
user settings** (`claude --setting-sources project,local --plugin-dir <rendered>
--plugin-dir mods/chela-hooks --debug-file …`), so the user-wide chela plugin and the live
daemon never see the test. The cwd is a fixture repo registered in a scratch
`CHELA_DIR/mergegate.json`, with a local bare remote and a stub `gh` first on `PATH`.

```python
# recorder.py <out.jsonl> <port>
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
OUT = open(sys.argv[1], "a", buffering=1)
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        t = time.time()
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        OUT.write(json.dumps({"t": t, "src": "mod" if self.path.startswith("/mod/") else "shell",
            "event": self.path.rsplit("/", 1)[-1], "t0": self.headers.get("X-Chela-Mod-T0"),
            "pre_ms": self.headers.get("X-Chela-Mod-Pre-Ms"), "session_id": body.get("session_id"),
            "tool_use_id": body.get("tool_use_id"), "source": body.get("source"),
            "reason": body.get("reason")}) + "\n")
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
```

Each scripted session ran a 15-step workload (Bash, Read, Write and Edit, the five gate
commands and three near misses, and one classifier-denied `curl` to a closed port). It was
then restarted with `--resume` for five more steps. Latencies come from `--debug-file`
logs, and denies from `$CHELA_DIR/mergegate.log`.
