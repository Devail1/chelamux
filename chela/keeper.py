"""Keeper digest — what happened in one workflow's repo since the last look (CMX-346).

Keeper mode (CMX-345) is one periodic agent that keeps a workflow's board aligned with the
work actually being done. This is its first slice and it is deliberately dumb: **no LLM,
no tracker writes**. ``chela keeper digest <WORKFLOW.md>`` reads only what chela already
has — the live pane map, the event log's hook records, the repo's git history, ``gh`` and
the workflow's tracker — and prints a bounded markdown digest (or ``--json`` for slice 2,
the LLM proposal run).

**A session is a live window whose claude process was launched inside the repo** (the
workflow file's own checkout, or a worktree under its ``workspace.root``). Its session id
is the LIVE one (:func:`chela.sessions.live_session_of_window`, CMX-337) — never the
``--resume <sid>`` on its command line, which a ``/clear`` or ``/resume`` leaves behind.

**Every section is bounded and says what it dropped** (``total`` next to the items), so a
busy hour stays near 3k tokens without anyone mistaking a cut list for the whole. A read
that fails (git, gh, the tracker) is reported as unreadable — ``total: None`` and an
``error`` — never as an empty section: "no commits" and "could not ask git" are
different facts.

**The cursor** (``$CHELA_DIR/keeper/<key>.json``) holds the event-log position and the
wall-clock time this digest ended at, so the next run starts there. ``--since`` overrides
it for that run and leaves the stored cursor where it was: a look must not swallow the
window the next periodic run is owed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from chela import agent_manager, config, discovery, event_log, sessions, transcripts
from chela.workflow import WorkflowDef, resolve_workspace_root

PROMPT_EVENT = "hook.user_prompt_submit"
TOOL_EVENT = "hook.post_tool_use"
EDIT_TOOLS = ("Edit", "Write")
# A UserPromptSubmit Claude Code itself sends when a background task finishes: not a
# prompt anyone typed.
HARNESS_PROMPT = "<task-notification>"
DEFAULT_SINCE_SECONDS = 3600
PROMPT_CHARS = 200
RECAP_CHARS = 300
# Per-section caps. Sized so a busy hour (8 sessions, a page of commits, a full board)
# renders near 3k tokens.
CAPS = {"sessions": 8, "prompts": 3, "edits": 8, "commits": 15, "prs": 10,
        "issues": 30, "board": 30}
GIT_TIMEOUT = 15
GH_TIMEOUT = 30
PR_LIMIT = 100

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str) -> int:
    """``"90m"`` / ``"2h"`` / ``"1d"`` / ``"600"`` → seconds. Raises ValueError otherwise."""
    m = _DURATION_RE.match(str(text))
    if not m or int(m.group(1)) <= 0:
        raise ValueError(f"not a duration: {text!r} (try 600, 90m, 2h, 1d)")
    return int(m.group(1)) * _UNIT[m.group(2).lower()]


def tracker_key(wf: WorkflowDef) -> str:
    """The prefix the tracker's issue ids carry: a Linear team key, else the project key."""
    if wf.get("tracker", "kind") == "linear":
        team = str(wf.get("tracker", "team", default="") or "").strip()
        if team:
            return team.upper()
    return wf.project_key.upper()


def issue_ids(texts, key: str) -> list[str]:
    """Every ``KEY-<n>`` in ``texts``, upper-cased, unique, in numeric order.

    Case-insensitive because branch names carry the id lower-cased (``cmx-12-...``); the
    boundaries keep ``XCMX-1`` and ``CMX-12a`` out.
    """
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(key)}-(\d+)(?![A-Za-z0-9])",
                         re.IGNORECASE)
    nums = {int(m.group(1)) for text in texts if text for m in pattern.finditer(str(text))}
    return [f"{key.upper()}-{n}" for n in sorted(nums)]


def cursor_path(wf: WorkflowDef) -> Path:
    key = re.sub(r"[^a-z0-9_-]+", "-", wf.project_key.lower())
    return Path(config.CHELA_DIR) / "keeper" / f"{key}.json"


def load_cursor(wf: WorkflowDef) -> dict | None:
    """The stored cursor, or None when there is none or it cannot be trusted."""
    try:
        data = json.loads(cursor_path(wf).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or not isinstance(data.get("seq"), int)
            or not isinstance(data.get("ts"), (int, float))):
        return None
    return data


def save_cursor(wf: WorkflowDef, cursor: dict) -> str | None:
    """Write the cursor atomically. Returns the error text when it could not be written."""
    path = cursor_path(wf)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")    # two runs never share one
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(cursor), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        return str(exc)
    return None


def _git_toplevel(path: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def repo_roots(wf: WorkflowDef) -> tuple[str, list[str]]:
    """``(repo, roots)``: the checkout the workflow file lives in, and every directory a
    session of this workflow can be launched under (the checkout + ``workspace.root``)."""
    here = Path(wf.path).expanduser().resolve().parent
    repo = _git_toplevel(here) or str(here)
    return repo, [os.path.realpath(repo), os.path.realpath(resolve_workspace_root(wf))]


def _under(path: str | None, roots: list[str]) -> bool:
    if not path:
        return False
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)


def _cut(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _section(items: list, cap: int) -> dict:
    """The newest ``cap`` of ``items`` (already oldest-first), plus how many there were."""
    return {"items": items[-cap:] if cap else [], "total": len(items)}


def _unreadable(why: str) -> dict:
    return {"items": [], "total": None, "error": why}


def _trim(section: dict, cap: int) -> dict:
    return section if section["total"] is None else {**section, **_section(section["items"], cap)}


def live_windows(roots: list[str]) -> list[dict]:
    """Every live claude window launched under ``roots``: identity, status and recap."""
    pane_map = sessions.panes(force=True)
    names = discovery.get_windows_by_id()
    status_map = agent_manager.session_status_map()
    out = []
    for wid, pane in pane_map.items():
        if not pane.claude_pid or not _under(pane.launched_in or pane.path, roots):
            continue
        entry = agent_manager.session_entry(agent_manager.claude_pid(wid), status_map)
        summary = transcripts.summary_for_path(sessions.transcript_for_window(wid))
        out.append({
            "wid": wid,
            "name": names.get(wid, ""),
            # CMX-337: the live id only. The command line's `--resume` id is where the
            # window STARTED; after a /clear it names a session nobody is running.
            "session_id": sessions.live_session_of_window(wid, pane_map),
            "status": (entry or {}).get("status") or "unknown",
            "recap": _cut(summary.get("recap") or "", RECAP_CHARS) or None,
            "recap_ts": summary.get("recap_ts"),
            "started": pane.started,
        })
    return sorted(out, key=lambda w: _wid_order(w["wid"]))


def _wid_order(wid: str) -> tuple[int, str]:
    num = wid.lstrip("@")
    return (int(num), wid) if num.isdigit() else (1 << 30, wid)


def read_events(cursor: dict | None, since_ts: float | None) -> dict:
    """The hook records to digest: after the cursor, or (``since_ts`` set) since then."""
    types = [PROMPT_EVENT, TOOL_EVENT]
    if since_ts is not None or cursor is None:
        batch = event_log.read(types=types)
        oldest = next((e.get("ts") for e in event_log.ring()), None)
        batch["gap"] = None
        if (batch["first_seq"] > 1 and isinstance(oldest, (int, float))
                and oldest > (since_ts or 0)):
            # The ring no longer reaches back to the window's start: say so, never imply
            # nobody typed anything before its first record.
            batch["gap"] = {"reason": "the event log no longer reaches back to the window "
                                      f"start (oldest record {_iso(oldest)})"}
        batch["events"] = [e for e in batch["events"]
                           if (e.get("ts") or 0) >= (since_ts or 0)]
        return batch
    return event_log.read(cursor["seq"], after_boot=cursor.get("boot_id"), types=types)


def _activity(window: dict, events: list[dict],
              roots: list[str]) -> tuple[list[dict], list[str], list[str]]:
    """``(prompts, edited paths, full prompt texts)`` for one window, oldest-first. A path
    under one of ``roots`` is shown relative to it."""
    prompts, paths, texts = [], [], []
    sid, started = window["session_id"], window.get("started")
    for e in events:
        # A wid is only this window's since its claude started: an older record under the
        # same @N belongs to whatever ran there before (tmux reuses window ids).
        by_wid = e.get("wid") == window["wid"] and (
            started is None or (e.get("ts") or 0) >= started)
        if not by_wid and not (sid and e.get("session_id") == sid):
            continue
        payload = e.get("payload") or {}
        if e.get("type") == PROMPT_EVENT:
            text = str(payload.get("prompt") or "")
            if text.lstrip().startswith(HARNESS_PROMPT):
                continue                 # the harness reporting a background task
            texts.append(text)
            prompts.append({"ts": e.get("ts"),
                            "text": _cut(text, PROMPT_CHARS)})
        elif e.get("type") == TOOL_EVENT and payload.get("tool_name") in EDIT_TOOLS:
            tool_input = payload.get("tool_input")
            path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
            if path:
                path = _relative(str(path), roots)
                if path in paths:
                    paths.remove(path)       # keep each path once, at its latest edit
                paths.append(path)
    return prompts, paths, texts


def _relative(path: str, roots: list[str]) -> str:
    for root in roots:
        prefix = root.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commits(repo: str, since_ts: float) -> dict:
    """Commits on any branch since ``since_ts``, oldest first (uncapped)."""
    try:
        r = subprocess.run(
            ["git", "-C", repo, "log", "--all", f"--since={_iso(since_ts)}",
             "--format=%H%x1f%ct%x1f%s"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unreadable(f"git log failed: {exc}")
    if r.returncode != 0:
        return _unreadable(f"git log exited {r.returncode}: {r.stderr.strip()[:200]}")
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3 and parts[1].isdigit() and int(parts[1]) >= since_ts:
            rows.append({"sha": parts[0][:10], "ts": int(parts[1]), "subject": parts[2]})
    rows.sort(key=lambda c: c["ts"])
    return {"items": rows, "total": len(rows)}


def _epoch(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pull_requests(repo: str, since_ts: float) -> dict:
    """PRs opened or merged since ``since_ts``, oldest first (uncapped)."""
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", str(PR_LIMIT),
             "--search", f"updated:>={_iso(since_ts)}",
             "--json", "number,title,state,createdAt,mergedAt"],
            cwd=repo, capture_output=True, text=True, timeout=GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unreadable(f"gh pr list failed: {exc}")
    if r.returncode != 0:
        return _unreadable(f"gh pr list exited {r.returncode}: {r.stderr.strip()[:200]}")
    try:
        prs = json.loads(r.stdout)
    except ValueError:
        return _unreadable("gh pr list returned unparseable JSON")
    if not isinstance(prs, list):
        return _unreadable("gh pr list returned no list")
    rows = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        merged, opened = _epoch(pr.get("mergedAt")), _epoch(pr.get("createdAt"))
        if merged is not None and merged >= since_ts:
            event, ts = "merged", merged
        elif opened is not None and opened >= since_ts:
            event, ts = "opened", opened
        else:
            continue
        rows.append({"number": pr.get("number"), "title": str(pr.get("title") or ""),
                     "state": str(pr.get("state") or ""), "event": event, "ts": ts})
    rows.sort(key=lambda p: p["ts"])
    # gh returns the newest PR_LIMIT and stops: a full page may have dropped older ones.
    return {"items": rows, "total": len(rows), "limit_hit": len(prs) >= PR_LIMIT}


def board(wf: WorkflowDef) -> dict:
    """Every open issue in the workflow's tracker. A failed read is unreadable, not empty."""
    from chela.sources import get_source
    try:
        source = get_source(wf)
        tasks = source.list_open_tasks()
    except Exception as exc:                   # noqa: BLE001 — a digest never dies on a tracker
        return _unreadable(f"tracker read failed: {exc}")
    if getattr(source, "read_failed", False):
        return _unreadable("tracker read failed")
    rows = [{"id": t.id, "title": t.title, "state": t.tracker_state or t.state}
            for t in tasks]
    return {"items": rows, "total": len(rows)}


def _quiet(window: dict) -> bool:
    return not (window["prompts"]["total"] or window["edits"]["total"])


def build(wf: WorkflowDef, *, since: int | None = None, now: float | None = None) -> dict:
    """The digest as data, plus the cursor the run ends at (``digest["cursor"]``).

    The window starts at: ``now - since`` when ``since`` is given, else the stored
    cursor, else ``now - DEFAULT_SINCE_SECONDS``.

    The cursor is None after a ``--since`` run: that is a look, and moving the cursor to
    ``now`` would skip whatever lay between the old cursor and the look's own start. When
    git or ``gh`` could not be read its ``ts`` stays at this window's start, so the next
    run asks them again for the same span.
    """
    now = time.time() if now is None else now
    cursor = load_cursor(wf)
    if since is not None:
        since_ts, source = now - since, "--since"
    elif cursor is not None:
        since_ts, source = float(cursor["ts"]), "cursor"
    else:
        since_ts, source = now - DEFAULT_SINCE_SECONDS, "default"
    batch = read_events(cursor if source == "cursor" else None,
                        None if source == "cursor" else since_ts)
    repo, roots = repo_roots(wf)

    windows, texts = [], []
    for window in live_windows(roots):
        prompts, paths, said = _activity(window, batch["events"], roots)
        texts += [window["name"], window["recap"], *said]
        windows.append({**window, "prompts": _section(prompts, CAPS["prompts"]),
                        "edits": _section(paths, CAPS["edits"])})
    git = commits(repo, since_ts)
    prs = pull_requests(repo, since_ts)

    # Ids come from everything in the window, not only the rows that survive a cap.
    texts += [c["subject"] for c in git["items"]] + [p["title"] for p in prs["items"]]
    key = tracker_key(wf)
    mentioned = issue_ids(texts, key)
    tracker = board(wf)
    if tracker["total"]:
        # The issues this digest names first, so the cap never hides their state.
        named = set(mentioned)
        ordered = sorted(tracker["items"], key=lambda t: t["id"] not in named)
        tracker = {"items": ordered[:CAPS["board"]], "total": tracker["total"]}
    read_ok = git["total"] is not None and prs["total"] is not None
    return {
        "workflow": str(wf.path),
        "tracker_key": key,
        "since": since_ts,
        "since_source": source,
        "until": now,
        "event_gap": (batch.get("gap") or {}).get("reason"),
        # The windows that did something come first (stable: by window id within each),
        # so the cap drops quiet ones before active ones.
        "sessions": {"items": sorted(windows, key=_quiet)[:CAPS["sessions"]],
                     "total": len(windows)},
        "commits": _trim(git, CAPS["commits"]),
        "prs": _trim(prs, CAPS["prs"]),
        "issues": _section(mentioned, CAPS["issues"]),
        "board": tracker,
        "cursor": None if source == "--since" else {
            "seq": batch["next_seq"], "boot_id": batch["boot_id"],
            "ts": now if read_ok else since_ts},
    }


def _hm(ts) -> str:
    if isinstance(ts, str):
        ts = _epoch(ts)
    if not isinstance(ts, (int, float)):
        return "?"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")


def _count(section: dict, noun: str) -> str:
    shown, total = len(section["items"]), section["total"]
    if total is None:
        return f"{noun}: unreadable ({section.get('error')})"
    text = f"{noun} ({total})" if shown == total else f"{noun} ({shown} of {total})"
    if section.get("limit_hit"):
        text += f" — gh's {PR_LIMIT}-row limit was hit; older ones may be missing"
    return text


def render(d: dict) -> str:
    """The digest as compact markdown."""
    span = datetime.fromtimestamp(d["since"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    out = [f"# Keeper digest — {d['tracker_key']} (since {span} UTC, from {d['since_source']})"]
    if d.get("event_gap"):
        out.append(f"⚠ event log gap: {d['event_gap']}")
    out += ["", f"## {_count(d['sessions'], 'Sessions')}"]
    for w in d["sessions"]["items"]:
        out.append(f"### {w['wid']} {w['name']} · {w['status']} · session "
                   f"{w['session_id'] or 'unknown'}")
        if w["recap"]:
            out.append(f"- recap ({_hm(w['recap_ts'])}): {w['recap']}")
        if w["prompts"]["total"]:
            out.append(f"- {_count(w['prompts'], 'prompts')}:")
            out += [f"  - {_hm(p['ts'])} {p['text']}" for p in w["prompts"]["items"]]
        if w["edits"]["total"]:
            out.append(f"- {_count(w['edits'], 'edited')}: {', '.join(w['edits']['items'])}")
    out += ["", f"## {_count(d['commits'], 'Commits')}"]
    out += [f"- {c['sha']} {c['subject']}" for c in d["commits"]["items"]]
    out += ["", f"## {_count(d['prs'], 'PRs')}"]
    out += [f"- #{p['number']} {p['event']} ({p['state']}): {p['title']}"
            for p in d["prs"]["items"]]
    out += ["", f"## {_count(d['issues'], 'Issues mentioned')}"]
    if d["issues"]["items"]:
        out.append(", ".join(d["issues"]["items"]))
    out += ["", f"## {_count(d['board'], 'Board')}"]
    out += [f"- {t['id']} [{t['state']}] {t['title']}" for t in d["board"]["items"]]
    return "\n".join(out) + "\n"


def run(wf: WorkflowDef, *, since: int | None = None, as_json: bool = False) -> str:
    """Build, advance the cursor (not after a ``--since`` look), and return the text."""
    digest = build(wf, since=since)
    error = save_cursor(wf, digest["cursor"]) if digest["cursor"] else None
    if error:
        digest["cursor_error"] = error
    if as_json:
        return json.dumps(digest, indent=2, default=str)
    text = render(digest)
    if error:
        text += f"\n⚠ cursor not saved ({error}): the next digest repeats this window\n"
    return text
