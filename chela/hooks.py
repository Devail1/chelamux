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
  assumption, and no way for a chatty ``.bashrc`` to corrupt the JSON contract — except
  ``SessionStart`` and ``MessageDisplay``, each forced onto ``command`` for its own
  measured reason (see :func:`recap_command` and :func:`message_display_command`);
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

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from chela import config, epoch, event_log, sessions, transcripts

log = logging.getLogger(__name__)

# Every hook event Claude Code emits (measured on 2.1.207 — `AskUserQuestion` and
# `ExitPlanMode` are NOT events, they are TOOLS, and arrive as PreToolUse /
# PermissionRequest carrying `tool_name` + the full `tool_input`). `MessageDisplay`
# is newer (2.1.152+, per CMX-285) and absent on an older pin — Claude Code simply never
# fires a hook it doesn't have, so an adopter on an old version sees no line, never a
# broken one.
HOOK_EVENTS: tuple[str, ...] = (
    "PreToolUse", "PostToolUse", "PermissionRequest", "PermissionDenied",
    "UserPromptSubmit", "SessionStart", "SessionEnd", "Stop", "MessageDisplay",
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


# `SessionStart` is the one event that does NOT ride the http transport: it never fires
# over it (measured — CMX-41), it fires as a `command` hook, and a command hook's STDOUT is
# injected into the agent's context. CMX-41 read that as a hazard and declined it. With
# rooms (CMX-61) it is the delivery mechanism: a restarted agent has forgotten everything
# its room ever told it, and this is the one moment we can hand it back (`rooms.recap`).
#
# The command is a `curl` into the SAME endpoint every other hook POSTs to — NOT a `chela`
# spawn:
#
#   * `chela` is not on an agent's PATH (it is a `uv run` inside the repo), so a
#     `chela room recap` hook would be `command not found` in most fleets and would fail
#     INVISIBLY — a hook that prints nothing is indistinguishable from an agent with no
#     rooms, which is exactly the silent-forgetting bug this fixes;
#   * the daemon already holds the tmux table, `rooms.json` and the log. A curl is a ~5 ms
#     spawn against ~90-250 ms for a Python interpreter that would then re-read all three,
#     and the agent BLOCKS on it;
#   * it fails open by construction: `--fail` so an HTTP error body can never be injected
#     as context, stderr to /dev/null, and `|| true` so a missing curl or a dead daemon
#     exits 0 having printed nothing at all — which is precisely "no recap".
#
# The daemon answers with `hookSpecificOutput.additionalContext` (honoured for
# `SessionStart` — Claude Code 2.1.209 collects `additionalContexts` from its SessionStart
# hooks), or with an EMPTY body when the session's window is in no room: no bytes out, no
# boilerplate in the context of every agent in the fleet.
#
# Being a `command` hook buys a second thing (CMX-160): it runs as a child of the claude
# process itself, so it inherits that process's environment — `$CHELA_WID`, exported into
# the pane's shell ahead of every chela-managed launch (`agent_manager.wid_env_prefix`,
# `spawn.py`). Every OTHER hook rides `http` (Claude Code's own client, which sends the
# payload and nothing of the agent's env), so this is the one place the agent can just SAY
# which window it is rather than have chela infer it from `/proc` and tmux panes — and the
# one place cross-platform, since inference needs `/proc` and this does not. See
# `recap_command` and `_explicit_wid`.
RECAP_TIMEOUT = 5


def hook_timeout(event: str) -> int:
    """The ``timeout`` this event's hook entry declares. See :data:`GATE_TIMEOUT`."""
    if event == "PermissionRequest":
        return GATE_TIMEOUT
    if event == "SessionStart":
        return RECAP_TIMEOUT
    return HOOK_TIMEOUT


# The event "timestamps on messages in the live terminal" maps to now (CMX-285,
# correcting CMX-277's own mechanism — see docs/SPIKE_LIVE_TERMINAL_TIMESTAMPS.md).
# CMX-277 used `systemMessage` on `UserPromptSubmit`/`Stop`, which Claude Code renders as
# its OWN separate `<Line>` above/below the message — visible, but not what Liav meant by
# "timestamps": his verdict on the shipped version was that it "doesn't seem to be
# presented like it does for zoharbabin/claude-code-message-timestamps," and that plugin's
# actual mechanism is `MessageDisplay`, not `systemMessage`. `MessageDisplay` fires once
# per streamed batch of an assistant message (`index` 0-based, `delta` the newly-completed
# text), and its `displayContent` response field replaces the delta ON SCREEN ONLY — the
# stored transcript and what the model sees are untouched (Claude Code's own schema:
# "Display-only: replaces the delta on screen without changing the stored message"). That
# is what lets the marker sit INSIDE the message's own text — genuinely inline — instead
# of arriving as a second, separately-rendered line. Requires Claude Code 2.1.152+.
TIMESTAMP_EVENTS: frozenset[str] = frozenset({"MessageDisplay"})


def message_display_response(body: dict) -> dict:
    """The ``MessageDisplay`` response that stamps ``body``'s delta with a local-time
    marker — but only on the message's FIRST streamed batch (``index == 0``), so the
    marker appears exactly once per assistant reply, not before every chunk.

    Every later batch returns ``{}``: Claude Code's own schema treats an absent
    ``displayContent`` as "display the original," so a later chunk's text renders exactly
    as it would have without this hook at all.

    Local time, ``[HH:MM]`` — no seconds (the marker's job is "roughly when did this
    land," not a stopwatch) and no emoji (a variable-width glyph at the start of every
    message; brackets are narrower, monospace-stable, and read as a marker rather than as
    content). CMX-297; the format CMX-277's ``timestamp_response`` (superseded by this)
    used ``HH:MM:SS``.
    """
    if body.get("index") != 0:
        return {}
    delta = body.get("delta")
    delta = delta if isinstance(delta, str) else ""
    ts = time.strftime("%H:%M")
    return {"hookSpecificOutput": {"hookEventName": "MessageDisplay",
                                    "displayContent": f"[{ts}] {delta}"}}


def recap_command(port: int | None = None, host: str = "127.0.0.1") -> str:
    """The ``SessionStart`` command hook: POST the payload, print the recap, fail open.

    Carries ``$CHELA_WID`` as a header — expanded by the agent's own shell at hook time,
    not baked in here (this string is one manifest shared by the whole fleet). Empty for a
    session chela did not launch (no such env var); the receiver treats an empty or
    unrecognised header exactly like a missing one (:func:`_explicit_wid`).
    """
    return ("curl -s --fail --max-time 3 -X POST "
            "-H 'Content-Type: application/json' "
            "-H \"X-Chela-Wid: ${CHELA_WID:-}\" "
            "--data-binary @- "
            f"{hook_url('SessionStart', port, host)} 2>/dev/null || true")


# CMX-303: `MessageDisplay` shipped a correct daemon response (CMX-285/CMX-297,
# `message_display_response` above) on the `http` transport every hook except
# `SessionStart` rides — and on a live fleet (Claude Code 2.1.233, `TERMINAL_TIMESTAMPS`
# on, the hook confirmed present in the loaded manifest, `curl`ing the endpoint directly
# and getting back a correct `{"hookSpecificOutput": {..., "displayContent": "[18:01]
# …"}}`) zero `[HH:MM]` markers appeared across 2000 lines of scrollback. Every layer
# this repo controls was measured correct; the one layer it does not — how Claude Code's
# own client applies an `http` hook's JSON response versus a `command` hook's stdout,
# despite the docs describing both as sharing one schema — is the remaining suspect, and
# `SessionStart` above is this repo's own prior, working precedent for a hook that needed
# `command` for Claude Code to actually act on what it returns (there for a different,
# already-diagnosed reason — CMX-41, `http` never fires it at all). Moving
# `MessageDisplay` onto the identical curl-relay shape changes only the transport, not the
# daemon logic: the command curls the SAME endpoint `message_display_response` already
# answers correctly, and prints that response body verbatim as the command's own stdout
# for Claude Code to parse.
def message_display_command(port: int | None = None, host: str = "127.0.0.1") -> str:
    """The ``MessageDisplay`` command hook: relay the payload to the daemon and print its
    response, fail open — see the module comment above for why this event needs
    ``command`` at all.

    No ``$CHELA_WID`` header: unlike the room recap, nothing ``message_display_response``
    returns is agent-specific, so there is nothing here worth the extra header.
    ``--max-time 1``, under :data:`HOOK_TIMEOUT`\\ 's 2s: this event fires tens of times
    per assistant reply (CMX-285's own measurement), so a hung curl must lose the race
    against Claude Code's own hook timeout, not tie it.
    """
    return ("curl -s --fail --max-time 1 -X POST "
            "-H 'Content-Type: application/json' "
            "--data-binary @- "
            f"{hook_url('MessageDisplay', port, host)} 2>/dev/null || true")

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
    """The plugin's ``hooks/hooks.json`` — one hook per event, POSTing to the daemon.

    Every event rides ``http`` (no shell, no spawn per tool call) except two forced onto
    ``command`` for their own measured reason: ``SessionStart`` never fires over ``http``
    at all and its stdout is the recap (see :data:`RECAP_TIMEOUT`), and ``MessageDisplay``
    fires over ``http`` but a live fleet never rendered what it returned there (CMX-303 —
    see the comment above :func:`message_display_command`). Both curl the SAME endpoint
    every other event POSTs to and print its response verbatim as their own stdout, so the
    daemon-side logic is identical either way — only the transport differs.

    Generated rather than hand-written so the committed manifest and the endpoint that
    serves it cannot drift apart (``tests/test_hooks.py`` asserts the file on disk still
    equals this).
    """
    hooks: dict[str, list[dict]] = {}
    for event in HOOK_EVENTS:
        entry: dict = {}
        if event in TOOL_EVENTS:
            entry["matcher"] = "*"
        if event == "SessionStart":
            entry["hooks"] = [{
                "type": "command",
                "command": recap_command(port),
                "timeout": hook_timeout(event),
            }]
        elif event == "MessageDisplay":
            entry["hooks"] = [{
                "type": "command",
                "command": message_display_command(port),
                "timeout": hook_timeout(event),
            }]
        else:
            entry["hooks"] = [{
                "type": "http",
                "url": hook_url(event, port),
                "timeout": hook_timeout(event),
            }]
        hooks[event] = [entry]
    return {"hooks": hooks}


_PORT_RE = re.compile(r"127\.0\.0\.1:\d+")


def hooks_fingerprint(port: int | None = None) -> str:
    """A hash of :func:`hooks_spec`, with the per-install port normalized out first.

    Claude Code keys a plugin update on ``plugin.json``'s ``version`` — an install with a
    matching version NO-OPs, hooks and all. So a change to the STRUCTURE of the rendered
    hooks (a header, a timeout, a new event) has to force a version bump, or every existing
    adopter silently keeps the old ones (this is exactly how #181 shipped
    ``X-Chela-Wid`` to nobody already installed). :data:`EXPECTED_HOOKS_FINGERPRINT`
    pins this hash to the version it was recorded for — see
    ``tests/test_hooks.py::test_hooks_fingerprint_matches_the_recorded_version``.

    The port is normalized to a placeholder first: two installs pointed at different
    dashboard ports render byte-different manifests but are the SAME structural hooks, and
    a port difference alone must never trip this guard.
    """
    rendered = json.dumps(hooks_spec(port or config.DEFAULT_DASHBOARD_PORT), sort_keys=True)
    normalized = _PORT_RE.sub("127.0.0.1:PORT", rendered)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# Recorded BY HAND, once per plugin.json version bump — never derived, or a change to
# hooks_spec() and a change to this dict would move together and the guard would prove
# nothing. To add an entry: bump plugin/.claude-plugin/plugin.json's version, then run
# `python -c "from chela import hooks; print(hooks.hooks_fingerprint())"` and paste the
# hash in here keyed by the new version.
EXPECTED_HOOKS_FINGERPRINT: dict[str, str] = {
    "0.2.1": "67b4358055f8df27922da7df6bf99c740ed23c800b19a03f0e41c485b4480bc9",
    "0.2.2": "0cfd26508b63a2804c0f815437e5b5c2564e1b72427bda18754692e199e8408d",
    "0.2.3": "80085a2e2953eed44c8006025ee4563987ad7d08e81f62499d33fe66d812e6b1",
}

PLUGIN_VERSION = "0.2.3"


def plugin_manifest() -> dict:
    return {
        "name": "chela",
        "version": PLUGIN_VERSION,
        "description": "Feed a chela fleet's event log from Claude Code hooks, and "
                       "answer an AskUserQuestion from Telegram with no keystrokes.",
        "author": {"name": "chela"},
        "homepage": "https://github.com/Devail1/chelamux",
        "license": "AGPL-3.0-or-later",
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
    return transcripts.claude_config_dir()


def plugins_dir() -> Path:
    return claude_config_dir() / "plugins"


@dataclass(frozen=True)
class InstalledPlugin:
    """One installed copy of chela's plugin — the manifest an agent actually loads.

    ``hooks`` is the parsed manifest, or ``None`` with ``error`` set when it cannot be
    read or is not the shape we know. Never both. ``marketplace`` is the slug it was
    installed under (the ``<marketplace>`` half of ``<plugin>@<marketplace>``) — what
    ``chela update`` needs to refresh this copy with `claude plugin update
    chela@<marketplace>`; ``None`` only if that could not be determined either.
    """

    root: Path
    version: str | None
    found_via: str
    hooks: dict | None
    error: str | None
    marketplace: str | None = None

    @property
    def manifest(self) -> Path:
        return self.root / "hooks" / "hooks.json"


def _registered_copies() -> list[tuple[Path, str | None, str | None]]:
    """``(installPath, version, marketplace)`` per installed copy, from Claude Code's own
    bookkeeping.

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
    out: list[tuple[Path, str | None, str | None]] = []
    for key, entries in plugins.items():
        # the key is `<plugin>@<marketplace>`; the marketplace is whatever the operator
        # named it when they added it.
        plugin_name, _, marketplace = str(key).partition("@")
        if plugin_name != PLUGIN_NAME or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            install = entry.get("installPath")
            if isinstance(install, str) and install:
                version = entry.get("version")
                out.append((Path(install).expanduser(),
                            version if isinstance(version, str) else None,
                            marketplace or None))
    return out


def _cached_copies() -> list[tuple[Path, str | None, str | None]]:
    """The fallback, when the registry is missing or has changed shape: scan the cache.

    ``<plugins>/cache/<marketplace>/<plugin>/<version>/`` — globbed at every level,
    including the version, so a bump moves the directory and this still finds it. The
    marketplace is read off that same path, one level up from the plugin name.
    """
    try:
        manifests = sorted(
            (plugins_dir() / "cache").glob(f"*/{PLUGIN_NAME}/*/hooks/hooks.json"))
    except OSError:
        return []
    return [
        (m.parent.parent, m.parent.parent.name, m.parent.parent.parent.parent.name)
        for m in manifests
    ]


def installed_plugins() -> list[InstalledPlugin]:
    """Every installed copy of chela's plugin — what agents READ, not what we render.

    An empty list means no agent is running chela's hooks at all. It is never a pass.
    """
    found, via = _registered_copies(), "installed_plugins.json"
    if not found:
        found, via = _cached_copies(), "a scan of the plugin cache"
    copies: list[InstalledPlugin] = []
    for root, version, marketplace in found:
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
        copies.append(InstalledPlugin(root, version, via, data, error, marketplace))
    return copies


# Every field of a hook that decides whether it WORKS. `url` (CMX-41: a port nobody
# serves) and `timeout` (CMX-56: a gate hook killed after 2s) each killed a feature
# silently on their own; `type` and `command` are the same trapdoor for the SessionStart
# recap — an installed copy still declaring the old http SessionStart would inject nothing
# and say nothing.
_HOOK_FIELDS = ("type", "url", "command", "timeout")


def _declared(entries) -> list[dict]:
    """EVERY hook an event declares, across its entries — not merely the first.

    A comparison that reads only ``entries[0]["hooks"][0]`` reports green on a manifest
    that is missing every hook after it. That is the CMX-56 class of bug, one level in.
    """
    out: list[dict] = []
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            out.extend(h for h in entry["hooks"] if isinstance(h, dict))
    return out


def _describe(hooks_: list[dict]) -> str:
    return "; ".join(
        ", ".join(f"{f}={h[f]!r}" for f in _HOOK_FIELDS if h.get(f) is not None)
        for h in hooks_) or "(nothing)"


def manifest_drift(installed: dict, expected: dict) -> list[str]:
    """What the installed manifest says vs what we render, per event. ``[]`` = they agree.

    Compares every declared hook, field by field (:data:`_HOOK_FIELDS`), and names the
    difference rather than summarising it: each of these fields has, on its own, silently
    killed a feature that every check then reported green.
    """
    got = installed.get("hooks") if isinstance(installed, dict) else None
    if not isinstance(got, dict):
        return ["the installed manifest has no `hooks` object"]
    want = expected.get("hooks") or {}
    drift: list[str] = []
    for event, entries in want.items():
        want_hooks = _declared(entries)
        got_hooks = _declared(got.get(event))
        if not got_hooks:
            drift.append(f"{event}: MISSING from the installed manifest (we render "
                         f"{_describe(want_hooks)})")
            continue
        if len(got_hooks) != len(want_hooks):
            drift.append(f"{event}: the installed manifest declares {len(got_hooks)} "
                         f"hook(s), we render {len(want_hooks)}")
        for got_hook, want_hook in zip(got_hooks, want_hooks):
            for field in _HOOK_FIELDS:
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
# process was launched in — because it is *almost* the one thing about a session that does
# not move. Three facts, each measured on Claude Code 2.1.207, make it work:
#
#   1. a session's transcript lives at `~/.claude/projects/<slug>/<session_id>.jsonl`,
#      and `<slug>` is derived from the origin directory ONCE, at session start. It does
#      not follow a `cd`: the orchestrator's session, started in `~` and then `cd`-ed
#      into a repo, still writes to the `~` slug — while its payloads report the repo.
#   2. every hook payload carries `transcript_path`. The slug is therefore already in
#      the event, for free, with NO filesystem access and no /proc walk at all.
#   3. Claude Code never `chdir`s its own process — it tracks the working directory
#      internally (that is exactly why the payload `cwd` and the process cwd disagree).
#      So the *process* cwd of a pane's `claude` IS that pane's origin directory, and
#      encoding it with the same `encode_cwd` yields the same slug.
#
# Both sides of that comparison are immutable for the life of the session. `cwd` — mutable
# on both sides, and the whole of CMX-48 — is not consulted, not even as a hint: the only
# thing a cwd fallback can add is the confidently-wrong answer this replaced.
#
# `--resume` IS THE EXCEPTION, and it is checked FIRST (CMX-70). A session resumed from a
# different directory keeps its transcript in the project dir it was BORN in, so its slug
# names a directory no pane is sitting in — origin-matching then resolves it to None (or,
# worse, to an unrelated agent that happens to live in that birth directory). The pane's
# own command line settles it outright: `claude --resume <sid>` is that window claiming
# that session, by construction. See `chela/sessions.py`, which owns both signals.

# session_id → slug. A session's origin never changes, so a hit is cached for the life of
# the process; only misses re-resolve. Bounded — a long-lived daemon sees many sessions.
_SLUG_CACHE_MAX = 1024
_slug_cache: dict[str, str] = {}


def _panes(force: bool = False) -> dict[str, sessions.Pane]:
    """The live pane map — ONE tmux call plus a few /proc reads, TTL-cached upstream.

    Correlation stays a single ~5 ms subprocess (cached), rather than the pid→cwd dance
    ``/api/agents`` does (a per-window ``pgrep`` plus ``claude agents --json``, which can
    take seconds — far too slow for something an agent is blocked on).
    """
    return sessions.panes(force)


# ``chela.epoch.current()`` is its own ~5 ms tmux subprocess (`display-message`), same
# order of cost as `_panes()` above — but unlike a pane map, the tmux SERVER identity
# essentially never changes between two hooks fired seconds apart, so paying that cost on
# every single hook event (CMX-236 stamps one onto every record — see `ingest` below) buys
# nothing an occasional refresh would not. TTL-cached the same shape as `sessions.panes`,
# deliberately more generous (an epoch flip is a tmux server restart, not a window churn).
_EPOCH_TTL = 5.0
_epoch_cache: dict[str, object] = {"ts": 0.0, "value": None}


def _current_epoch() -> str | None:
    now = time.time()
    if now - _epoch_cache["ts"] >= _EPOCH_TTL:
        _epoch_cache["value"] = epoch.current()
        _epoch_cache["ts"] = now
    return _epoch_cache["value"]


def _by_slug(panes: dict[str, sessions.Pane]) -> dict[str, list[sessions.Pane]]:
    """``{project slug of the pane's ORIGIN: [pane, …]}`` — the lookup the slug hits."""
    out: dict[str, list[sessions.Pane]] = {}
    for pane in panes.values():
        origin = pane.origin
        if origin:
            out.setdefault(transcripts.encode_cwd(_norm(origin)), []).append(pane)
    return out


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
    path = sessions.transcript_for_session(session_id)
    return path.parent.name if path is not None else None


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


# A window id, straight from the shape chela hands out (`@N`) — validated before it is
# ever trusted, the same discipline `SESSION_RE` applies to a session id pasted into a
# glob (`chela/sessions.py`): a header is attacker-adjacent input (it rode an HTTP
# request), so its shape is checked and its claim is checked against a LIVE window before
# either ever reaches an event record.
_WID_RE = re.compile(r"^@\d+$")


def _explicit_wid(hint: str | None,
                  panes: dict[str, sessions.Pane] | None = None) -> str | None:
    """A window id the AGENT ITSELF supplied, via ``$CHELA_WID`` on the one hook whose
    ``command`` string carries it — ``SessionStart``, which inherits its process's env
    (:func:`recap_command`; :func:`message_display_command` is also a ``command`` hook,
    for an unrelated reason, and carries no header) — ground truth, not inference: no pane
    walk, no ``/proc``, nothing that a missing kernel interface can take out from under it
    on macOS.

    Still not trusted blind. Malformed, empty (no such env var — a session chela did not
    launch) or naming a window that is not live right now all fall through to ``None``
    exactly as if the header had never been sent, and the caller re-derives it the old way
    — a bad header must never be WORSE than no header.

    ``panes=None`` (the real caller, never a test with a fixed snapshot) gets ONE retry
    against a forced-fresh read before "not live" is trusted: ``sessions.panes``'s own TTL
    cache (≤1s) can in principle predate a session that just replaced its own claude
    process INSIDE an already-open window (auto-compact, ``/clear`` — the window never
    closed), if ``SessionStart`` fires before the cache refreshes. ``wid_for_session``'s
    own inference fallback already forces a re-read for exactly this shape ("a window that
    appeared since the last refresh"); this mirrors it so the header path gets the same
    second look before a live window is ever called not-live.
    """
    if not hint or not _WID_RE.match(hint):
        return None
    live = _panes() if panes is None else panes
    if hint in live:
        return hint
    if panes is not None:
        return None
    return hint if hint in _panes(force=True) else None


def _explicit_wid_dead(hint: str | None,
                       panes: dict[str, sessions.Pane] | None = None) -> str | None:
    """The ``X-Chela-Wid`` value itself, but ONLY on the one shape :func:`_explicit_wid`
    folds silently into ``None`` alongside "no header at all": well-formed, present, and
    STILL not live after the same forced-refresh retry :func:`_explicit_wid` gives it.

    Unset (no ``$CHELA_WID`` — a session chela did not launch) and malformed both return
    ``None`` here too, same as a live wid — those are never a fault and must never warn.
    A well-formed hint that survives the retry and is still not live USUALLY means the
    agent was relaunched by hand and inherited a stale ``$CHELA_WID`` from tmux's global
    environment (the CMX-192 root cause) — but it can just as well be a window that was
    genuinely THIS session's and simply closed or was replaced moments earlier, which is
    not a fault at all. This function cannot tell those two shapes apart on its own; see
    ``runtime_truth._hooks_rejected_wid_report`` (CMX-236) for the severity split that
    does, scoped to the tmux epoch this record is stamped with below.
    """
    if not hint or not _WID_RE.match(hint):
        return None
    live = _panes() if panes is None else panes
    if hint in live:
        return None
    if panes is not None:
        return hint
    return None if hint in _panes(force=True) else hint


def wid_for_session(session_id: str | None,
                    transcript_path: str | None = None,
                    explicit_wid: str | None = None) -> str | None:
    """The chela window a session runs in, or None if it cannot be said for certain.

    Ambiguity resolves to **None**, never to a guess: two agents launched in one directory
    would make a wrong ``wid`` indistinguishable from a right one, and an event filed
    against the wrong window is worse than an event filed against no window — the
    ``session_id``, ``cwd`` and ``transcript_path`` are in the payload either way, so
    nothing is lost but the shortcut.

    ``explicit_wid`` — the ``X-Chela-Wid`` header the ``SessionStart`` command hook sends
    (CMX-160) — is checked FIRST and, once validated, short-circuits the inference below
    entirely: the agent said which window it is, so there is nothing left to guess.

    A **subagent**'s hooks carry its parent's ``session_id``, so they resolve to the
    parent's window. That is the right answer, not a near-miss: the subagent runs inside
    that agent, in that window, and there is no window of its own to file it against.
    """
    wid = _explicit_wid(explicit_wid)
    if wid:
        return wid
    wid = _wid_in(session_id, transcript_path, _panes())
    if wid:
        return wid
    # A window that appeared since the last refresh — a freshly spawned agent's
    # SessionStart is exactly this case. One forced tmux call, only on a miss.
    return _wid_in(session_id, transcript_path, _panes(force=True))


def _wid_in(session_id: str | None, transcript_path: str | None,
            panes: dict[str, sessions.Pane]) -> str | None:
    """The window a session runs in, against one snapshot of the panes."""
    # A pane running `claude --resume <sid>` IS that session's window, whatever directory
    # it was resumed from — the one signal a `--resume` cannot invalidate (CMX-70).
    claimed = sessions.wid_claiming_session(session_id, panes)
    if claimed:
        return claimed
    slug = session_slug(session_id, transcript_path)
    if not slug:
        return None
    candidates = _by_slug(panes).get(slug)
    if not candidates:
        return None
    claude = [p.wid for p in candidates if p.command == "claude"]
    if len(claude) == 1:
        return claude[0]
    if claude:
        return None                            # two agents, one origin: cannot say which
    # No pane reports `claude` as its command (a wrapper, a different launcher). Fall
    # back to the origin being unique among windows — still an unambiguous answer.
    return candidates[0].wid if len(candidates) == 1 else None


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

def ingest(event: str, body, explicit_wid: str | None = None) -> dict | None:
    """One hook POST → one log record. Returns the record, or None if it was dropped.

    NEVER raises. The caller is a Flask route serving an agent that is blocked on this
    request: a 500 here is a stalled tool call, and a malformed body is a bug in *our*
    parsing, not grounds for breaking someone's session.

    ``event`` comes from the URL, not from the body: the URL is what the plugin we ship
    controls, so it cannot be spoofed by a payload. ``explicit_wid`` is the ``X-Chela-Wid``
    header — set only on ``SessionStart`` (see :func:`recap_command`) — and is validated
    inside :func:`wid_for_session`, not here.

    Every record is also stamped with the tmux epoch it was written under (CMX-236, via
    :func:`_current_epoch`) — ``None`` if it could not be read. ``rejected_wid`` alone
    cannot say whether a dead window is a genuine CMX-192 fault or an ordinary teardown; the
    epoch stamp is what lets ``runtime_truth._hooks_rejected_wid_report`` tell them apart.
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
                explicit_wid=explicit_wid,
            ),
            session_id=session_id,
            rejected_wid=_explicit_wid_dead(explicit_wid),
            epoch=_current_epoch(),
        )
    except Exception:                          # noqa: BLE001 — see the docstring
        log.exception("hooks: ingest failed for %s — event dropped", event)
        return None
