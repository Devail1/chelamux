"""Notification digest — interrupt only for needs-you events, batch the rest into a brief.

Every inbox event used to be pushed into the orchestrator the tick it happened: a judge
verdict, a CI flake that re-ran green, a run that sat in review. Most of those need nobody;
the few that need a human (a run escalated to ``needs_human``, an override waiting for an
approval, a failed merge) were buried among them. This splits the two.

**Interrupt rules** (``~/.chela/config.json``, key ``digest``)::

    "digest": {
      "enabled": true,
      "interrupt": ["run_needs_human", "*override_requested", "*_failed"],
      "times": ["09:00", "16:00"],
      "tz": "Asia/Jerusalem"
    }

``interrupt`` is a list of ``fnmatch`` patterns over the event kind. A kind that matches goes
out the moment it happens, exactly as before. Any other kind is held for the digest: it is
still appended to the event log (``chela events``, the dashboard Feed), it is just not typed
into the orchestrator's session. Leave ``interrupt`` out for :data:`DEFAULT_INTERRUPT`.

**Off unless ``enabled`` is true.** With the feature off, or a config that cannot be read,
every kind interrupts, which is the behaviour before this module. Holding an event back is
the one thing that needs an explicit yes; an unreadable rule never withholds anything.

**The digest** goes out at each of ``times`` (``HH:MM`` in ``tz``, the host's local time
when ``tz`` is unset) and on demand (``chela digest``). It has four parts: needs you (at most
:data:`NEEDS_YOU_MAX`), waiting on others, done since last, changed. It is capped at
:data:`WORD_CAP` words and every line carries a link.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chela import config, event_log

log = logging.getLogger(__name__)

CONFIG_KEY = "digest"
DIGEST_KIND = "digest"

# Measured on the core event log, 2026-10-24..27, after the wrong-notice fixes (CMX-320's PR
# body has the table). Everything here is a decision only a human (or the orchestrator acting
# for one) can make, or a failure nobody else will retry.
DEFAULT_INTERRUPT = (
    "run_needs_human",            # the dispatcher gave up: rework cap, infra, a human gate
    "*override_requested",        # a merge override waiting for an approval
    "*release_requested",         # a release waiting for an approval
    "override_*",                 # an override's outcome: approved ones carry the deploy step
    "release_*",                  # same, for a release
    "*_failed",                   # a failed run, merge, release or deploy
    "run_ci_known_flake",         # CI still red after its 2nd re-run, only on known flakes
    "*oom_kill*",                 # a low-memory kill (not `*oom*`: that is every room_* too)
    "*_oom",
    "run_permission_denied",      # an agent is blocked on a permission it was refused
    "keeper_proposals",           # board changes waiting for an approval
    "orchestrator.moved",         # the inbox now delivers to a different window
    "orchestrator.escalation.*",  # chela itself asked for a human
    "run_merged_outside_gate",    # the merge gate's backstop fired
    "run_unjudged_merged",
)
DEFAULT_TIMES = ("09:00", "16:00")

INTERRUPT, HOLD = "interrupt", "digest"

NEEDS_YOU_MAX = 3
WORD_CAP = 150

# Waiting on others: the latest of these per task decides. A run in review, waiting on CI or
# a judge, or parked is waiting; a run that moved on (failed, back to its agent) is not. A
# merged or closed run is never waiting, whatever is logged after it: the inbox logs a judge
# verdict on the tick AFTER an auto-merge, so a "judge clean" can follow the merge it caused.
WAITING_TYPES = {
    "run_review": "in review",
    "run_judge_not_started": "waiting on CI or judge",
    "run_judge_clean": "judge clean, awaiting merge",
    "run_selfcheck_long": "in self-check",
    "run_ci_flake": "CI flake, re-run green",
    "run_parked": "parked",
}
CLEARS_WAITING = ("run_failed", "run_needs_human", "run_changes_requested", "run_unparked")
ENDS_RUN = ("orchestrator.merge", "run_closed", "run_was_closed")
REOPENS_RUN = "run_reopened"
DONE_TYPES = {
    "orchestrator.merge": "merged",
    "orchestrator.release_pushed": "released",
}
# Needs you: what to call an interrupt kind in one line, first matching pattern wins.
NEEDS_NAMES = (
    ("run_needs_human", "needs a human"),
    ("*override_requested", "override awaits approval"),
    ("*release_requested", "release awaits approval"),
    ("keeper_proposals", "board changes await approval"),
    ("run_ci_known_flake", "CI red on known flakes after re-runs"),
    ("run_permission_denied", "agent refused a permission"),
    ("orchestrator.moved", "inbox now delivers to a new window"),
    ("*_failed", "failed"),
)
# Changed: new and reopened issues, plus every other kind the default rules hold back, so a
# held notice is never only in `chela events`.
CHANGED_TYPES = {
    "task.created": "new issue",
    "run_reopened": "reopened",
    "run_ci_red": "CI red, sent back",
    "run_pr_conflicting": "PR conflicts with its base",
    "run_stuck": "agent looks stuck",
    "run_marker_unread": "agent marker unread",
    "run_park_overdue": "park overdue",
    "run_base_merge_infra": "base merge could not run tests",
    "split_filed": "split",
    "ops_findings": "ops scan findings",
    "nightly_sweep": "nightly sweep",
}
# What resolves a needs-you event on the same subject: the run moved forward, ended, or the
# request it was waiting on was carried out. Any other later event leaves it standing.
RESOLVES = (*WAITING_TYPES, *ENDS_RUN, "run_reopened", "run_unparked", "run_changes_requested",
            "orchestrator.release_pushed", "orchestrator.merge_override")


# --- rules -----------------------------------------------------------------------------

def _raw_config() -> dict:
    try:
        from chela import userconfig
        raw = userconfig.get(CONFIG_KEY)
    except Exception:  # noqa: BLE001 — an unreadable config never withholds an event
        log.warning("digest: config.json unreadable; every event interrupts", exc_info=True)
        return {}
    return raw if isinstance(raw, dict) else {}


def rules(raw: dict | None = None) -> dict:
    """The effective rules: ``{enabled, interrupt, times, tz}``.

    ``raw`` is the ``digest`` object from ``config.json`` (read when None). A field that is
    missing or the wrong type takes its default; ``enabled`` is true only for a real ``true``.
    """
    raw = _raw_config() if raw is None else raw
    interrupt = raw.get("interrupt")
    if not (isinstance(interrupt, list) and all(isinstance(p, str) for p in interrupt)):
        interrupt = list(DEFAULT_INTERRUPT)
    times = raw.get("times")
    if not (isinstance(times, list) and times and all(_parse_hhmm(t) for t in times)):
        if times is not None:
            log.warning("digest: times %r is not a non-empty list of HH:MM; using %s",
                        times, list(DEFAULT_TIMES))
        times = list(DEFAULT_TIMES)
    tz = raw.get("tz") if isinstance(raw.get("tz"), str) and raw.get("tz") else None
    return {"enabled": raw.get("enabled") is True, "interrupt": interrupt,
            "times": times, "tz": tz}


def route(kind: str | None, rule: dict | None = None) -> str:
    """:data:`INTERRUPT` (push now) or :data:`HOLD` (wait for the digest) for one event kind.

    The digest itself always interrupts, and so does every kind while the feature is off.
    """
    rule = rules() if rule is None else rule
    kind = kind or ""
    if not rule["enabled"] or kind == DIGEST_KIND:
        return INTERRUPT
    if any(fnmatch.fnmatchcase(kind, p) for p in rule["interrupt"]):
        return INTERRUPT
    return HOLD


# --- the digest ------------------------------------------------------------------------

def _link(event: dict) -> str:
    p = event.get("payload") or {}
    for key in ("pr_url", "url", "issue_url"):
        if isinstance(p.get(key), str) and p[key].startswith(("http://", "https://")):
            return p[key]
    return config.dashboard_url("/")


def _label(event: dict) -> str:
    p = event.get("payload") or {}
    return str(p.get("task_id") or p.get("identifier") or p.get("tag") or event.get("type")
               or "?")


def _subject(event: dict) -> str | None:
    """What an event is about, so a later event can resolve it: a task, a release tag."""
    p = event.get("payload") or {}
    value = p.get("task_id") or p.get("tag") or p.get("request_id")
    return str(value) if value else None


def _what(event: dict, names: dict[str, str]) -> str:
    kind = event.get("type") or ""
    known = names.get(kind) or next(
        (text for pattern, text in NEEDS_NAMES if fnmatch.fnmatchcase(kind, pattern)), None)
    if known:
        return known
    label = _label(event)
    words = [w for w in (event.get("summary") or kind).split() if label not in w]
    return " ".join(words[:6])


def _line(event: dict, names: dict[str, str]) -> str:
    return f"• {_label(event)} {_what(event, names)} {_link(event)}"


def build(events: list[dict], since: float, rule: dict | None = None) -> dict:
    """The four parts, from event-log records. Pure: no I/O but the rules' config read.

    ``needs_you`` / ``done`` / ``changed`` take events after ``since``; ``waiting`` is the
    CURRENT state, so it reads every event given (a run that went into review before the
    last digest and is still there is still waiting). Newest first in each part.
    """
    rule = rules() if rule is None else rule
    ordered = sorted(events, key=lambda e: (e.get("ts") or 0, e.get("seq") or 0))
    recent = [e for e in ordered if (e.get("ts") or 0) > since]

    loud = {**rule, "enabled": True}

    def is_loud(e: dict) -> bool:
        return e.get("type") != DIGEST_KIND and route(e.get("type"), loud) == INTERRUPT

    # Still needs you: nothing has resolved the same subject since (a release that was
    # pushed, an override that merged, a run back in review after needs_human).
    moved_on: dict[str, float] = {}
    for e in ordered:
        key = _subject(e)
        if key and e.get("type") in RESOLVES:
            moved_on[key] = e.get("ts") or 0
    needs = [e for e in recent if is_loud(e)
             and not moved_on.get(_subject(e) or "", 0) > (e.get("ts") or 0)]

    latest: dict[str, dict] = {}
    ended: set[str] = set()
    for e in ordered:
        kind, task = e.get("type"), (e.get("payload") or {}).get("task_id")
        if not task:
            continue
        if kind == REOPENS_RUN:
            ended.discard(task)
            latest.pop(task, None)
        elif kind in ENDS_RUN:
            ended.add(task)
        elif kind in WAITING_TYPES or kind in CLEARS_WAITING:
            latest[task] = e
    waiting = [e for task, e in latest.items()
               if task not in ended and e.get("type") in WAITING_TYPES]

    def per_task(types: dict) -> list[dict]:
        seen: dict[str, dict] = {}
        for e in recent:
            if e.get("type") in types:
                seen[_label(e)] = e
        return list(seen.values())

    newest = lambda xs: sorted(xs, key=lambda e: e.get("ts") or 0, reverse=True)  # noqa: E731
    return {"needs_you": newest(needs), "waiting": newest(waiting),
            "done": newest(per_task(DONE_TYPES)), "changed": newest(per_task(CHANGED_TYPES))}


_PARTS = (("needs_you", "Needs you", {}), ("waiting", "Waiting on others", WAITING_TYPES),
          ("done", "Done since last", DONE_TYPES), ("changed", "Changed", CHANGED_TYPES))
# The parts that give up lines first when the digest is over the word cap (needs you is last).
_TRIM_FIRST = ("changed", "done", "waiting")


def _words(lines: list[str]) -> int:
    return sum(len(line.split()) for line in lines)


def _render_lines(parts: dict, shown: dict[str, int], since_label: str) -> list[str]:
    lines = [f"chela digest since {since_label}"]
    for key, title, names in _PARTS:
        items = parts[key]
        lines.append(f"{title} {len(items)}")
        for e in items[:shown[key]]:
            lines.append(_line(e, names))
        if len(items) > shown[key]:
            lines.append(f"• +{len(items) - shown[key]} more {config.dashboard_url('/')}")
    return lines


def render_lines(parts: dict, since_label: str = "last digest") -> list[str]:
    """The digest as lines, at most :data:`WORD_CAP` words, a link on every item line.

    Needs you shows at most :data:`NEEDS_YOU_MAX` and is never trimmed further. Over the cap,
    the longest of the other three parts gives up its oldest line (on a tie: changed, then
    done, then waiting). Every dropped line is counted in its part's ``+N more`` line, which
    links to the dashboard. With every other part trimmed, what is left (the headers, at most
    three needs-you lines and the ``+N more`` lines) is about 40 words.
    """
    shown = {key: len(parts[key]) for key, _, _ in _PARTS}
    shown["needs_you"] = min(shown["needs_you"], NEEDS_YOU_MAX)
    lines = _render_lines(parts, shown, since_label)
    while _words(lines) > WORD_CAP:
        others = [k for k in _TRIM_FIRST if shown[k] > 0]
        if not others:
            break      # left: headers, at most 3 needs-you lines and the +N more lines
        key = max(others, key=lambda k: (shown[k], -_TRIM_FIRST.index(k)))
        shown[key] -= 1
        lines = _render_lines(parts, shown, since_label)
    return lines


def render(parts: dict, since_label: str = "last digest") -> str:
    return "\n".join(render_lines(parts, since_label))


# --- the schedule ----------------------------------------------------------------------

def _parse_hhmm(raw) -> tuple[int, int] | None:
    if not isinstance(raw, str):
        return None
    hh, sep, mm = raw.strip().partition(":")
    if not (sep and hh.isdigit() and mm.isdigit() and len(mm) == 2):
        return None
    h, m = int(hh), int(mm)
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


def _zone(tz: str | None):
    if not tz:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("digest: unknown tz %r; using the host's local time", tz)
        return None


def last_slot(now: float, rule: dict) -> float | None:
    """The most recent configured digest time at or before ``now`` (epoch seconds), looking
    back at most one day. None when no time is configured."""
    zone = _zone(rule.get("tz"))
    here = datetime.fromtimestamp(now, zone).astimezone(zone)
    best = None
    for raw in rule.get("times") or ():
        at = _parse_hhmm(raw)
        if not at:
            continue
        for day in (here, here - timedelta(days=1)):
            slot = day.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
            if slot.timestamp() <= now:
                best = max(best or 0.0, slot.timestamp())
                break
    return best


def state_path() -> Path:
    return Path(config.CHELA_DIR) / "digest.json"   # per call: a test redirects CHELA_DIR


def last_sent() -> float | None:
    """When the last digest went out; None when none has (or the record is unreadable)."""
    try:
        value = json.loads(state_path().read_text()).get("last_sent")
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def mark_sent(ts: float) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"last_sent": ts}))
    tmp.replace(path)


def due(now: float, rule: dict, sent: float | None) -> bool:
    """Is a scheduled digest owed at ``now``? A slot has passed since the last one went out.

    With no record of a last digest nothing is owed: enabling the feature at 15:00 starts
    the clock rather than sending the 09:00 digest at once.
    """
    slot = last_slot(now, rule)
    return bool(rule["enabled"] and slot is not None and sent is not None and slot > sent)


def load_events() -> list[dict]:
    """Every record in the core log (chela's own events, weeks of them; no ``hook.*``)."""
    event_log.seed_core()
    records, _ = event_log._core_records()
    return records


def compose(now: float | None = None, since: float | None = None,
            rule: dict | None = None, events: list[dict] | None = None) -> tuple[str, dict]:
    """``(text, payload)`` for a digest covering ``since`` (default: the last digest sent,
    else the last 24 hours) up to ``now``."""
    now = time.time() if now is None else now
    rule = rules() if rule is None else rule
    if since is None:
        since = last_sent() or now - 86400
    events = load_events() if events is None else events
    parts = build(events, since, rule)
    zone = _zone(rule.get("tz"))
    label = datetime.fromtimestamp(since, zone).astimezone(zone).strftime("%a %H:%M")
    payload = {"since": since, "counts": {k: len(v) for k, v in parts.items()}}
    return render(parts, label), payload


def send(now: float | None = None, rule: dict | None = None) -> dict:
    """Deliver one digest: the orchestrator's inbox, the phone, the event log. Then mark it
    sent, so the next one covers from here."""
    from chela import inbox, notify
    now = time.time() if now is None else now
    text, payload = compose(now=now, rule=rule)
    # Marked BEFORE delivering: a send that raises must not leave the slot owed, or every
    # daemon tick would queue the same digest again.
    mark_sent(now)
    queued = inbox.enqueue(DIGEST_KIND, " | ".join(text.splitlines()), payload)
    pushed = notify.send(text, title="chela digest") if notify.enabled() else False
    event_log.append(DIGEST_KIND, text.splitlines()[0], {**payload, "text": text})
    return {"text": text, "queued": queued, "pushed": pushed, **payload}


def daemon_tick(now: float | None = None) -> dict | None:
    """Send the scheduled digest when one is owed. Called every daemon tick; cheap when not."""
    now = time.time() if now is None else now
    rule = rules()
    if not rule["enabled"]:
        return None
    sent = last_sent()
    if sent is None:
        mark_sent(now)                     # start the clock; see `due`
        return None
    if not due(now, rule, sent):
        return None
    return send(now=now, rule=rule)
