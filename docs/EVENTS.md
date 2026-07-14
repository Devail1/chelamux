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

And **[Claude Code hooks](HOOKS.md)**, shipped as a plugin: typed events POSTed straight
into the daemon by the agents themselves, namespaced `hook.*` (`hook.permission_request`,
`hook.pre_tool_use`, …). They arrive *before* the fact — a gate lands in the log while the
agent is still blocked on it, with the full `tool_input` attached — which the transcript
cannot do and a pane scrape can only approximate. Ingestion answers nothing; the one hook
that *does* answer — an `AskUserQuestion`, and only with a human's tap — is
[documented where it lives](HOOKS.md#answering-a-question-with-zero-keypresses).

This log is the one authority all of them append to, and the one the dashboard timeline
and the inbox both read — there is deliberately no second event source alongside it.
