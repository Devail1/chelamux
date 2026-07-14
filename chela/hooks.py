"""Claude Code hooks → the event log. **Ingestion only** — this module never decides.

The transcript only lands an interactive record at *resolution* — the `tool_use` for an
`AskUserQuestion` or a gated `Bash` is appended to the JSONL when the human answers, not
when the question is asked. That single fact is why every gate chela relays today is
scraped off a tmux pane: at the moment the agent is *blocked*, the structured channel is
empty. **Hooks are the missing channel** — typed, structured, and delivered *before* the
fact, with the full `tool_input` attached.

This module turns a hook POST into an :mod:`chela.event_log` record. It does nothing else:
it answers no gate and decides no permission. **One hook does now answer** — a
``PermissionRequest`` for an ``AskUserQuestion`` can be answered from Telegram with zero
keystrokes — but that decision is made in :mod:`chela.gateanswer` and returned by
:func:`chela.dashboard.app.api_hooks`, deliberately in a module of its own, so that
"chela can answer a prompt on your behalf" stays a thing you have to go and *read*, not a
field that quietly appeared in an ingestion path. Every other event still returns ``{}``.
The pane-scraped gates (``chela/telegram/{panescan,gatewatch,interactive}.py``) stay
exactly as they are, and must: hooks are read at agent **startup**, so an already-running
fleet has none, and a fleet member launched without the plugin never will.

**A hook runs synchronously inside a live agent.** Everything here is written to that
constraint:

* the transport is ``http`` — no shell script, no process spawn per tool call, no PATH
  assumption, and no way for a chatty ``.bashrc`` to corrupt the JSON contract;
* ``timeout`` is short (:data:`HOOK_TIMEOUT`) and the receiver appends and returns;
* the daemon being **down** must not wedge an agent — a refused connection is a lost
  event (fail OPEN), never a stalled tool call;
* :func:`ingest` never raises, and neither does :func:`chela.event_log.append`.

**Correlation without the pane.** An event carries ``cwd``, ``session_id`` and
``transcript_path``, never a window. The key is the session's **origin directory** — see
:func:`wid_for_session`. ``cwd`` is NOT a key and is never consulted: it is the session's
*current* directory, which moves the instant an agent ``cd``s, and matching it against a
pane filed the orchestrator's every event against a different agent's window (CMX-48). If
you find yourself capturing a pane to identify an event, you have reinvented the thing
this replaces.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from chela import config, event_log, transcripts

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

# `PermissionRequest` is the one event whose handler may deliberately take its time: it is
# where a gate is ANSWERED from Telegram (:mod:`chela.gateanswer`), and an answer needs a
# human to look at their phone. It gets its own, far longer timeout — and it alone, because
# PreToolUse/PostToolUse are ~78% of the log's volume and must stay fast.
#
# MEASURED, not taken from the docs (Claude Code 2.1.209, against a hook that never
# replies; `claude -p` baseline 4.5s): declared 10s → the turn blocked 10.2s; declared 65s
# → 66s; declared 130s → 133s. The declared timeout is honoured VERBATIM — there is no 60s
# clamp — and when it expires the harness fails open and the turn proceeds unharmed. So
# this ceiling is real, and `CHELA_GATE_WAIT_S` (the human's budget) is clamped strictly
# below it: an answer that arrives after Claude Code has killed the hook is an answer
# nobody receives.
GATE_TIMEOUT = 120


def hook_timeout(event: str) -> int:
    """The ``timeout`` this event's hook entry declares. See :data:`GATE_TIMEOUT`."""
    return GATE_TIMEOUT if event == "PermissionRequest" else HOOK_TIMEOUT

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
# precedence) — so chela's hooks ride alongside whatever the user already runs rather
# than overriding any of it.

def hook_url(event: str, port: int | None = None, host: str = "127.0.0.1") -> str:
    """The URL a hook POSTs to. The port defaults to the one the running dashboard
    PUBLISHED (``config.live_dashboard_port``), not merely the one configured — a
    manifest rendered against a port nobody is listening on is the whole CMX-41 bug."""
    return f"http://{host}:{port or config.live_dashboard_port()}/hooks/{event}"


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
            "timeout": hook_timeout(event),
        }]
        hooks[event] = [entry]
    return {"hooks": hooks}


def plugin_manifest() -> dict:
    from chela import __version__
    return {
        "name": "chela",
        "version": __version__,
        "description": "Feed a chela fleet's event log from Claude Code hooks, and "
                       "answer an AskUserQuestion from Telegram with no keystrokes.",
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


# --- the copy that actually RUNS ---------------------------------------------------
#
# `chela plugin` writes a manifest that no agent ever reads. `/plugin install` COPIES the
# plugin into Claude Code's own cache, and *that* copy is what every agent loads at
# startup. The two drifted for a day — the rendered manifest said `PermissionRequest`
# timeout 120, the installed one still said 2 — so every gate hook was killed after two
# seconds, no gate was ever held, and the phone's answer buttons never appeared. Every
# check was green, because every check read the file we WRITE.
#
# So: find the copy an agent would load, and read *it*. Two things make that honest.
#
#   * The path is DISCOVERED, never constructed. Claude Code records it in
#     `installed_plugins.json` (`plugins["chela@<marketplace>"][].installPath`), and that
#     path contains the plugin VERSION — `…/cache/chela/chela/0.1.0/`. Build the path from
#     a hardcoded version and the day someone bumps `plugin.json` you are checking a
#     directory that no longer exists, silently. Read the path Claude Code wrote down.
#   * The cache is Claude Code's implementation detail and may change shape between
#     releases. When it does, the only acceptable failure is a loud "I cannot verify
#     this" — a silent pass here would be the exact bug being fixed, reintroduced one
#     level up. Hence :attr:`InstalledPlugin.error`, which the doctor reports as an ERROR.
#
# chela DETECTS and INSTRUCTS; it never writes into that cache. It is not ours, a
# reinstall would overwrite whatever we put there, and Claude Code's own bookkeeping
# (version, `installedAt`, the marketplace it came from) would then describe a copy it
# did not install — a fourth place for one fact to live, which is how we got here.

PLUGIN_NAME = "chela"


def claude_config_dir() -> Path:
    """Claude Code's config directory — ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``."""
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def plugins_dir() -> Path:
    return claude_config_dir() / "plugins"


@dataclass(frozen=True)
class InstalledPlugin:
    """One installed copy of chela's plugin — the manifest an agent actually loads.

    ``hooks`` is the parsed manifest, or ``None`` with ``error`` set when it cannot be
    read or is not the shape we know. Never both.
    """

    root: Path
    version: str | None
    found_via: str
    hooks: dict | None
    error: str | None

    @property
    def manifest(self) -> Path:
        return self.root / "hooks" / "hooks.json"


def _registered_copies() -> list[tuple[Path, str | None]]:
    """``(installPath, version)`` per installed copy, from Claude Code's own bookkeeping.

    Version-proof by construction: the path is *recorded*, not reconstructed.
    """
    path = plugins_dir() / "installed_plugins.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return []
    out: list[tuple[Path, str | None]] = []
    for key, entries in plugins.items():
        # the key is `<plugin>@<marketplace>`; the marketplace is whatever the operator
        # named it, so only the plugin half is ours to match.
        if str(key).split("@", 1)[0] != PLUGIN_NAME or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            install = entry.get("installPath")
            if isinstance(install, str) and install:
                version = entry.get("version")
                out.append((Path(install).expanduser(),
                            version if isinstance(version, str) else None))
    return out


def _cached_copies() -> list[tuple[Path, str | None]]:
    """The fallback, when the registry is missing or has changed shape: scan the cache.

    ``<plugins>/cache/<marketplace>/<plugin>/<version>/`` — globbed at every level,
    including the version, so a bump moves the directory and this still finds it.
    """
    try:
        manifests = sorted(
            (plugins_dir() / "cache").glob(f"*/{PLUGIN_NAME}/*/hooks/hooks.json"))
    except OSError:
        return []
    return [(m.parent.parent, m.parent.parent.name) for m in manifests]


def installed_plugins() -> list[InstalledPlugin]:
    """Every installed copy of chela's plugin — what agents READ, not what we render.

    An empty list means no agent is running chela's hooks at all. It is never a pass.
    """
    found, via = _registered_copies(), "installed_plugins.json"
    if not found:
        found, via = _cached_copies(), "a scan of the plugin cache"
    copies: list[InstalledPlugin] = []
    for root, version in found:
        data: dict | None = None
        error: str | None = None
        try:
            parsed = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("hooks"), dict):
                error = "it has no `hooks` object — not a manifest shape chela knows"
            else:
                data = parsed
        except FileNotFoundError:
            error = "there is no hooks/hooks.json in the installed copy"
        except OSError as exc:
            error = f"it cannot be read: {exc}"
        except ValueError as exc:
            error = f"it is not valid JSON: {exc}"
        copies.append(InstalledPlugin(root, version, via, data, error))
    return copies


def _first_hook(entries) -> dict | None:
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    declared = entries[0].get("hooks")
    if not isinstance(declared, list) or not declared or not isinstance(declared[0], dict):
        return None
    return declared[0]


def manifest_drift(installed: dict, expected: dict) -> list[str]:
    """What the installed manifest says vs what we render, per event. ``[]`` = they agree.

    Both the ``url`` (CMX-41: a port nobody serves) and the ``timeout`` (CMX-56: a gate
    hook killed after 2s) are load-bearing, and a drift in either kills the feature
    silently — so both are compared, and the difference is named rather than summarised.
    """
    got = installed.get("hooks") if isinstance(installed, dict) else None
    if not isinstance(got, dict):
        return ["the installed manifest has no `hooks` object"]
    want = expected.get("hooks") or {}
    drift: list[str] = []
    for event, entries in want.items():
        want_hook = _first_hook(entries) or {}
        got_hook = _first_hook(got.get(event))
        if got_hook is None:
            drift.append(
                f"{event}: MISSING from the installed manifest (we render "
                f"{want_hook.get('url')}, timeout {want_hook.get('timeout')}s)")
            continue
        for field in ("url", "timeout"):
            if got_hook.get(field) != want_hook.get(field):
                drift.append(
                    f"{event}: {field} is {got_hook.get(field)!r} in the installed "
                    f"manifest, {want_hook.get(field)!r} in the one we render")
    for event in got:
        if event not in want:
            drift.append(f"{event}: the installed manifest hooks an event we no longer do")
    return drift


# --- correlation: session → window, without touching a pane ------------------------
#
# The correlation key is the session's ORIGIN DIRECTORY — the directory the `claude`
# process was launched in — because it is the one thing about a session that does not
# move. Three facts, each measured on Claude Code 2.1.207, make it work:
#
#   1. a session's transcript lives at `~/.claude/projects/<slug>/<session_id>.jsonl`,
#      and `<slug>` is derived from the origin directory ONCE, at session start. It does
#      not follow a `cd`: the orchestrator's session, started in `~` and then `cd`-ed
#      into a repo, still writes to the `~` slug — while its payloads report the repo.
#   2. every hook payload carries `transcript_path`. The slug is therefore already in
#      the event, for free, with NO filesystem access and no /proc walk at all.
#   3. Claude Code never `chdir`s its own process — it tracks the working directory
#      internally (that is exactly why the payload `cwd` and the process cwd disagree).
#      So `#{pane_current_path}` of a `claude` pane IS that pane's origin directory,
#      and encoding it with the same `encode_cwd` yields the same slug.
#
# Both sides of the comparison are now immutable for the life of the session. `cwd` —
# mutable on both sides, and the whole of CMX-48 — is not consulted, not even as a hint:
# the only thing a cwd fallback can add is the confidently-wrong answer this replaced.

_PANE_TTL = 1.0
_panes_cache: dict = {"ts": 0.0, "by_slug": {}}
_panes_lock = threading.Lock()

# session_id → slug. A session's origin never changes, so a hit is cached for the life of
# the process; only misses re-resolve. Bounded — a long-lived daemon sees many sessions.
_SLUG_CACHE_MAX = 1024
_slug_cache: dict[str, str] = {}

# A session id is pasted into a glob, so it is validated as the uuid Claude Code emits
# rather than trusted (`../../` in a payload must not walk the filesystem).
_SESSION_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{7,63}$")


def _load_panes() -> dict[str, list[tuple[str, str]]]:
    """``{slug: [(window_id, pane_command), …]}`` — ONE tmux call, no pgrep, no pane read.

    Keyed by the *project slug* of each pane's path, so the lookup is a dict hit against
    the slug the payload already carries. Correlation stays a single ~5 ms subprocess
    (cached), rather than the pid→cwd dance ``/api/agents`` does (a per-window ``pgrep``
    plus ``claude agents --json``, which can take seconds — far too slow for something an
    agent is blocked on).
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
    by_slug: dict[str, list[tuple[str, str]]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        wid, command, cwd = (p.strip() for p in parts)
        if wid and cwd:
            by_slug.setdefault(transcripts.encode_cwd(_norm(cwd)), []).append(
                (wid, command))
    return by_slug


def _panes(force: bool = False) -> dict[str, list[tuple[str, str]]]:
    now = time.time()
    if not force and now - _panes_cache["ts"] < _PANE_TTL:
        return _panes_cache["by_slug"]
    with _panes_lock:
        if not force and time.time() - _panes_cache["ts"] < _PANE_TTL:
            return _panes_cache["by_slug"]    # a concurrent caller refreshed it
        _panes_cache["by_slug"] = _load_panes()
        _panes_cache["ts"] = time.time()
    return _panes_cache["by_slug"]


def _norm(path: str) -> str:
    """Both sides of the comparison through the same normaliser (symlinks, trailing /)."""
    try:
        return os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return path


def _slug_from_transcript(transcript_path: str | None) -> str | None:
    """``…/projects/<slug>/<session>.jsonl`` → ``<slug>``. A pure string operation."""
    if not transcript_path or not isinstance(transcript_path, str):
        return None
    path = PurePosixPath(transcript_path)
    if path.suffix != ".jsonl":
        return None
    slug = path.parent.name
    return slug or None


def _slug_from_disk(session_id: str) -> str | None:
    """Find the session's project directory on disk — the fallback for a payload with no
    ``transcript_path``. One glob, and only ever on a cache miss."""
    if not _SESSION_RE.match(session_id):
        return None
    try:
        for path in transcripts.CLAUDE_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"):
            return path.parent.name
    except OSError:
        return None
    return None


def session_slug(session_id: str | None, transcript_path: str | None = None) -> str | None:
    """The project slug a session writes its transcript under — its origin, encoded."""
    if session_id and (slug := _slug_cache.get(session_id)):
        return slug
    slug = _slug_from_transcript(transcript_path)
    if not slug and session_id:
        slug = _slug_from_disk(session_id)
    if slug and session_id:
        if len(_slug_cache) >= _SLUG_CACHE_MAX:
            _slug_cache.clear()               # bounded; a rebuild is one string parse
        _slug_cache[session_id] = slug
    return slug


def wid_for_session(session_id: str | None,
                    transcript_path: str | None = None) -> str | None:
    """The chela window a session runs in, or None if it cannot be said for certain.

    Ambiguity resolves to **None**, never to a guess: two agents launched in one directory
    would make a wrong ``wid`` indistinguishable from a right one, and an event filed
    against the wrong window is worse than an event filed against no window — the
    ``session_id``, ``cwd`` and ``transcript_path`` are in the payload either way, so
    nothing is lost but the shortcut.

    A **subagent**'s hooks carry its parent's ``session_id``, so they resolve to the
    parent's window. That is the right answer, not a near-miss: the subagent runs inside
    that agent, in that window, and there is no window of its own to file it against.
    """
    slug = session_slug(session_id, transcript_path)
    if not slug:
        return None
    candidates = _panes().get(slug)
    if not candidates:
        # A window that appeared since the last refresh — a freshly spawned agent's
        # SessionStart is exactly this case. One forced tmux call, only on a miss.
        candidates = _panes(force=True).get(slug)
    if not candidates:
        return None
    claude = [wid for wid, command in candidates if command == "claude"]
    if len(claude) == 1:
        return claude[0]
    if claude:
        return None                            # two agents, one origin: cannot say which
    # No pane reports `claude` as its command (a wrapper, a different launcher). Fall
    # back to the origin being unique among windows — still an unambiguous answer.
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
        session_id = body.get("session_id")
        session_id = session_id if isinstance(session_id, str) else None
        transcript_path = body.get("transcript_path")
        return event_log.append(
            event_type(event),
            summarize(event, body),
            clip_payload(body),
            wid=wid_for_session(
                session_id,
                transcript_path if isinstance(transcript_path, str) else None,
            ),
            session_id=session_id,
        )
    except Exception:                          # noqa: BLE001 — see the docstring
        log.exception("hooks: ingest failed for %s — event dropped", event)
        return None
