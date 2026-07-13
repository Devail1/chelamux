"""Claude Code hooks → the event log. **Ingestion only, and OBSERVE-ONLY.**

The transcript only lands an interactive record at *resolution* — the `tool_use` for an
`AskUserQuestion` or a gated `Bash` is appended to the JSONL when the human answers, not
when the question is asked. That single fact is why every gate chela relays today is
scraped off a tmux pane: at the moment the agent is *blocked*, the structured channel is
empty. **Hooks are the missing channel** — typed, structured, and delivered *before* the
fact, with the full `tool_input` attached.

This module turns a hook POST into an :mod:`chela.event_log` record. It does nothing
else. It answers no gate, decides no permission, and returns no verdict to the agent —
see :func:`chela.dashboard.app.api_hooks`. The pane-scraped gates
(``chela/telegram/{panescan,gatewatch,interactive}.py``) stay exactly as they are, and
must: hooks are read at agent **startup**, so an already-running fleet has none, and a
fleet member launched without the plugin never will.

**A hook runs synchronously inside a live agent.** Everything here is written to that
constraint:

* the transport is ``http`` — no shell script, no process spawn per tool call, no PATH
  assumption, and no way for a chatty ``.bashrc`` to corrupt the JSON contract;
* ``timeout`` is short (:data:`HOOK_TIMEOUT`) and the receiver appends and returns;
* the daemon being **down** must not wedge an agent — a refused connection is a lost
  event (fail OPEN), never a stalled tool call;
* :func:`ingest` never raises, and neither does :func:`chela.event_log.append`.

**Correlation without the pane.** An event carries ``cwd`` and ``session_id``, never a
window. tmux is asked once (``pane_current_path``) and the answer is cached — see
:func:`wid_for_cwd`. If you find yourself capturing a pane to identify an event, you have
reinvented the thing this replaces.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from chela import config, event_log

log = logging.getLogger(__name__)

# Every hook event Claude Code emits (measured on 2.1.207 — `AskUserQuestion` and
# `ExitPlanMode` are NOT events, they are TOOLS, and arrive as PreToolUse /
# PermissionRequest carrying `tool_name` + the full `tool_input`).
HOOK_EVENTS: tuple[str, ...] = (
    "PreToolUse", "PostToolUse", "PermissionRequest", "PermissionDenied",
    "UserPromptSubmit", "SessionStart", "SessionEnd", "Stop",
    "SubagentStart", "SubagentStop", "Notification",
    "PreCompact", "PostCompact", "Elicitation",
)

# The events whose hook entry takes a tool `matcher` ("*" = every tool).
TOOL_EVENTS: frozenset[str] = frozenset(
    {"PreToolUse", "PostToolUse", "PermissionRequest", "PermissionDenied"}
)

# Seconds. The agent BLOCKS on this. An append is sub-millisecond; the budget is for a
# daemon that is briefly busy, not for anything slow to happen inside the request.
HOOK_TIMEOUT = 2

# Event types are namespaced: `hook.pre_tool_use` says *an agent told us this*, as
# against `run_review` / `died` / `daemon_start`, which are chela's own bookkeeping.
TYPE_PREFIX = "hook."

# --- payload bounds --------------------------------------------------------------
# A `Write` of a 200 KB file carries that file in `tool_input.content`. The log is a
# line-per-event JSONL that a human tails, so the payload is CLIPPED, never dropped:
# what a bound has to protect is the per-option `label`/`description` of an
# AskUserQuestion (tens of bytes) and a Bash `command` — the things a decision is
# actually made from.
MAX_BODY = 1024 * 1024      # a POST larger than this is not read at all
MAX_STR = 2000              # per string value
MAX_ITEMS = 100             # per list
MAX_DEPTH = 8
MAX_PAYLOAD = 32 * 1024     # the encoded payload; past this it degrades to a stub
MAX_SUMMARY = 160


def event_type(hook_event_name: str) -> str:
    """``PreToolUse`` → ``hook.pre_tool_use`` — the log's ``type`` for a hook event."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", hook_event_name).lower()
    return f"{TYPE_PREFIX}{snake}"


# --- the plugin manifest ----------------------------------------------------------
#
# Shipped as a PLUGIN, and never by writing the user's settings.json: that file holds
# hundreds of hand-curated permission entries, and chela has no business opening it.
# Plugin hooks MERGE additively with the user's own (and fire last, at the lowest
# precedence) — which is exactly right for something that only observes.

def hook_url(event: str, port: int | None = None, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{port or config.dashboard_port()}/hooks/{event}"


def hooks_spec(port: int | None = None) -> dict:
    """The plugin's ``hooks/hooks.json`` — one http hook per event, POSTing to the daemon.

    Generated rather than hand-written so the committed manifest and the endpoint that
    serves it cannot drift apart (``tests/test_hooks.py`` asserts the file on disk still
    equals this).
    """
    hooks: dict[str, list[dict]] = {}
    for event in HOOK_EVENTS:
        entry: dict = {}
        if event in TOOL_EVENTS:
            entry["matcher"] = "*"
        entry["hooks"] = [{
            "type": "http",
            "url": hook_url(event, port),
            "timeout": HOOK_TIMEOUT,
        }]
        hooks[event] = [entry]
    return {"hooks": hooks}


def plugin_manifest() -> dict:
    from chela import __version__
    return {
        "name": "chela",
        "version": __version__,
        "description": "Feed a chela fleet's event log from Claude Code hooks "
                       "(observe-only — it never answers a prompt).",
        "author": {"name": "chela"},
        "homepage": "https://github.com/Devail1/chelamux",
        "license": "MIT",
        "keywords": ["observability", "orchestration", "tmux"],
    }


def marketplace_manifest(source: str = "./plugin") -> dict:
    return {
        "name": "chela",
        "owner": {"name": "chela"},
        "description": "chela — a tmux-driven orchestrator for Claude Code agents.",
        "plugins": [{
            "name": "chela",
            "source": source,
            "description": plugin_manifest()["description"],
        }],
    }


def render_plugin(directory: Path, port: int | None = None) -> Path:
    """Write a ready-to-install plugin (with the given port baked in) to ``directory``.

    The committed ``plugin/`` targets chela's DEFAULT port. Anyone running the dashboard
    elsewhere needs their own copy, because a hook ``url`` is a literal — hence this.
    The rendered directory is also a one-plugin marketplace, so it installs either way:

        chela plugin --dir ~/.chela/plugin
        claude --plugin-dir ~/.chela/plugin          # this session only
        /plugin marketplace add ~/.chela/plugin      # persistently
    """
    directory = Path(directory).expanduser()
    (directory / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (directory / "hooks").mkdir(parents=True, exist_ok=True)
    _write_json(directory / ".claude-plugin" / "plugin.json", plugin_manifest())
    _write_json(directory / ".claude-plugin" / "marketplace.json",
                marketplace_manifest(source="./"))
    _write_json(directory / "hooks" / "hooks.json", hooks_spec(port))
    return directory


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --- correlation: cwd → window, without touching a pane ---------------------------

_PANE_TTL = 1.0
_panes_cache: dict = {"ts": 0.0, "by_cwd": {}}
_panes_lock = threading.Lock()


def _load_panes() -> dict[str, list[tuple[str, str]]]:
    """``{cwd: [(window_id, pane_command), …]}`` — ONE tmux call, no pgrep, no pane read.

    ``#{pane_current_path}`` is the pane process's cwd, and a Claude Code agent *is* the
    pane process, so it is the same cwd the hook reports. That makes correlation a single
    ~5 ms subprocess rather than the pid→cwd dance ``/api/agents`` does (a per-window
    ``pgrep`` plus ``claude agents --json``, which can take seconds — far too slow for
    something an agent is blocked on).
    """
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", config.current_session(), "-F",
             "#{window_id}\t#{pane_current_command}\t#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}
    if result.returncode != 0:
        return {}
    by_cwd: dict[str, list[tuple[str, str]]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        wid, command, cwd = (p.strip() for p in parts)
        if wid and cwd:
            by_cwd.setdefault(_norm(cwd), []).append((wid, command))
    return by_cwd


def _panes(force: bool = False) -> dict[str, list[tuple[str, str]]]:
    now = time.time()
    if not force and now - _panes_cache["ts"] < _PANE_TTL:
        return _panes_cache["by_cwd"]
    with _panes_lock:
        if not force and time.time() - _panes_cache["ts"] < _PANE_TTL:
            return _panes_cache["by_cwd"]     # a concurrent caller refreshed it
        _panes_cache["by_cwd"] = _load_panes()
        _panes_cache["ts"] = time.time()
    return _panes_cache["by_cwd"]


def _norm(path: str) -> str:
    """Both sides of the comparison through the same normaliser (symlinks, trailing /)."""
    try:
        return os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return path


def wid_for_cwd(cwd: str | None) -> str | None:
    """The chela window an event came from, or None if it cannot be said for certain.

    Ambiguity resolves to **None**, never to a guess: two agents in one cwd would make a
    wrong ``wid`` indistinguishable from a right one, and an event filed against the
    wrong window is worse than an event filed against no window — the ``cwd`` and
    ``session_id`` are in the payload either way, so nothing is lost but the shortcut.
    """
    if not cwd:
        return None
    key = _norm(cwd)
    candidates = _panes().get(key)
    if not candidates:
        # A window that appeared since the last refresh — a freshly spawned agent's
        # SessionStart is exactly this case. One forced tmux call, only on a miss.
        candidates = _panes(force=True).get(key)
    if not candidates:
        return None
    claude = [wid for wid, command in candidates if command == "claude"]
    if len(claude) == 1:
        return claude[0]
    if claude:
        return None                            # two agents, one cwd: cannot disambiguate
    # No pane reports `claude` as its command (a wrapper, a different launcher). Fall
    # back to the cwd being unique among windows — still an unambiguous answer.
    return candidates[0][0] if len(candidates) == 1 else None


# --- clipping ---------------------------------------------------------------------

def _clip(value, depth: int = 0):
    """Bound a payload without losing the fields a decision is made from."""
    if depth >= MAX_DEPTH:
        return "…"
    if isinstance(value, str):
        if len(value) <= MAX_STR:
            return value
        return value[:MAX_STR] + f"… [+{len(value) - MAX_STR} chars]"
    if isinstance(value, dict):
        return {str(k): _clip(v, depth + 1) for k, v in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, list):
        out = [_clip(v, depth + 1) for v in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            out.append(f"… [+{len(value) - MAX_ITEMS} items]")
        return out
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_STR]


def clip_payload(body: dict) -> dict:
    """The hook body, bounded. Degrades to a stub rather than writing a megabyte line."""
    clipped = _clip(body)
    try:
        size = len(json.dumps(clipped, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        size = MAX_PAYLOAD + 1
    if size > MAX_PAYLOAD:
        log.warning("hooks: payload of %s is %d bytes — storing a stub",
                    body.get("hook_event_name"), size)
        return {
            "hook_event_name": body.get("hook_event_name"),
            "tool_name": body.get("tool_name"),
            "session_id": body.get("session_id"),
            "cwd": body.get("cwd"),
            "clipped": True,
        }
    return clipped


# --- the one-line summary ---------------------------------------------------------

def _tool_detail(tool: str, tool_input: dict) -> str:
    """The one thing about a tool call a human reading a notification needs."""
    if tool == "Bash":
        return str(tool_input.get("command") or "")
    if tool in ("Read", "Write", "Edit", "NotebookEdit"):
        return str(tool_input.get("file_path") or "")
    if tool in ("Grep", "Glob"):
        return str(tool_input.get("pattern") or "")
    if tool == "Task":
        return str(tool_input.get("description") or "")
    if tool == "AskUserQuestion":
        questions = tool_input.get("questions")
        if isinstance(questions, list) and questions and isinstance(questions[0], dict):
            return str(questions[0].get("question") or "")
        return ""
    if tool == "ExitPlanMode":
        return "plan ready for approval"
    for value in tool_input.values():
        if isinstance(value, str) and value:
            return value
    return ""


def summarize(event: str, body: dict) -> str:
    """One line. The essay stays in the payload — that split is the log's whole point."""
    tool = str(body.get("tool_name") or "")
    tool_input = body.get("tool_input")
    detail = _tool_detail(tool, tool_input if isinstance(tool_input, dict) else {})
    suffix = f": {detail}" if detail else ""

    if event == "PreToolUse":
        text = f"{tool or 'tool'}{suffix}"
    elif event == "PostToolUse":
        text = f"{tool or 'tool'} done{suffix}"
    elif event == "PermissionRequest":
        # THE event this whole subsystem exists for: it fires while the agent is
        # BLOCKED — in every permission mode, `auto` included — and carries the full
        # tool_input the pane can only show truncated.
        text = f"permission asked — {tool or 'tool'}{suffix}"
    elif event == "PermissionDenied":
        text = f"permission denied — {tool or 'tool'}{suffix}"
    elif event == "UserPromptSubmit":
        text = f"prompt: {body.get('prompt') or ''}"
    elif event == "SessionStart":
        text = f"session start ({body.get('source') or 'unknown'})"
    elif event == "SessionEnd":
        text = f"session end ({body.get('reason') or 'unknown'})"
    elif event == "Stop":
        text = f"stopped: {body.get('last_assistant_message') or ''}"
    elif event == "SubagentStart":
        text = f"subagent start{suffix}"
    elif event == "SubagentStop":
        text = "subagent stop"
    elif event == "Notification":
        text = f"notification: {body.get('message') or ''}"
    elif event in ("PreCompact", "PostCompact"):
        text = "compacting" if event == "PreCompact" else "compacted"
    elif event == "Elicitation":
        text = f"elicitation: {body.get('message') or ''}"
    else:
        text = event

    text = " ".join(text.split())
    return text[:MAX_SUMMARY - 1] + "…" if len(text) > MAX_SUMMARY else text


# --- ingest -----------------------------------------------------------------------

def ingest(event: str, body) -> dict | None:
    """One hook POST → one log record. Returns the record, or None if it was dropped.

    NEVER raises. The caller is a Flask route serving an agent that is blocked on this
    request: a 500 here is a stalled tool call, and a malformed body is a bug in *our*
    parsing, not grounds for breaking someone's session.

    ``event`` comes from the URL, not from the body: the URL is what the plugin we ship
    controls, so it cannot be spoofed by a payload.
    """
    try:
        if event not in HOOK_EVENTS:
            log.debug("hooks: unknown event %r — dropped", event)
            return None
        if not isinstance(body, dict):
            log.warning("hooks: %s body is %s, not an object — dropped",
                        event, type(body).__name__)
            return None
        cwd = body.get("cwd")
        return event_log.append(
            event_type(event),
            summarize(event, body),
            clip_payload(body),
            wid=wid_for_cwd(cwd if isinstance(cwd, str) else None),
            session_id=body.get("session_id") if isinstance(body.get("session_id"), str)
            else None,
        )
    except Exception:                          # noqa: BLE001 — see the docstring
        log.exception("hooks: ingest failed for %s — event dropped", event)
        return None
