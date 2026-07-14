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

There is a fourth, and it is the one that indicts the three above: the dispatcher was
dead for nine hours and doctor printed ALL-GREEN. ``CHELA_DISPATCH_WORKFLOWS`` was gone
from the env file, so the running environment and the file **agreed — and both were
wrong**. Checking that two copies of a fact match is not checking the fact. So doctor now
asserts the *capability*: is the dispatcher on, does each configured ``WORKFLOW.md``
exist and parse, does its tracker exist — and, because doctor runs in a **different
process** than the daemon, it reads what the RUNNING daemon published
(``$CHELA_DIR/daemon.json``, the ``dashboard.port`` pattern) rather than re-reading the
config that was already lying. With no daemon running it says, out loud, that it is
inferring.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from chela import capabilities, config
from chela.sources import get_source
from chela.workflow import load_workflow

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
    _check_daemon(findings)
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


def _check_daemon(findings: list[Finding]) -> None:
    """The capability check: is the daemon actually DOING the job, not merely configured
    to. Observed from what the running daemon published; inferred (and said to be
    inferred) when none is running."""
    live = capabilities.live()
    if live is None:
        caps = [c.as_dict() for c in capabilities.effective()]
        findings.append(Finding(
            WARN, f"no daemon has published {capabilities.state_file()}",
            "`chela run` is not running (or predates this check). The capabilities below "
            "are INFERRED from this shell's config — they are what a daemon started now "
            "would do, not what anything is doing.",
        ))
    else:
        caps = [c for c in live["capabilities"] if isinstance(c, dict)]
        findings.append(Finding(
            OK, f"daemon running (pid {live.get('pid')}, session "
                f"{live.get('session') or '?'}) — capabilities read from it, not from config"))

    for cap in caps:
        label = cap.get("label") or cap.get("key") or "?"
        if cap.get("on"):
            findings.append(Finding(OK, f"{label}: ON", str(cap.get("detail") or "")))
        elif cap.get("warn_when_off"):
            detail = str(cap.get("detail") or "")
            fix = str(cap.get("fix") or "")
            findings.append(Finding(
                WARN, f"{label}: OFF", f"{detail} — fix: {fix}" if fix else detail))
        # A capability that is merely *unset* (notifications, the inbox) is reported by
        # the daemon's startup log; repeating every off-by-choice toggle here would bury
        # the one that matters. `warn_when_off` is what marks a foot-gun.

    dispatch = next((c for c in caps if c.get("key") == "dispatch"), None)
    if dispatch is None:
        return
    # A daemon started before the env changed carries the OLD capability. That
    # config-vs-running disagreement is exactly the CMX-42 trap, and it is invisible
    # unless something says it: the running process, not the file, is what dispatches —
    # and only a restart closes the gap.
    if live is not None and bool(dispatch.get("on")) != bool(config.DISPATCH_WORKFLOWS):
        findings.append(Finding(
            ERROR,
            "the RUNNING daemon's dispatcher is "
            f"{'ON' if dispatch.get('on') else 'OFF'}, but this shell's config says "
            f"{'ON' if config.DISPATCH_WORKFLOWS else 'OFF'}",
            "The daemon is running on a stale environment — CHELA_DISPATCH_WORKFLOWS "
            "changed after it started. Restart it (e.g. `pm2 restart chela-daemon`); "
            "until then the config describes a daemon that does not exist. (If this "
            "shell simply has a different env than the service, that is the same drift "
            "the env file exists to end — export nothing, source the file.)",
        ))
    _check_workflows(findings, [Path(p) for p in dispatch.get("workflows") or []])


def _check_workflows(findings: list[Finding], paths: list[Path]) -> None:
    """Each dispatched workflow must EXIST, PARSE, and have a tracker to read. All three
    are file reads — no subprocess: doctor is run interactively and must stay instant."""
    for path in paths:
        if not path.exists():
            findings.append(Finding(
                ERROR, f"dispatch workflow {path} does not exist",
                "The daemon is configured to dispatch a file that is not there: it will "
                "claim no work. Fix CHELA_DISPATCH_WORKFLOWS or restore the file.",
            ))
            continue
        try:
            wf = load_workflow(path)
        except Exception as exc:
            findings.append(Finding(
                ERROR, f"dispatch workflow {path.name} does not parse",
                f"{exc} — the daemon keeps reconciling on its last known-good config but "
                "starts NO new work until this parses.",
            ))
            continue
        try:
            source = get_source(wf)
        except Exception as exc:
            findings.append(Finding(ERROR, f"{path.name}: unusable tracker", str(exc)))
            continue
        tracker = getattr(source, "path", None)   # a gh_issues tracker is not a file
        if tracker is not None and not Path(tracker).exists():
            findings.append(Finding(
                ERROR, f"{path.name}: tracker {tracker} does not exist",
                "The dispatcher reads its work items from this file. With no file there "
                "is no queue — and nothing says so.",
            ))
            continue
        findings.append(Finding(
            OK, f"{path.name} parses (project {wf.project_key})",
            f"tracker: {tracker}" if tracker else "tracker: gh_issues",
        ))


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
