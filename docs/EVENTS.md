# The event log

chela had nowhere for an event to *live*. `chela msg` / `broadcast` fire into a pane and
vanish, a watch is only a key in `inbox.json`, the delivery queue is transient — so the
only record of "what happened" was prose, pasted into a session. **You cannot render,
filter, thread or reconcile prose**, and that is the root cause behind a whole family of
inbox bugs: a state that was *inferred* rather than observed, a snapshot with no expiry,
a notification that was an entire task brief.

`chela/event_log.py` is the durable, ordered place a fact goes instead: an append-only
JSONL under `$CHELA_DIR` (`~/.chela/events.jsonl`), a cursor-based read API, and a CLI.
It is a standalone subsystem with a **programmatic append** — nothing about it needs an
agent, a hook or tmux to exercise.

## The record

One JSON object per line. The `type` / `summary` / `payload` triple is the *same* record
[`chela/inbox.py`](../chela/inbox.py) already queues (its `kind` **is** this `type`) —
deliberately one event schema, so the log and the inbox cannot drift apart:

| field | meaning |
|---|---|
| `seq` | monotonic cursor, allocated under the append lock. Never reused, never reset while the log's state survives. |
| `boot_id` | the epoch a `seq` belongs to. Changes on daemon start and on a lost/rebuilt seq space — see below. |
| `ts` | unix time of the append. |
| `type` | what happened (`run_review`, `died`, `daemon_start`, …). |
| `wid` | the chela window, when known. |
| `session_id` | the Claude Code session, when known. |
| `summary` | **one line** — what a notification renders. |
| `payload` | the structured facts — run id, PR url, the full title, timestamps. |

The summary/payload split is the point: a notification is a line, and the essay belongs
in something the reader can open, filter and re-check.

## Reading it

```bash
chela events                                   # the log
chela events --type run_review --wid @3        # filtered
chela events --after-seq 41 --after-boot 9f2a  # replay from a cursor
chela events --follow                          # tail it live
chela events --json                            # one JSON object per line, for a pipe
chela events emit --type note --summary "hi" --payload '{"k":1}'   # the append, from a shell
```

Resume from the `next_seq` a read hands back, not from the last seq you *saw*: `--limit`
returns the **oldest** N events after the cursor (never the newest), so a bounded read is
resumable and cannot skip an event you have not seen. A filtered read still advances
`next_seq` past the events it dropped, so a reader following one `type` does not re-scan
the log forever.

### …and over HTTP: `GET /api/log`

The dashboard reads the log through **`/api/log`** — a thin wrapper over the same
`event_log.read()` the CLI calls, with the same cursor, the same filters, and `gap` +
`next_seq` passed straight through. One reader, two front ends; two readers would be two
truths.

```
GET /api/log?after_seq=41&after_boot=9f2a&type=run_review&wid=@3&limit=200
```

⛔ **Not `/api/events`** — that path is the dashboard's SSE *delta-notification* stream,
which carries no data at all. When the log's `seq` moves, that stream pushes a small
`log` frame carrying **only the new seq**, and the client fetches `/api/log` from its own
cursor. So a dropped frame loses nothing, and there is no second event source: if the
stream never connects, the view's poll timer covers the gap exactly as it did before.

## Retiring the log (an operator step)

```bash
chela events rotate          # dry run — says what it would do
chela events rotate --yes    # renames events.jsonl → events.jsonl.bak, mints a new boot_id
```

Rows can be *known-wrong* rather than merely old — everything written before the `wid`
correlation fix carries the wrong window id — and a UI must never render known-wrong rows.
This is how you retire them: the file is **renamed, never unlinked** (the decision is
reversible), `seq` stays monotonic, and the `boot_id` moves so every reader holding a
cursor into the retired file is told about the **gap** instead of resuming into an empty
log as though nothing had happened.

It is deliberately **not** something a process does at boot. A log that silently wipes
itself when a daemon starts is a log you cannot trust.

## A stale cursor is detectable, not silently wrong

`boot_id` names the epoch a `seq` belongs to. It lives in the state file, shared by every
writer (a per-process id would be meaningless in a multi-writer log), and it changes on
exactly the two things that can invalidate a cursor:

* **daemon start** — while the daemon was down, nothing was listening. A hook fails
  *open* (it must never wedge a live agent), so events from that window were never
  appended: a genuine hole.
* **a lost or corrupt state file** — the `seq` space itself is re-derived.

Pass your remembered `boot_id` back (`--after-boot`) and a read that cannot honour your
cursor returns a **`gap`** — a boot change, a cursor ahead of the log, or events that are
no longer served — instead of handing you a plausible-looking, wrong continuation. `seq`
itself never restarts on a boot change: two events must never share an identity.

## Concurrency: a short-held `flock`, with nothing slow inside it

Several processes append at once — the daemon, the dashboard, the CLI, and (next) one
Claude Code hook call per tool use. An `O_APPEND` write of a short line is atomic on
POSIX, but allocating `seq` is a read-modify-write and is **not** — two writers would
hand out the same number. So the append takes an exclusive `flock` across exactly three
small writes:

> read the sidecar → bump it → write one line

and nothing else: no network, no tmux, no second file. The sidecar is bumped **before**
the line lands, so a crash in between loses an event (the reader sees the `seq` hole and
says so) rather than reusing a `seq` — two different events with one identity is the one
failure nothing downstream could untangle.

`append()` never raises. Its next caller is a hook running synchronously *inside a live
agent*, where an exception stalls or breaks that agent: a lost event is a bug, a wedged
agent is an outage.

This is proved, not asserted: `tests/test_event_log.py` hammers the log from N concurrent
writer **processes** (not threads — threads share the interpreter and could not catch the
real hazard) and asserts every line is intact and `seq` is exactly `1..N*M`, with no
duplicates and no gaps.

## Durability

* A **torn final line** (a crash mid-append) is *skipped on read, not fatal*, and counted
  as `corrupt_lines`. The next append closes it first — otherwise one crash would eat the
  next event too, and keep eating them.
* The file **rotates** at `CHELA_EVENTS_MAX_BYTES` (default 8 MB), keeping
  `CHELA_EVENTS_KEEP` generations (`events.jsonl.1`, `.2`, …): an unbounded append is a
  disk-filler on a busy fleet. Live reads come from a bounded in-memory ring
  (`CHELA_EVENTS_RING`, default 2000); the rolled files are the audit trail.

## Configuration

| env | default | what |
|---|---|---|
| `CHELA_EVENTS_FILE` | `$CHELA_DIR/events.jsonl` | the log |
| `CHELA_EVENTS_MAX_BYTES` | `8388608` | rotate at this size |
| `CHELA_EVENTS_KEEP` | `3` | rolled generations to keep |
| `CHELA_EVENTS_RING` | `2000` | in-memory tail served to readers |

## What feeds it

The daemon (`daemon_start`) and the decisions inbox — every event it generates, including
the ones it never delivers. The queue is what the orchestrator is *told*; the log is what
*happened*, and conflating the two is why a bug like the false `DIED` had no history to be
reconciled against.

That includes the inbox reporting **its own** failure to deliver (`inbox_undeliverable`,
classed as a **gate** — it needs a human). A window id is an address, not an identity: tmux
issues `@N` per *server*, so a restart renumbers the fleet and every id chela persisted now
names somebody else. On 2026-07-14 an OOM did exactly that; the inbox queued five
`run_review` events behind a `@0` that no longer existed and delivered none of them, with no
error, no log line and `chela doctor` green — five finished PRs sat unreviewed until a human
noticed. Every persisted id now carries the tmux epoch that issued it (`chela/epoch.py`), one
from a dead epoch is never written to, and the queue being stuck behind it says so here, in
the daemon log, on a phone, and in `chela doctor` (`inbox.address`). A watch whose agent died
with the server is retired as `watch_epoch_lost` — outcome *unknown*, rather than a status
read off the stranger who inherited its number.

And **[Claude Code hooks](HOOKS.md)**, shipped as a plugin: typed events POSTed straight
into the daemon by the agents themselves, namespaced `hook.*` (`hook.permission_request`,
`hook.pre_tool_use`, …). They arrive *before* the fact — a gate lands in the log while the
agent is still blocked on it, with the full `tool_input` attached — which the transcript
cannot do and a pane scrape can only approximate. Ingestion answers nothing; the one hook
that *does* answer — an `AskUserQuestion`, and only with a human's tap — is
[documented where it lives](HOOKS.md#answering-a-question-with-zero-keypresses).

And **[agent rooms](../README.md#agent-rooms--agents-that-can-actually-talk-to-each-other)**
(`chela room`): a room's ledger *is* this log. Every post is one event — `room_question`,
`room_handoff`, `room_status`, … (one per kind) — plus a `room_delivery` when a post is
actually pasted into a peer's terminal, which is also what the loop guard's rate limit
counts. A room is therefore **membership + a filter over this log**, and only the
membership (mutable, and the log rotates) gets a file of its own. Read one with
`chela room digest <room>`, or `chela events --type room_question`.

And the **outbound Telegram relay**, which reports its own silence: a bound window whose
transcript cannot be resolved relays *nothing*, and used to do so with no error, no warning
and every health surface green (CMX-70 — an hour of dead outbound while inbound kept
working, because inbound only needs the `wid`). It now appends `relay.transcript_missing`
when a window stops resolving and `relay.transcript_found` when it resolves again, so an
outage that is invisible by nature has a record you can read back. `chela doctor` asserts
the same fact from the other side (`relay.transcripts`).

This log is the one authority all of them append to, and the one the dashboard timeline
and the inbox both read — there is deliberately no second event source alongside it.
