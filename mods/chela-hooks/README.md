# chela-hooks-mod — opt-in chela Mod

A Claude Code **Mod** (Claude Code ≥ 2.1.301) that sits on top of the shell-hook plugin in
[`plugin/`](../../plugin). `plugin/` stays the baseline: it works on every Claude Code chela
supports and in org-managed installs. chela never installs this Mod. You opt in by
loading it.

```bash
claude --plugin-dir /path/to/chelamux/mods/chela-hooks
```

## Status line (CMX-181)

The Mod draws chela's status line under the prompt through `$.ui.status`:

```
ctx ▰▰▰▰▱▱▱▱▱▱ 42% · @7 · orchestrator · run CMX-9 (judge)
```

| Field | Source | Left out when |
|---|---|---|
| context bar | `$.session.usage()`, the figures Claude Code's own statusLine gets | the session has no reading yet (fresh, or just compacted) |
| window id | `$CHELA_WID` | not a chela window |
| `orchestrator` | `$CHELA_ORCHESTRATOR_WID`, else `inbox.json`'s `orchestrator` (what `chela watch` records) | another window is the orchestrator, or none is recorded |
| `run <id> (<role>)` | `$CHELA_RUN_ID` / `$CHELA_RUN_ROLE`, which the dispatcher exports | not a dispatched run |

It redraws at session start, after every turn and after a compaction. Each redraw also
pipes the same figures, in the statusLine payload's shape, to
[`scripts/cache-statusline.sh`](../../scripts/cache-statusline.sh). That is the script
`chela install-statusline` wires into `settings.json`, so the dashboard's context bar and
rate-limit pills read the same file either way, and **no `settings.json` edit is needed**
while the Mod is loaded. `tests/test_mods_status.py` proves that a Mod-written cache reads
the same as the native statusLine's.

**Version gate.** Claude Code before 2.1.301 has no Mods and loads nothing from this folder.
The Mod also checks `$.session.version()` and draws nothing below 2.1.301. **Default.**
Without the Mod, `chela install-statusline` is unchanged and remains the documented path.
Running both is harmless: they write the same figures to the same file.

Turn it off without unloading the Mod with the `statusLine` option (`/config`, or
`pluginConfigs` in settings). Off, it draws nothing and writes no cache.

## Turn timestamps (CMX-182)

With `CHELA_TERMINAL_TIMESTAMPS` on, the Mod draws the time of the last assistant reply in
a band above the prompt, in the same local `[HH:MM]` the `plugin/` path writes:

```
[14:05] last reply
```

Without the Mod, `plugin/` still writes `**[HH:MM]**` into the first line of each reply
through `MessageDisplay`'s `displayContent` (CMX-277/283/285), unchanged. While the band
is on, the Mod adds `"chela_timestamp_owner": "mod"` to every `MessageDisplay` payload it
passes on, and the daemon answers that payload with `{}`, so a reply never shows its time
in both places. The fixture both sides test against is `hooks/timestamps-cases.ts`
(`tests/test_mods_timestamps.py` runs it through the real endpoint).

| Case | Band | `plugin/` marker |
|---|---|---|
| Mod loaded, its flag on | the last reply's time | none (the payload carries the handshake) |
| Mod loaded, its flag unset or off (`false`, `0`, `no`, `off`) | none | as without the Mod |
| Mod not loaded, or Claude Code below 2.1.301 | none | as without the Mod |
| an org-managed install | none | as without the Mod (`classic.MessageDisplay` does not reach a person's plugins there) |

"As without the Mod" means the daemon's own `CHELA_TERMINAL_TIMESTAMPS` decides. The Mod
reads the flag from the Claude Code process's environment and the daemon from its own,
so set it in both.

## Needs you (CMX-183)

In the orchestrator session only (the window `chela watching` names), the Mod draws one
line above the prompt with what is waiting on you: pending override approvals, pending
access requests and runs in `needs_human`. It shows the counts and the oldest item, each a
link to where the dashboard shows it (an approval's `/override/<id>` page, else the
dashboard):

```
needs you: 1 approval · 1 access request · 1 run in needs_human · oldest: access request mount /data (ro) (20m)
```

It reads the daemon's `GET /api/needs-you` on `127.0.0.1:$CHELA_DASHBOARD_PORT` (default
5001) every 10 s, from a timer: no tool call waits on it. It is read-only. Approving stays
in Telegram, the dashboard or a plain terminal.

| Case | Band |
|---|---|
| the orchestrator session, something waiting | counts plus the oldest |
| nothing waiting | none |
| any other session (another window is the orchestrator, none recorded, not a chela window) | none, and the daemon is never asked |
| the daemon is down, answers 503 (a store it cannot read) or anything but the documented shape | none |
| the last good reading is more than 30 s old (the daemon hangs) | none: a count is never shown stale |
| Claude Code below 2.1.301 | none |

The orchestrator is found as the status line finds it: `$CHELA_ORCHESTRATOR_WID`, else
`inbox.json`'s `orchestrator`. The fixture both sides test against is
`hooks/needsyou-cases.ts` (`tests/test_mods_needsyou.py` runs it through the real route).

## Hook spike (CMX-157)

The merge gate and event posting, measured side by side with `plugin/` in
[`docs/MODS_SPIKE.md`](../../docs/MODS_SPIKE.md). The gate runs whenever the Mod is loaded:
it runs the same `mergegate.py` as `plugin/`, so the two always agree. Events are posted
**only when `CHELA_MOD_HOOKS_URL` is set**. In a chela pane `plugin/` already posts every
event, and a default URL would file each one twice.

| Variable | Default | Meaning |
|---|---|---|
| `CHELA_WID` | unset ⇒ nothing is posted | the window the events are filed under (the gate runs regardless) |
| `CHELA_MOD_HOOKS_URL` | unset ⇒ nothing is posted | where `POST /hooks/<Event>` goes, e.g. `http://127.0.0.1:5001` |
| `CHELA_MOD_MERGEGATE` | `<mod>/../../plugin/hooks/mergegate.py` | the gate script it runs |
| `CHELA_MOD_STATUSLINE` | `<mod>/../../scripts/cache-statusline.sh` | the cache writer the status line pipes to |
| `CHELA_DASHBOARD_PORT` | `5001` | the port the needs-you band asks the daemon on |

Tests: `claude plugin validate mods/chela-hooks && claude plugin test mods/chela-hooks`
(also run by `tests/test_mods_spike.py` when a Mods-capable `claude` is on `PATH`).
