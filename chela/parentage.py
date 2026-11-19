"""Session parentage — which session launched which (CMX-389).

Claude Code keeps no parent field (not in ``~/.claude/sessions/*.json``, not in the
transcripts), so chela records one. It is the data half of nesting an orchestrator's child
sessions under it in the sidebar.

**One parent per session, decided once.** Two sources, in order of authority:

1. ``spawn`` — ``chela spawn`` run from inside a session (:func:`caller_session`) names that
   session as the new one's parent. The CLI does not write the store: it appends a
   ``session.spawned`` event (:func:`record_spawn`) and the daemon folds it in.
2. ``first_message`` — a session with no launch record whose FIRST inbound
   ``<cross-session-message>`` arrives within :func:`chela.config.parent_window_minutes` of
   its start gets that message's sender. The sender is identified by the sender's own
   ``SendMessage`` tool call in the event log (same text, sent just before) — never by the
   envelope's ``from`` address, which names a socket, not a session. An ambiguous match
   (two sessions sent that text) or none at all records nothing.

Otherwise no parent: a session the user opens and drives stays top-level. Dispatcher runs
stay out (they have their own Dispatched section): a receiver whose cwd is a run's worktree
is never given a parent.

A parent is never changed once set, a parent that is the session itself or one of its
descendants is refused (no cycles), and so is one already at :func:`chela.config.
parent_max_depth`.

**Keyed by session id, never window id.** Window ids are reissued on every tmux restore
(the CMX-378 lesson); session ids survive it.

**One writer.** ``CHELA_DIR/session-parents.json`` is written by ``chela-daemon``'s tick
(:func:`tick`, which already owns the event-log-fed bookkeeping and holds the tick lock),
and by ``chela parents backfill --apply``, which takes that same tick lock first
(:func:`chela.ticklock.between_ticks`) so the two never interleave.

Store shape::

    {"parents": {"<child sid>": {"parent": "<sid>", "source": "spawn", "ts": 1.0}},
     "state": {"cursor": {"seq": 0, "boot_id": ""}, "starts": {}, "first_seen": {}}}

The ``SendMessage`` text the sender match compares is kept in the daemon's memory only
(:data:`_SENDS`), never in this file.

Rule 2 needs the session's ``startup`` SessionStart in the log: a session that began as a
``--resume``, or whose start rolled out of the log before its first message, is never
inferred.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from chela import config, event_log, sessions

log = logging.getLogger(__name__)

SPAWN = "spawn"
FIRST_MESSAGE = "first_message"

SPAWNED_TYPE = "session.spawned"
_START = "hook.session_start"
_PROMPT = "hook.user_prompt_submit"
_PRE_TOOL = "hook.pre_tool_use"
EVENT_TYPES = [SPAWNED_TYPE, _START, _PROMPT, _PRE_TOOL]

# How far back a receiver's message looks for the sender's own SendMessage call.
SEND_MATCH_SECONDS = 120
# The text compared between a SendMessage call and the message that arrived. Both sides are
# clipped previews in the event log (hooks.PREVIEW_CHARS / TEXT_CHARS), so the comparison
# is on a common prefix, never the whole body.
MATCH_CHARS = 150

_ENVELOPE_RE = re.compile(r"\A\s*<cross-session-message\b[^>]*>")
_CLIP_RE = re.compile(r"…\s*\[\+\d+ chars\]\s*\Z")


def store_path() -> Path:
    """Read per call so a test's ``CHELA_DIR`` redirect always wins."""
    return config.CHELA_DIR / "session-parents.json"


@dataclass(frozen=True)
class Settings:
    window_seconds: float
    max_depth: int


def settings(window_minutes: int | None = None, max_depth: int | None = None) -> Settings:
    """Explicit argument > ``CHELA_PARENT_*`` env > default."""
    wm = config.parent_window_minutes() if window_minutes is None else window_minutes
    md = config.parent_max_depth() if max_depth is None else max_depth
    return Settings(window_seconds=wm * 60.0, max_depth=md)


# --- the store ----------------------------------------------------------------------

def _empty() -> dict:
    return {"parents": {}, "state": {}}


def load() -> dict:
    """The whole store. A missing or unreadable file is an empty one."""
    try:
        data = json.loads(store_path().read_text())
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("parents"), dict):
        return _empty()
    if not isinstance(data.get("state"), dict):
        data["state"] = {}
    return data


def save(data: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def parents() -> dict:
    """``{child sid: {parent, source, ts}}`` — the read side, for ``/api/agents``."""
    return load()["parents"]


def row_fields(session_id: str | None, table: dict) -> dict:
    """The two ``/api/agents`` fields for a row, looked up by its SESSION id."""
    entry = table.get(session_id) if session_id else None
    if not isinstance(entry, dict):
        return {"parent_session_id": None, "parent_source": None}
    return {"parent_session_id": entry.get("parent"), "parent_source": entry.get("source")}


def children_of(table: dict) -> dict[str, list[str]]:
    """``{parent sid: [child sids]}`` — the tree read top-down (CMX-390: the sidebar archive's
    cascade walks it). Children in session-id order, so a walk is deterministic."""
    out: dict[str, list[str]] = {}
    for child, entry in table.items():
        parent = entry.get("parent") if isinstance(entry, dict) else None
        if isinstance(parent, str) and parent and parent != child:
            out.setdefault(parent, []).append(child)
    return {p: sorted(cs) for p, cs in out.items()}


# --- the rules ----------------------------------------------------------------------

def ancestors(table: dict, sid: str) -> list[str]:
    """``sid``'s parent chain, nearest first. Stops at a repeat, so a corrupt file that
    somehow holds a loop cannot hang a reader."""
    out: list[str] = []
    cur = sid
    while True:
        entry = table.get(cur)
        parent = entry.get("parent") if isinstance(entry, dict) else None
        if not parent or parent in out or parent == sid:
            return out
        out.append(parent)
        cur = parent


def refusal(table: dict, child: str | None, parent: str | None,
            max_depth: int) -> str | None:
    """Why ``parent`` cannot become ``child``'s parent, or None if it can."""
    if not child or not parent:
        return "missing session id"
    if child in table:
        return "already has a parent"
    if parent == child:
        return "a session cannot be its own parent"
    chain = ancestors(table, parent)
    if child in chain:
        return "cycle: the parent is the session's own descendant"
    if len(chain) + 1 > max_depth:
        return f"too deep: the parent is already {len(chain)} level(s) down (max {max_depth})"
    return None


def assign(table: dict, child: str | None, parent: str | None, source: str, ts: float,
           max_depth: int) -> str | None:
    """Record ``parent`` for ``child`` unless :func:`refusal` says no; returns the refusal."""
    why = refusal(table, child, parent, max_depth)
    if why is None and child and parent:
        table[child] = {"parent": parent, "source": source, "ts": ts}
    return why


# --- the fold: event log -> decisions -----------------------------------------------

def _clean(text: str) -> str:
    return _CLIP_RE.sub("", text or "").strip()


def message_body(prompt) -> str | None:
    """The text inside a ``<cross-session-message …>`` envelope, or None if ``prompt`` is
    not one."""
    if not isinstance(prompt, str):
        return None
    m = _ENVELOPE_RE.match(prompt)
    if not m:
        return None
    body = prompt[m.end():]
    body = re.sub(r"</cross-session-message>\s*\Z", "", body)
    return _clean(body)


def _same_text(sent: str, body: str) -> bool:
    n = min(len(sent), len(body), MATCH_CHARS)
    return n > 0 and sent[:n] == body[:n]


def match_sender(sends: list, body: str, ts: float, receiver: str) -> str | None:
    """The ONE session whose SendMessage call carried ``body`` shortly before ``ts``."""
    found = {sid for (sent_ts, sid, text) in sends
             if sid != receiver and ts - SEND_MATCH_SECONDS <= sent_ts <= ts
             and _same_text(text, body)}
    return found.pop() if len(found) == 1 else None


def _prune(state: dict, now: float, cfg: Settings) -> None:
    keep_from = now - cfg.window_seconds
    state["starts"] = {k: v for k, v in state.get("starts", {}).items() if v >= keep_from}
    state["first_seen"] = {k: v for k, v in state.get("first_seen", {}).items()
                           if v >= keep_from}
    state["sends"] = [s for s in state.get("sends", []) if s[0] >= now - SEND_MATCH_SECONDS]


def fold(table: dict, state: dict, events: list[dict], cfg: Settings,
         excluded=lambda cwd: False) -> list[dict]:
    """Apply ``events`` (oldest first) to ``table``/``state`` in place; return each decision
    as ``{child, parent, source, ts, refused}`` (``refused`` None when recorded).

    ``excluded(cwd)`` is True for a receiver chela's dispatcher launched — never inferred.
    """
    decisions: list[dict] = []
    state.setdefault("starts", {})
    state.setdefault("first_seen", {})
    state.setdefault("sends", [])
    for ev in events:
        etype = ev.get("type")
        ts = float(ev.get("ts") or 0.0)
        sid = ev.get("session_id")
        raw = ev.get("payload")
        payload: dict = raw if isinstance(raw, dict) else {}
        if etype == SPAWNED_TYPE:
            child = payload.get("session_id")
            parent = payload.get("parent_session_id")
            why = assign(table, child, parent, SPAWN, ts, cfg.max_depth)
            decisions.append({"child": child, "parent": parent, "source": SPAWN,
                              "ts": ts, "refused": why})
        elif etype == _START and sid:
            if payload.get("source") == "startup":
                state["starts"].setdefault(sid, ts)
        elif etype == _PRE_TOOL and sid:
            if payload.get("tool_name") == "SendMessage":
                tool_input = payload.get("tool_input")
                text = tool_input.get("message") if isinstance(tool_input, dict) else None
                if isinstance(text, str) and _clean(text):
                    state["sends"].append([ts, sid, _clean(text)[:MATCH_CHARS]])
        elif etype == _PROMPT and sid:
            body = message_body(payload.get("prompt"))
            if body is None or sid in table or sid in state["first_seen"]:
                continue
            state["first_seen"][sid] = ts
            start = state["starts"].get(sid)
            if start is None or ts - start > cfg.window_seconds:
                continue
            if excluded(payload.get("cwd")):
                continue
            sender = match_sender(state["sends"], body, ts, sid)
            if sender is None:
                continue
            why = assign(table, sid, sender, FIRST_MESSAGE, ts, cfg.max_depth)
            decisions.append({"child": sid, "parent": sender, "source": FIRST_MESSAGE,
                              "ts": ts, "refused": why})
        _prune(state, ts, cfg)
    return decisions


# --- dispatcher exclusion -------------------------------------------------------------

def dispatched_roots() -> list[str]:
    """Every dispatcher run's worktree. Raises if the runs table cannot be read — the
    caller must not infer anything it cannot rule out as a dispatched run."""
    from chela import dispatcher   # deferred: the dispatcher sits far above this module
    return [os.path.realpath(r["worktree_path"]) for r in dispatcher.list_runs()
            if r.get("worktree_path")]


def excluder(roots: list[str]):
    def excluded(cwd) -> bool:
        if not isinstance(cwd, str) or not cwd:
            return False
        real = os.path.realpath(cwd)
        return any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)
    return excluded


# --- the daemon tick (the one writer) -------------------------------------------------

# The SendMessage text prefixes the sender match needs, held in THIS process only: they are
# agent-to-agent message text, so they never go to disk. A daemon restart forgets at most
# SEND_MATCH_SECONDS of them.
_SENDS: list = []


def tick(cfg: Settings | None = None) -> list[dict]:
    """Fold the event log since the stored cursor into the store. ``chela-daemon`` only.

    The file is rewritten only when something in it changed. A cursor the log could not
    honour (the hook ring rolled past it between ticks) is logged: anything in that hole
    is lost to the live fold, and ``chela parents backfill`` is how to recover it.
    """
    cfg = cfg or settings()
    try:
        roots = dispatched_roots()
    except Exception:
        log.warning("parentage: runs table unreadable — skipping this tick", exc_info=True)
        return []
    data = load()
    before = json.dumps(data, sort_keys=True)
    state = data["state"]
    cursor = state.get("cursor") or {}
    res = event_log.read(cursor.get("seq"), after_boot=cursor.get("boot_id"),
                         types=EVENT_TYPES)
    if res.get("gap"):
        log.warning("parentage: the event log could not resume from %s (%s) — parents in "
                    "the gap are missed; `chela parents backfill` recovers them",
                    cursor, res["gap"])
    state.pop("sends", None)
    state["sends"] = _SENDS
    try:
        decisions = fold(data["parents"], state, res["events"], cfg, excluder(roots))
    finally:
        _SENDS[:] = state.pop("sends", [])
    state["cursor"] = {"seq": res["next_seq"], "boot_id": res["boot_id"]}
    if json.dumps(data, sort_keys=True) != before:
        save(data)
    for d in decisions:
        if d["refused"]:
            log.info("parentage: %s -> %s (%s) refused: %s", d["child"], d["parent"],
                     d["source"], d["refused"])
        else:
            log.info("parentage: %s is a child of %s (%s)", d["child"], d["parent"], d["source"])
    return decisions


# --- spawn: the launch record -------------------------------------------------------

def caller_session(env=None) -> tuple[str | None, str | None]:
    """``(session id, note)`` of the Claude session running ``chela spawn``.

    Unset ``$CHELA_WID`` is a human's own shell: ``(None, None)``, nothing to say. Otherwise
    the session is read from Claude Code's own registry entry for our claude ancestor and
    accepted ONLY when that entry says it runs in ``$CHELA_WID`` — an inherited, stale
    ``$CHELA_WID`` naming some other window is refused rather than guessed past (CMX-192).
    """
    env = os.environ if env is None else env
    wid = (env.get("CHELA_WID") or "").strip()
    if not wid:
        return None, None
    pid = sessions.own_claude_pid()
    if not pid:
        return None, (f"$CHELA_WID={wid} is set but this is not running inside a Claude "
                      "session — no parent recorded")
    entry = sessions.registry_entry(pid)
    if not entry:
        return None, (f"cannot read the Claude session registry for pid {pid} — no parent "
                      "recorded")
    actual = sessions.registry_wid(entry)
    if actual != wid:
        return None, (f"$CHELA_WID={wid} is stale (this session runs in {actual or 'no window'})"
                      " — no parent recorded")
    return entry["sessionId"], None


def record_spawn(child: str, parent: str, wid: str | None = None) -> bool:
    """Append the ``session.spawned`` launch record the daemon folds in. True if logged."""
    rec = event_log.append(SPAWNED_TYPE, f"session {child} spawned by {parent}",
                           {"session_id": child, "parent_session_id": parent, "wid": wid},
                           wid=wid, session_id=child)
    return rec is not None


# --- backfill -----------------------------------------------------------------------

def log_records() -> list[dict]:
    """Every record still on disk — rolled files oldest first, then the live one — in seq
    order, de-duplicated."""
    base = event_log.log_path()
    paths = [base.with_name(f"{base.name}.{i}") for i in range(event_log.KEEP_ROTATIONS, 0, -1)]
    paths.append(base)
    seen: dict[int, dict] = {}
    for p in paths:
        recs, _ = event_log._parse(p)
        for r in recs:
            seen.setdefault(r["seq"], r)
    wanted = set(EVENT_TYPES)
    return [seen[k] for k in sorted(seen) if seen[k].get("type") in wanted]


def backfill(cfg: Settings, records: list[dict], table: dict,
             excluded=lambda cwd: False) -> tuple[dict, list[dict]]:
    """Rule 2 over ``records``, on a COPY of ``table``: ``(new table, decisions)``.
    Spawn records in the log are honoured too — they are the same facts the daemon reads."""
    new = {k: dict(v) for k, v in table.items()}
    decisions = fold(new, {}, records, cfg, excluded)
    return new, decisions


def tree_lines(table: dict) -> list[str]:
    """``table`` as an indented tree, roots first, each child under its parent."""
    children: dict[str, list[str]] = {}
    for child, entry in table.items():
        children.setdefault(entry.get("parent"), []).append(child)
    roots = sorted(p for p in children if p not in table)
    lines: list[str] = []

    def walk(sid: str, depth: int, seen: set) -> None:
        for c in sorted(children.get(sid, [])):
            if c in seen:
                continue
            src = table[c].get("source")
            lines.append(f"{'  ' * (depth + 1)}└ {c}  ({src})")
            walk(c, depth + 1, seen | {c})

    for r in roots:
        lines.append(r)
        walk(r, 0, {r})
    return lines


def apply_backfill(decisions: list[dict], cfg: Settings) -> int:
    """Write ``decisions`` under the daemon's tick lock, re-checking each against the file
    as it is NOW (never change a parent; no cycles). Returns how many were recorded."""
    from chela import ticklock
    with ticklock.between_ticks() as err:
        if err:
            raise RuntimeError(err)
        data = load()
        n = 0
        for d in decisions:
            if d["refused"]:
                continue
            if assign(data["parents"], d["child"], d["parent"], d["source"], d["ts"],
                      cfg.max_depth) is None:
                n += 1
        save(data)
        return n
