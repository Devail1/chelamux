"""Agent-facing orchestrator toolkit — the OBSERVE half (Slice 1).

A sibling agent (the orchestrator) pulls a filtered status view (`peek`) or a
distilled transcript (`read`) of another window, keyed by its tmux **window id**
(``@N``) — stable and collision-free, unlike window *names*. Everything reuses
the same data layer the dashboard `/api/agents` reads (``agent_manager`` +
``transcripts``), so a CLI peek and a dashboard row agree, and none of it needs
the dashboard HTTP server running.

Transcript resolution goes through ``sessions.transcript_for_window`` (session id
first, cwd only as a last resort — and REFUSED even then if another live window
shares the same cwd), never a bare cwd guess: two windows launched in the same
directory otherwise race for "newest transcript" and one window's `read`/`peek`
silently serves the other's session (CMX-190; same root cause as CMX-153).

Authority for "what is this agent doing" is the native ``session_status``
(busy/idle/waiting) and the JSONL transcript — never a pane scrape (a raw
``capture-pane`` shows Claude Code's grey *ghost-text* suggestion as if it were
typed input; it is not, and its presence actually means the prompt is idle).

DRIVE (messaging a sibling) reuses the existing `messenger`/`dispatch` path; see
`chela drive`. UI is Slice 2.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from chela import agent_manager, discovery, sessions, transcripts

# Per-turn character cap for the tail/query digests — enough to read intent
# without dumping a whole essay per turn. `--all` is uncapped (full read).
_DIGEST_TURN_CHARS = 600


def self_wid() -> str | None:
    """This process's own window id: ``$CHELA_WID`` (injected at spawn), falling
    back to deriving it from ``$TMUX_PANE`` (tmux sets it in every pane) so it
    works even for agents chela didn't spawn."""
    wid = os.environ.get("CHELA_WID")
    if wid:
        return wid
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#{window_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return None


def resolve_window(wid: str) -> dict | None:
    """``{wid, name, cwd}`` for a live window id, or None if it isn't live."""
    name = discovery.get_windows_by_id().get(wid)
    if name is None:
        return None
    return {"wid": wid, "name": name, "cwd": discovery.get_window_cwd_by_id(wid)}


def peek(wid: str) -> dict | None:
    """Filtered status view for one window id — the default, low-cost tier.

    Returns None if the window isn't live. Otherwise: identity + native
    session_status + liveness/health + window_type + recap + context usage,
    all off the shared data layer.
    """
    win = resolve_window(wid)
    if win is None:
        return None

    status_map = agent_manager.session_status_map()
    cpid = agent_manager.claude_pid(wid)
    claude_running = cpid is not None
    sess_status = status_map["by_pid"].get(cpid) if cpid is not None else None
    live, health = agent_manager.liveness(claude_running, sess_status)
    win_type = agent_manager.window_type(wid, claude_running)

    path = sessions.transcript_for_window(wid)
    summary = transcripts.summary_for_path(path)
    ctx = transcripts.latest_context_usage(path) if path is not None else None

    return {
        "wid": wid,
        "name": win["name"],
        "cwd": win["cwd"],
        "window_type": win_type,
        "claude_running": claude_running,
        "session_status": sess_status,
        "liveness": live,
        "health": health,
        "recap": summary["recap"],
        "recap_ts": summary["recap_ts"],
        "pr": summary["pr"],
        "context": ctx,
    }


def _ago(ts: str | None) -> str:
    """Compact 'how long ago' for an ISO timestamp (best-effort, '' on failure)."""
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        secs = (datetime.now(timezone.utc) - t.astimezone(timezone.utc)).total_seconds()
    except (ValueError, TypeError):
        return ""
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def format_peek(p: dict) -> str:
    """Render a peek() dict as a compact human-readable block."""
    dot = {"green": "●", "yellow": "◐", "grey": "○"}.get(p["health"], "○")
    status = p["session_status"] or ("shell" if not p["claude_running"] else "—")
    lines = [
        f"{p['wid']}  {p['name']}   {dot} {p['liveness']}/{p['health']}   "
        f"claude:{'yes' if p['claude_running'] else 'no'} ({status})",
        f"  cwd:     {p['cwd'] or '?'}",
        f"  type:    {p['window_type']}",
    ]
    ctx = p.get("context")
    if ctx and ctx.get("used_tokens"):
        model = f" ({ctx['model']})" if ctx.get("model") else ""
        lines.append(f"  context: ~{ctx['used_tokens'] // 1000}K tokens{model}")
    if p.get("pr"):
        pr = p["pr"]
        lines.append(f"  PR:      {pr.get('url') or pr.get('title') or pr}")
    if p["recap"]:
        ago = _ago(p["recap_ts"])
        head = f"  recap ({ago}):" if ago else "  recap:"
        # indent wrapped recap lines under the label
        body = "\n".join("    " + ln for ln in p["recap"].splitlines())
        lines.append(head + "\n" + body)
    else:
        lines.append("  recap:   (none yet)")
    return "\n".join(lines)


# --- read: distilled transcript ------------------------------------------------

def _short_ts(ts: str | None) -> str:
    if not ts:
        return "--:--"
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return t.strftime("%H:%M")
    except (ValueError, TypeError):
        return "--:--"


def _format_turn(turn: dict, cap: int | None) -> str:
    role = "user" if turn["role"] == "user" else "asst"
    text = turn["text"]
    if cap and len(text) > cap:
        text = text[:cap].rstrip() + " …"
    tools = ""
    if turn["role"] == "assistant" and turn["tools"]:
        uniq = list(dict.fromkeys(turn["tools"]))
        tools = "  ⚙ " + ", ".join(uniq)
    head = f"[{_short_ts(turn['ts'])}] {role}:"
    if not text and tools:
        return head + tools
    body = "\n".join("    " + ln for ln in text.splitlines())
    return f"{head}{tools}\n{body}" if text else head + tools


def read(wid: str, *, tail: int | None = None, query: str | None = None,
         all_turns: bool = False) -> dict:
    """Distilled read of a sibling's transcript. Returns
    ``{"ok", "error"?, "wid", "name", "mode", "turns": [rendered str], "count"}``.

    Modes (mutually exclusive; ``tail`` is the default):
      - ``tail=N``   → the last N conversation turns (role + text, not raw JSON).
      - ``query=Q``  → turns whose text contains every whitespace-split term in Q
                       (case-insensitive substring — a solid grep/FTS fallback;
                       the memory-stack index is not wired for transcripts here).
      - ``all_turns``→ the whole conversation, uncapped.
    """
    win = resolve_window(wid)
    if win is None:
        return {"ok": False, "error": f"{wid} is not a live window", "wid": wid}
    path = sessions.transcript_for_window(wid)
    if path is None:
        return {"ok": False, "error": f"no transcript found for {wid} ({win['cwd']}): "
                f"{sessions.explain(wid)}",
                "wid": wid, "name": win["name"]}

    turns = list(transcripts.iter_turns(path))

    if query:
        terms = [t.lower() for t in query.split() if t.strip()]
        matched = [t for t in turns if all(term in t["text"].lower() for term in terms)]
        rendered = [_format_turn(t, _DIGEST_TURN_CHARS) for t in matched]
        return {"ok": True, "wid": wid, "name": win["name"], "mode": f"query:{query}",
                "turns": rendered, "count": len(rendered), "scanned": len(turns)}

    if all_turns:
        rendered = [_format_turn(t, None) for t in turns]
        return {"ok": True, "wid": wid, "name": win["name"], "mode": "all",
                "turns": rendered, "count": len(rendered)}

    n = tail if tail and tail > 0 else 10
    recent = turns[-n:]
    rendered = [_format_turn(t, _DIGEST_TURN_CHARS) for t in recent]
    return {"ok": True, "wid": wid, "name": win["name"], "mode": f"tail:{n}",
            "turns": rendered, "count": len(rendered), "total": len(turns)}
