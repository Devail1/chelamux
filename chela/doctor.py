"""``chela doctor`` — does the config a process is RUNNING with still match the env file?

The failure this exists to catch is not a crash. It is silence. Three examples, all from
one week:

* ``ecosystem.config.js`` carried ``CHELA_TMUX_SESSION: 'ccbot'`` in three ``env:``
  blocks for a day after the tmux session was renamed to ``chela``. Nothing complained —
  the live processes had the right value in their environment already, and only a clean
  ``pm2 start`` would have brought the fleet up against a session that no longer exists.
* The dashboard bound port 5005 from a ``--port`` flag, so the port lived *inside that
  one process*. ``chela plugin``, a different process, rendered the hooks manifest
  against the default 5001. Every hook then POSTed into a closed socket and failed open,
  exactly as designed — so the entire hook feature did nothing, and said nothing.
* ``pm2 restart --update-env`` MERGES the environment; it does not delete a variable you
  removed. A var you think you deleted is still in the running process.

Each is the same shape: two copies of one fact, disagreeing, with no one to notice. So
this module compares — for real, against the *running* processes and files — and returns
findings. :data:`ERROR` findings mean something is broken right now; the CLI exits 1.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from chela import config

OK = "ok"
WARN = "warn"
ERROR = "error"

_SYMBOL = {OK: "✓", WARN: "!", ERROR: "✗"}

# The variables the env file is expected to own. Anything else in it is still sourced;
# these are the ones a drift is worth naming.
KNOWN_VARS = (
    "CHELA_DIR",
    "CHELA_TMUX_SESSION",
    "CHELA_DASHBOARD_PORT",
    "CHELA_DASH_HOST",
    "CHELA_IGNORE_WINDOWS",
    "CHELA_TERMINALS_ENABLED",
    "CHELA_TERMINALS_EXPOSE",
    "CHELA_DISPATCH_WORKFLOWS",
    "CHELA_GATE_WAIT_S",
    "CHELA_GATE_MAX_WAITS",
)


@dataclass(frozen=True)
class Finding:
    level: str
    title: str
    detail: str = ""

    def render(self) -> str:
        line = f"{_SYMBOL.get(self.level, '·')} {self.title}"
        return f"{line}\n    {self.detail}" if self.detail else line


def check() -> list[Finding]:
    """Every check, in the order a human wants to read them."""
    findings: list[Finding] = []
    declared = _check_env_file(findings)
    _check_drift(findings, declared)
    _check_session(findings, declared)
    port = _check_dashboard_port(findings)
    _check_plugin(findings, port)
    return findings


def _check_env_file(findings: list[Finding]) -> dict[str, str]:
    path = config.env_file_path()
    if path is None:
        findings.append(Finding(
            WARN, "env file disabled (CHELA_ENV_FILE is empty)",
            "Config comes from the process environment only.",
        ))
        return {}
    if not path.exists():
        findings.append(Finding(
            WARN, f"no env file at {path}",
            "Running on defaults / whatever is exported. Copy examples/chela.env there "
            "to make the config a file instead of a habit.",
        ))
        return {}

    declared = config.parse_env_file(path)
    findings.append(Finding(OK, f"env file {path} ({len(declared)} vars)"))

    # The file cannot relocate itself: CHELA_DIR is what tells us where to LOOK for it.
    dir_in_file = declared.get("CHELA_DIR")
    if dir_in_file and Path(dir_in_file).expanduser() != path.parent:
        findings.append(Finding(
            ERROR, f"{path} sets CHELA_DIR={dir_in_file}, but it lives in {path.parent}",
            "The env file is found *via* CHELA_DIR, so it cannot move itself. Export "
            "CHELA_DIR in the environment (the launcher does), or drop the line.",
        ))
    return declared


def _check_drift(findings: list[Finding], declared: dict[str, str]) -> None:
    """The running environment vs the file. A difference is not automatically wrong — an
    explicit export is *meant* to win — but it is always worth saying out loud, because
    it is indistinguishable from ``pm2 restart --update-env`` carrying a stale value."""
    drifted = [
        (key, os.environ[key], value)
        for key, value in declared.items()
        if key in os.environ and os.environ[key] != value and key in KNOWN_VARS
    ]
    for key, running, in_file in drifted:
        findings.append(Finding(
            WARN, f"{key}: running with {running!r}, env file says {in_file!r}",
            "An exported value wins over the file. If this is a stale PM2 env, "
            "`pm2 restart --update-env` will NOT clear it — `pm2 delete <app>` then "
            "`pm2 start ecosystem.config.js` from a non-tmux shell.",
        ))
    if not drifted and declared:
        findings.append(Finding(OK, "the running environment agrees with the env file"))


def _check_session(findings: list[Finding], declared: dict[str, str]) -> None:
    session = config.current_session()
    if os.environ.get("CHELA_TMUX_SESSION"):
        findings.append(Finding(OK, f"tmux session {session!r} (CHELA_TMUX_SESSION)"))
    elif os.environ.get("TMUX_PANE"):
        findings.append(Finding(
            WARN, f"tmux session {session!r} — DERIVED from $TMUX_PANE, not configured",
            "Correct for an agent in its own pane; wrong for a service, where a leaked "
            "TMUX_PANE silently targets a webterm_* mirror session. Services must start "
            "via scripts/run-chela.sh (`env -u TMUX -u TMUX_PANE`).",
        ))
    else:
        findings.append(Finding(OK, f"tmux session {session!r} (default)"))


def _check_dashboard_port(findings: list[Finding]) -> int:
    """The one that broke the hooks: configured port vs the port really being served."""
    configured = config.dashboard_port()
    live = config.live_dashboard()
    if live is None:
        findings.append(Finding(
            OK, f"dashboard port {configured} (configured; no dashboard running)",
            f"Nothing has published {config.dashboard_port_file()} — a `chela plugin` "
            "rendered now targets the configured port.",
        ))
        return configured
    if live["port"] != configured:
        findings.append(Finding(
            ERROR,
            f"dashboard is LISTENING on {live['port']}, but the config says {configured}",
            "A --port flag beats the env, and the env is supposed to be the source of "
            f"truth. Set CHELA_DASHBOARD_PORT={live['port']} in the env file and restart "
            "the dashboard without --port. (`chela plugin` follows the live port, so "
            "hooks still work — but the next clean start will not.)",
        ))
        return live["port"]
    findings.append(Finding(OK, f"dashboard listening on {live['port']} (pid {live['pid']})"))
    return live["port"]


def _check_plugin(findings: list[Finding], port: int) -> None:
    """A rendered plugin bakes the port in as a literal. If the dashboard moved since,
    the manifest points at a closed socket — and a hook that fails open says nothing."""
    manifest = config.CHELA_DIR / "plugin" / "hooks" / "hooks.json"
    if not manifest.exists():
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        url = data["hooks"]["SessionStart"][0]["hooks"][0]["url"]
        rendered = int(url.rsplit(":", 1)[1].split("/", 1)[0])
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        findings.append(Finding(WARN, f"cannot read the rendered plugin at {manifest}"))
        return
    if rendered != port:
        findings.append(Finding(
            ERROR, f"the rendered plugin POSTs to port {rendered} — the dashboard is on {port}",
            f"Every hook is posting into a closed socket (and failing open, silently). "
            f"Re-render it: `chela plugin --dir {manifest.parent.parent}`.",
        ))
    else:
        findings.append(Finding(OK, f"rendered plugin posts to port {rendered}"))
