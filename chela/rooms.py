"""Agent rooms — the RELATIONSHIP behind an agent-to-agent message.

``chela msg`` fires a string into a pane and vanishes: no record, no kind, no reply
path, no room. Two agents "linked" by it share nothing a UI could draw and nothing a
later turn could re-read — the message is gone the moment tmux renders it. A room is
the missing half: a **typed, durable ledger two or more windows are members of**, plus
**active dispatch** — a targeted ``handoff``/``question``/``blocker`` is injected into
the peer's terminal, so an idle agent wakes, answers, and the answer routes back to the
asker **with no human in the middle**.

**A room is membership + a FILTER over the event log — not a second store.**
``chela/event_log.py`` is already the append-only, ``seq``-ordered, cursor-readable
record ("the ONE authority the UI and the inbox both read"), so every post lands there
as one event (``room_<kind>``), and the ledger is :func:`digest` — a read of the log
filtered by ``payload["room"]``. Membership is the one thing that cannot live there:
it is *mutable* (join, leave) and the log is append-only and **rotates**, so a fold
over the log would silently lose a join the moment it rolled off. It gets a small
durable table of its own (``$CHELA_DIR/rooms.json``, the shape ``inbox.json`` already
uses), and that table holds **no events**.

**Only a TARGETED ``handoff``/``question``/``blocker`` may interrupt.** Everything else
(``status``, ``finding``, ``summary``, ``task``…) is recorded and never injected, and an
*untargeted* post is never injected at all whatever its kind — a fleet where any post
can paste into any pane is an interrupt storm, and the recipient of a status update has
no reply to make.

**The safety rails are the inbox's, reused, not reinvented:**

* ⛔ **Never paste into a ``waiting`` agent.** It is sitting on a permission/question
  prompt and our text would be consumed as the ANSWER to that gate. The post is still
  recorded, and the delivery is PARKED (:func:`flush_pending`) until the gate clears —
  deferred, never dropped. (``busy`` is a fine recipient: Claude Code queues the paste.
  Gating on idle-only is what silently drops a message to a working agent — CMX-47.)
* A recipient is resolved against the **live window table**
  (:func:`chela.messenger.resolve_window` — id or name), never guessed, and an unknown
  or dead one fails **loudly** (exit 1 at the CLI), never silently.
* Messaging **yourself** is refused: that is the loop, closed at the source.

**The loop guard, in three layers — because an echo between two live agents burns a
real machine and real money.** A relays to B, B's reply is injected into A, A relays it
back… Nothing about "be careful" stops that, so it is bounded structurally:

1. **A relayed prompt can never be re-posted.** Every injected prompt opens with a
   fixed, non-localised machine header (:data:`RELAY_HEADER`); :func:`is_relay_text`
   refuses a post whose body starts with one. This kills the naive echo outright.
2. **Hop cap on a chain.** The injected reply command carries ``--reply-to <seq>``; a
   reply inherits its parent's ``chain_id`` and ``hop + 1``, and past
   :data:`MAX_HOPS` the post is *recorded but not delivered*.
3. **Pair rate limit — the backstop that holds even when an agent ignores both.** At
   most :data:`MAX_PAIR_DISPATCHES` injections from one window into another within
   :data:`PAIR_WINDOW_SECONDS`; beyond that a post is recorded, and the wire goes
   quiet. Counted from the log's own ``room_delivery`` events, so it survives a restart.

A tripped guard is never silent: the post lands in the ledger with ``blocked`` naming
the guard, and the CLI says so on stderr.

**The body is UNTRUSTED INPUT being typed into a terminal.** We already inject
keystrokes; a room widens *who* can. So a body is stripped of ANSI escapes and every
control character (:func:`sanitize`), truncated to :data:`MAX_TEXT_CHARS`, and then
**quoted** (``> ``) inside the prompt — a body can therefore never sit at position 0 of
the injected text, which is the only place Claude Code reads a leading ``/`` as a slash
command, and it cannot carry an ``Escape`` or a ``C-c`` into the recipient's TUI.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from chela import agent_manager, discovery, event_log, messenger

log = logging.getLogger(__name__)

# Mosaic's typed room kinds (github.com/emergent-inc/mosaic — the protocol, not the
# wire), spelled the way the rest of chela spells things.
KINDS: tuple[str, ...] = (
    "summary", "task", "decision", "finding", "file_changed", "test_result",
    "blocker", "question", "handoff", "review_finding", "status", "message",
)

# The ONLY kinds that may interrupt a peer, and only when they name a target. A status
# update has no reply to make; a busy fleet that can be pasted into by any post is an
# interrupt storm.
DISPATCH_KINDS: frozenset[str] = frozenset({"handoff", "question", "blocker"})

# The event types a room's ledger is made of — `room_<kind>` for a post, plus the
# delivery record. `type` is the log's own field, so `chela events --type room_question`
# works with no new machinery and no second event source.
POST_TYPES: tuple[str, ...] = tuple(f"room_{k}" for k in KINDS)
DELIVERY_TYPE = "room_delivery"
ROOM_TYPES: tuple[str, ...] = (*POST_TYPES, DELIVERY_TYPE)

# Statuses `claude agents --json` reports (agent_manager.status_by_wid). `waiting` is
# the one that must NEVER be pasted into — see the module docstring.
BUSY, IDLE, WAITING = "busy", "idle", "waiting"

# The machine header every injected prompt opens with. Fixed and non-localised ON
# PURPOSE: it is an agent-to-agent protocol marker, not UI, and `is_relay_text` is the
# first layer of the loop guard. Changing it is a protocol change.
RELAY_HEADER = "[chela room]"

# Loop guard, layers 2 and 3 (see the module docstring).
MAX_HOPS = 6
MAX_PAIR_DISPATCHES = 6
PAIR_WINDOW_SECONDS = 300.0

# A dispatched body is real working content (a diff, a schema, an answer), so this is
# sized for one — but it is bounded, because it is typed into a terminal.
MAX_TEXT_CHARS = 4000

# A delivery parked at a gate is not immortal: an agent can sit `waiting` for hours, and
# a question that stale is worse than no question. Parked deliveries expire, loudly.
PENDING_TTL_SECONDS = 3600.0
MAX_PENDING_PER_WID = 20

_ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$", re.IGNORECASE)
# ANSI/OSC escape sequences first (they start with ESC, which the control-char pass
# below would otherwise strip, leaving the payload `[31m` as visible garbage).
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# Every C0 control except \n and \t, plus DEL and the C1 block. These are keystrokes,
# not text: a raw \x03 in a body is a Ctrl-C aimed at the recipient's TUI.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


# --- the membership table (rooms.json — NO events live here) ---------------------

def store_path() -> Path:
    # ``config.CHELA_DIR`` per call, never latched at import — see event_log.log_path().
    from chela import config
    return Path(os.environ.get("CHELA_ROOMS_FILE") or (config.CHELA_DIR / "rooms.json"))


def _empty() -> dict:
    return {"rooms": {}, "pending": {}}


def load() -> dict:
    """Read the durable table. A missing/corrupt file reads as empty, never raises."""
    try:
        data = json.loads(store_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    base = _empty()
    base.update({k: data[k] for k in base if isinstance(data.get(k), dict)})
    return base


def save(store: dict) -> None:
    """Persist atomically (tmp + rename) so a crash can't truncate the membership."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(path)


@contextmanager
def locked_store():
    """Read-modify-write under an exclusive lock — same reason ``inbox`` has one.

    The daemon flushes parked deliveries while an agent posts from its own session;
    a plain load→modify→save between the two loses one of them.
    """
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            store = load()
            yield store
            save(store)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def create(room: str) -> dict:
    """Create an empty room. Idempotent — creating an existing room is not an error."""
    room = (room or "").strip()
    if not _ROOM_RE.match(room):
        return {"ok": False, "error": f"invalid room id: {room!r} "
                                      "(letters, digits, . _ - ; 1-40 chars)"}
    with locked_store() as store:
        existed = room in store["rooms"]
        if not existed:
            store["rooms"][room] = {"created": time.time(), "members": {}}
    return {"ok": True, "room": room, "created": not existed}


def join(room: str, wid: str) -> dict:
    """Add a live window to a room. The window is resolved, never guessed."""
    target = messenger.resolve_window(wid)
    if target is None:
        return {"ok": False, "error": f"{wid} is not a live window — cannot join"}
    result = create(room)
    if not result["ok"]:
        return result
    name = discovery.get_windows_by_id().get(target, target)
    with locked_store() as store:
        store["rooms"][room]["members"][target] = {"name": name, "joined": time.time()}
    return {"ok": True, "room": room, "wid": target, "name": name}


def leave(room: str, wid: str) -> dict:
    """Remove a window from a room. A *gone* window can still leave (by id)."""
    target = messenger.resolve_window(wid) or wid
    with locked_store() as store:
        members = (store["rooms"].get(room) or {}).get("members", {})
        existed = members.pop(target, None) is not None
    return {"ok": existed, "room": room, "wid": target}


def members(room: str) -> dict[str, dict]:
    return dict((load()["rooms"].get(room) or {}).get("members", {}))


def rooms() -> dict[str, dict]:
    return dict(load()["rooms"])


def rooms_for(wid: str) -> list[str]:
    """Every room this window is a member of."""
    return sorted(r for r, meta in load()["rooms"].items()
                  if wid in (meta.get("members") or {}))


# --- the body: untrusted input, on its way into a terminal -----------------------

def sanitize(text: str) -> str:
    """Strip ANSI escapes and control characters; collapse CRLF; cap the length.

    A room body is typed into someone else's TUI. A raw ``\\x1b`` or ``\\x03`` in it is
    not text — it is a keypress aimed at that agent's Claude Code prompt. Newlines and
    tabs survive (real content has them; the paste path handles newlines); everything
    else in the control range does not.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip() + "…"
    return text


def is_relay_text(text: str) -> bool:
    """Is this body a prompt WE injected into someone? Loop guard, layer 1.

    An agent that answers a room prompt by pasting the prompt back is the echo loop.
    The header is fixed and non-localised precisely so this check is exact.
    """
    return (text or "").lstrip().startswith(RELAY_HEADER)


def build_prompt(post: dict, recipient_wid: str) -> str:
    """The text injected into the recipient's terminal. Machine protocol, not UI.

    Opens with :data:`RELAY_HEADER` (so a re-post of it is refused), quotes the body
    (so a leading ``/`` can never reach the TUI as a slash command), and — for a
    ``question`` — carries the exact command that routes the answer BACK to the asker,
    with ``--from`` pinned to the recipient so the reply is attributed to the window
    that actually answered rather than to whatever the shell guesses.
    """
    payload = post["payload"]
    body = "\n".join("> " + line for line in (payload["text"] or "").splitlines())
    head = (f'{RELAY_HEADER} {payload["kind"]} from {payload["from_wid"]}'
            f' ({payload["from_name"]}) in room "{payload["room"]}" (post #{post["seq"]}):')
    lines = [head, body, ""]
    if payload["kind"] == "question":
        lines += [
            "Answer by posting back to the asker — this wakes them, with no human in "
            "the middle:",
            f'  chela room post {payload["room"]} --kind handoff --from {recipient_wid} '
            f'--to {payload["from_wid"]} --reply-to {post["seq"]} -- "<your answer>"',
        ]
    else:
        lines.append("Respond or continue from this room update.")
    lines.append(f"Do NOT relay this message onward: a post whose body starts with "
                 f'"{RELAY_HEADER}" is refused, and a chain is capped at {MAX_HOPS} hops.')
    return "\n".join(lines)


# --- the ledger: a filter over the event log (there is no second store) ----------

def _ledger(limit: int | None = None) -> list[dict]:
    return event_log.read(types=list(ROOM_TYPES), limit=limit)["events"]


def digest(room: str, limit: int | None = None) -> list[dict]:
    """This room's ledger, oldest-first: every post and delivery, read from the LOG."""
    out = [e for e in _ledger() if (e.get("payload") or {}).get("room") == room]
    return out[-limit:] if limit else out


def _find_post(seq: int) -> dict | None:
    for event in _ledger():
        if event.get("seq") == seq and event.get("type") in POST_TYPES:
            return event
    return None


def _recent_dispatches(from_wid: str, to_wid: str, now: float) -> int:
    """How many times ``from_wid`` has already been injected into ``to_wid`` lately.

    Counted from the log's ``room_delivery`` events — the durable record of what was
    actually pasted — so the backstop survives a daemon restart and cannot be reset by
    an agent simply starting a fresh chain.
    """
    cutoff = now - PAIR_WINDOW_SECONDS
    n = 0
    for event in _ledger():
        if event.get("type") != DELIVERY_TYPE or (event.get("ts") or 0) < cutoff:
            continue
        p = event.get("payload") or {}
        if p.get("from_wid") == from_wid and p.get("to_wid") == to_wid:
            n += 1
    return n


# --- post: record, then (maybe) dispatch -----------------------------------------

def _blocked_reason(from_wid: str, to_wid: str, hop: int, now: float) -> str | None:
    """Which loop guard, if any, forbids injecting this post into ``to_wid``."""
    if hop > MAX_HOPS:
        return f"chain hop limit ({MAX_HOPS}) reached"
    seen = _recent_dispatches(from_wid, to_wid, now)
    if seen >= MAX_PAIR_DISPATCHES:
        return (f"pair rate limit ({MAX_PAIR_DISPATCHES} deliveries "
                f"{from_wid}->{to_wid} in {int(PAIR_WINDOW_SECONDS)}s) reached")
    return None


def _record_delivery(post: dict, to_wid: str, *, parked: bool = False) -> None:
    """The durable record that a post was actually pasted into a window.

    Its own event, never a mutation of the post (the log is append-only), and the thing
    :func:`_recent_dispatches` counts. ``delivered`` carries the recipient so the Feed
    and the rate limit read one shape whether the delivery was immediate or parked.
    """
    p = post["payload"]
    via = " (gate cleared)" if parked else ""
    event_log.append(
        DELIVERY_TYPE,
        f'📮 {p["kind"]} #{post["seq"]} delivered to {to_wid}{via} — room "{p["room"]}"',
        {"room": p["room"], "kind": p["kind"], "from_wid": p["from_wid"],
         "to_wid": to_wid, "delivered": [to_wid], "post_seq": post["seq"],
         "chain_id": p.get("chain_id"), "hop": p.get("hop"), "parked": parked},
        wid=to_wid,
    )


def post(room: str, kind: str, text: str, *, from_wid: str, targets: list[str] | None = None,
         reply_to: int | None = None, statuses: dict[str, str] | None = None) -> dict:
    """Post to a room: ALWAYS recorded; injected only if targeted and interruptible.

    Returns ``{ok, error?, seq, delivered, deferred, blocked}``. ``ok=False`` is a
    refusal (bad kind, dead recipient, self-target, a relayed body) and nothing is
    recorded; a *guard* trip is ``ok=True`` with a ``blocked`` reason — the post is in
    the ledger, it simply did not wake anyone.
    """
    kind = (kind or "").strip()
    if kind not in KINDS:
        return {"ok": False, "error": f"unknown kind {kind!r} — one of: {', '.join(KINDS)}"}
    if room not in rooms():
        return {"ok": False, "error": f"no such room: {room!r} (create it first)"}
    if not from_wid:
        return {"ok": False, "error": "no sender window (pass --from @N, or run inside a "
                                      "chela-spawned agent so $CHELA_WID is set)"}
    body = sanitize(text)
    if not body:
        return {"ok": False, "error": "empty message body"}
    if is_relay_text(text):
        # Loop guard, layer 1: this body IS a prompt we injected into someone. Posting
        # it back is the echo loop, and it is refused at the source.
        return {"ok": False, "error": "refusing to post a relayed room prompt back into "
                                      "the room (that is the echo loop) — post your own "
                                      "answer, not the message you received"}

    member_wids = members(room)
    if from_wid not in member_wids:
        return {"ok": False, "error": f"{from_wid} is not a member of room {room!r} "
                                      f"— `chela room join {room} --wid {from_wid}` first"}

    resolved: list[str] = []
    for target in targets or []:
        wid = messenger.resolve_window(target)
        if wid is None:
            live = ", ".join(f"{w} {n}" for w, n in sorted(discovery.get_windows_by_id().items()))
            return {"ok": False, "error": f"{target} is not a live window — NOT delivered."
                                          f"\nlive windows: {live or '(none)'}"}
        if wid == from_wid:
            return {"ok": False, "error": f"{target} resolves to your own window ({wid}) "
                                          "— refusing to message myself (that is a loop)"}
        if wid not in member_wids:
            return {"ok": False, "error": f"{wid} is not a member of room {room!r} "
                                          f"— `chela room join {room} --wid {wid}` first"}
        if wid not in resolved:
            resolved.append(wid)

    now = time.time()
    parent = _find_post(reply_to) if reply_to else None
    chain_id = ((parent or {}).get("payload") or {}).get("chain_id") or uuid.uuid4().hex[:8]
    hop = int(((parent or {}).get("payload") or {}).get("hop") or 0) + (1 if parent else 0)
    # An interrupt is a TARGETED handoff/question/blocker and nothing else. An untargeted
    # post — of any kind — is recorded for the room to read, never pasted into a pane.
    dispatchable = bool(resolved) and kind in DISPATCH_KINDS

    from_name = discovery.get_windows_by_id().get(from_wid, from_wid)
    summary = (f'🔌 {kind} from {from_wid} in room "{room}"'
               f'{" -> " + ", ".join(resolved) if resolved else ""} — {body.splitlines()[0][:80]}')
    record = event_log.append(
        f"room_{kind}", summary,
        {"room": room, "kind": kind, "from_wid": from_wid, "from_name": from_name,
         "text": body, "targets": resolved, "chain_id": chain_id, "hop": hop,
         "reply_to": reply_to, "dispatch": dispatchable},
        wid=from_wid,
    )
    if record is None:
        return {"ok": False, "error": "event log append failed — post NOT recorded"}

    result = {"ok": True, "seq": record["seq"], "room": room, "kind": kind,
              "chain_id": chain_id, "hop": hop, "delivered": [], "deferred": [],
              "blocked": [], "failed": []}
    if not dispatchable:
        return result

    if statuses is None:
        statuses = agent_manager.status_by_wid()

    for wid in resolved:
        reason = _blocked_reason(from_wid, wid, hop, now)
        if reason:
            log.warning("rooms: NOT delivering %s #%s to %s — %s", kind, record["seq"],
                        wid, reason)
            result["blocked"].append({"wid": wid, "reason": reason})
            continue
        prompt = build_prompt(record, wid)
        if statuses.get(wid) == WAITING:
            # ⛔ The inbox's rule, reused: a `waiting` session sits on a permission/
            # question prompt and our paste would ANSWER it. Park the delivery instead —
            # deferred, never dropped (flush_pending sends it when the gate clears).
            _park(wid, record, prompt)
            result["deferred"].append(wid)
            continue
        if messenger.send_tmux(wid, prompt):
            _record_delivery(record, wid)
            result["delivered"].append(wid)
        else:
            log.warning("rooms: tmux send to %s failed — %s #%s not delivered",
                        wid, kind, record["seq"])
            result["failed"].append(wid)
    return result


# --- parked deliveries: the recipient was at a gate -------------------------------

def _park(wid: str, post_event: dict, prompt: str) -> None:
    p = post_event["payload"]
    entry = {"post_seq": post_event["seq"], "room": p["room"], "kind": p["kind"],
             "from_wid": p["from_wid"], "prompt": prompt, "ts": time.time()}
    with locked_store() as store:
        queue = store["pending"].setdefault(wid, [])
        queue.append(entry)
        if len(queue) > MAX_PENDING_PER_WID:
            dropped = queue.pop(0)
            log.warning("rooms: pending queue for %s is full — dropping %s #%s",
                        wid, dropped["kind"], dropped["post_seq"])
    log.info("rooms: %s #%s parked for %s (it is at a gate — never paste into `waiting`)",
             p["kind"], post_event["seq"], wid)


def has_pending() -> bool:
    """Cheap check for the daemon loop: is anything parked at all?"""
    return any(load()["pending"].values())


def pending() -> dict[str, list[dict]]:
    return dict(load()["pending"])


def flush_pending(statuses: dict[str, str] | None = None) -> list[dict]:
    """Deliver parked posts whose recipient has left its gate. Returns what went out.

    A post parked because its recipient was ``waiting`` is not lost: as soon as that
    window is live and no longer at a gate, it is pasted and a ``room_delivery`` lands
    in the log. An entry older than :data:`PENDING_TTL_SECONDS` is dropped LOUDLY — a
    question that stale is worse than no question, and a silent drop is the bug this
    whole subsystem exists to stop.
    """
    store = load()
    if not any(store["pending"].values()):
        return []
    if statuses is None:
        statuses = agent_manager.status_by_wid()
    live = discovery.get_windows_by_id()
    now = time.time()

    # The pasting happens OUTSIDE the lock (tmux is slow, and locked_store()'s one rule is
    # that nothing slow happens inside it), so `resolved` collects exactly the entries this
    # pass settled — delivered or deliberately dropped — and only those are removed from
    # the store afterwards. A post that parks a NEW entry while we paste is left alone.
    sent: list[dict] = []
    resolved: set[tuple[str, int]] = set()
    for wid, queue in sorted(store["pending"].items()):
        for entry in queue:
            key = (wid, entry["post_seq"])
            if now - (entry.get("ts") or 0) > PENDING_TTL_SECONDS:
                log.warning("rooms: dropping parked %s #%s for %s — expired (>%ds at a gate)",
                            entry["kind"], entry["post_seq"], wid, int(PENDING_TTL_SECONDS))
                resolved.add(key)
                continue
            if wid not in live:
                log.warning("rooms: dropping parked %s #%s — %s is gone",
                            entry["kind"], entry["post_seq"], wid)
                resolved.add(key)
                continue
            if statuses.get(wid) == WAITING:
                continue                   # still at the gate — it waits, it is not lost
            if not messenger.send_tmux(wid, entry["prompt"]):
                log.warning("rooms: parked delivery to %s failed; leaving it queued", wid)
                break                      # tmux is unhappy — try this window again next tick
            resolved.add(key)
            sent.append({**entry, "to_wid": wid})

    with locked_store() as fresh:
        for wid, queue in list(fresh["pending"].items()):
            kept = [e for e in queue if (wid, e["post_seq"]) not in resolved]
            if kept:
                fresh["pending"][wid] = kept
            else:
                fresh["pending"].pop(wid)

    for entry in sent:
        record = _find_post(entry["post_seq"])
        if record is not None:
            _record_delivery(record, entry["to_wid"], parked=True)
    return sent


def status(room: str | None = None) -> dict:
    """What rooms exist, who is in them, and what is parked — the CLI's view."""
    store = load()
    live = discovery.get_windows_by_id()
    out = {"rooms": {}, "pending": store["pending"]}
    for name, meta in sorted(store["rooms"].items()):
        if room and name != room:
            continue
        out["rooms"][name] = {
            "created": meta.get("created"),
            "members": {wid: {**info, "live": wid in live,
                              "name": live.get(wid, info.get("name", wid))}
                        for wid, info in sorted((meta.get("members") or {}).items())},
        }
    return out
