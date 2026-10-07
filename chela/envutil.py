"""The environment chela hands to every child it spawns — ``os.environ`` minus PM2's IPC leak.

⛔ CMX-390. pm2 forks each managed process (``chela-daemon``, ``chela-dashboard``, …) through
Node's ``child_process.fork`` — IPC channel included, even for a Python target — so the
daemon's own ``os.environ`` carries ``NODE_CHANNEL_FD=3`` and
``NODE_CHANNEL_SERIALIZATION_MODE=json``. A plain ``subprocess.run(cmd, shell=True)`` hands
both to its child, and any Node program down that tree (``pnpm``, ``npm``, ``node --test``)
treats fd 3 as its parent's IPC channel and aborts: measured 2026-09-29, ``pnpm --version``
under the daemon's exact env printed its version and then exited 134 "core dumped", which
killed every ``before_run`` hook that ran it — and with it every agent and judge launch.

:func:`child_env` is the one place that knows what to drop, so every hook, suite and tmux
launch call site passes ``env=child_env()`` instead of inheriting implicitly.

What is dropped, and why only this:

* ``NODE_CHANNEL_FD`` / ``NODE_CHANNEL_SERIALIZATION_MODE`` / ``NODE_UNIQUE_ID`` — Node's
  IPC-child and cluster-worker markers. A child that is not the process pm2 forked is never
  that IPC child or that cluster worker.
* ``NODE_APP_INSTANCE`` and every ``pm_*`` / ``PM2_*`` var — pm2's per-process bookkeeping
  (``pm_id``, ``pm_exec_path``, ``PM2_USAGE``, …). They describe the DAEMON, not the child,
  and a child that reads ``pm_id`` believes it is itself a pm2-managed process.

Kept on purpose: ``PM2_HOME`` — it is where the pm2 CLI finds its god daemon, and an
``after_done`` hook that runs ``pm2 restart`` (the documented deploy pattern) must reach the
SAME daemon, not a fresh default one. Everything else — ``PATH``, ``HOME``, ``CHELA_*`` —
passes through untouched; this module strips a leak, it is not an environment reset.

🔐 CMX-425 — secrets. ``chela/config.py`` sources ``chela.env`` into ``os.environ`` at import
and ``scripts/run-chela.sh`` adds ``secrets.env``, so the daemon's own env can hold
credentials meant for CHELA (``LINEAR_API_KEY`` for the tracker adapter, ``TELEGRAM_BOT_TOKEN``
for the bridge). None of its children — dispatched agents and judges working in a possibly
PUBLIC repo, a test suite, a workflow hook — needs them, so :func:`child_env` drops every name
:func:`is_secret` matches: the explicit :data:`SECRET_ENV_VARS` plus the obvious shapes
``*_API_KEY`` / ``*_TOKEN`` / ``*_SECRET`` / ``*PASSWORD*``.

Forwarded on purpose (:data:`FORWARDED_SECRET_VARS`), because a child cannot do its job
without them: Claude Code's own credentials (``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``,
``CLAUDE_CODE_OAUTH_TOKEN`` — the agent and judge windows ARE Claude Code, and a tmux server
this env starts hands its env to every window) and the GitHub CLI's (``GH_TOKEN``,
``GITHUB_TOKEN`` — agents and hooks run ``gh``). An operator adds more by name in
``CHELA_CHILD_ENV_FORWARD`` (comma/space separated). ``TELEGRAM_*`` is NOT forwarded: the
bridge is its own service, started by the process manager, never a child of these call sites.

The parent's env is a channel too: a tmux window inherits the tmux SERVER's global
environment, not the env of the client that ran ``new-window``. :func:`tmux_secret_names`
reads that table so a launch path can ``set-environment -gu`` whatever secret a server
started before this fix (or by an operator's shell) still carries.

🧯 CMX-21 — a polluted tmux SERVER. On 2026-10-06 an orphaned perf-harness supervisor
re-created the live tmux server from its own environment, so every window born after it
carried ``HTTPS_PROXY=http://127.0.0.1:9`` (ECONNREFUSED on every request), a parent Claude
session's ``CLAUDE_CODE_SESSION_ID``/``CLAUDE_CODE_CHILD_SESSION`` (transcripts off) and a
``TMUX_TMPDIR`` pointing at a deleted dir. Two more groups, therefore:

* :data:`SESSION_MARKER_VARS` — the markers a running Claude Code session sets for its
  OWN children. A process chela spawns is never that session's child, so
  :func:`child_env` drops them everywhere.
* :func:`is_server_hazard` — proxies, ``TMUX_TMPDIR``, ``PERF_*`` and the harness's ``H``.
  Legitimate in a hook's or a suite's env (a proxy may be real), so :func:`child_env`
  keeps them; but a tmux SERVER's global table hands them to every window, so
  :func:`server_env` (the env a heal-create starts the server from) drops them and
  :func:`scrub_tmux_secrets` unsets them from the global table. An operator who really
  runs behind a proxy names it in ``CHELA_CHILD_ENV_FORWARD``.
"""
from __future__ import annotations

import os
import socket
import subprocess
import urllib.parse
from collections.abc import Callable, Mapping

# Node's own IPC / cluster markers: a Node process that sees these believes it is a forked
# IPC child (or a cluster worker) and wires itself to a channel that is not its own.
NODE_IPC_ENV_VARS = ("NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE", "NODE_UNIQUE_ID")

# pm2's per-process bookkeeping. Exact names plus prefixes; PM2_HOME is the one exception.
_PM2_EXACT = ("NODE_APP_INSTANCE",)
_PM2_PREFIXES = ("pm_", "PM2_")
_PM2_KEEP = frozenset({"PM2_HOME"})


# 🔐 CMX-425. Named outright, whatever shape the pattern below would give it.
SECRET_ENV_VARS = ("LINEAR_API_KEY",)
_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")
_SECRET_SUBSTRINGS = ("PASSWORD",)
# Secret-shaped, but a child needs it: Claude Code's own auth, and the GitHub CLI's.
FORWARDED_SECRET_VARS = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
    "GH_TOKEN", "GITHUB_TOKEN",
})
FORWARD_ENV = "CHELA_CHILD_ENV_FORWARD"


# 🧯 CMX-21. Set by a running Claude Code session for its own children; inherited by a
# process that is NOT its child, they switch that process's transcripts off.
SESSION_MARKER_VARS = (
    "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID", "AI_AGENT",
)
# Kept out of a tmux SERVER's global env (every window inherits it), not out of a hook's.
PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
                  "http_proxy", "https_proxy", "all_proxy", "ftp_proxy")
_SERVER_HAZARD_EXACT = (*PROXY_ENV_VARS, "TMUX_TMPDIR", "H")
_SERVER_HAZARD_PREFIXES = ("PERF_",)


def forwarded_names(src: Mapping[str, str] | None = None) -> frozenset[str]:
    """:data:`FORWARDED_SECRET_VARS` plus the operator's ``CHELA_CHILD_ENV_FORWARD`` names."""
    src = os.environ if src is None else src
    extra = (src.get(FORWARD_ENV) or "").replace(",", " ").split()
    return FORWARDED_SECRET_VARS | frozenset(extra)


def is_secret(key: str, forward: frozenset[str] = FORWARDED_SECRET_VARS) -> bool:
    """True for a credential :func:`child_env` keeps out of a child (unless in ``forward``)."""
    if key in forward:
        return False
    if key in SECRET_ENV_VARS:
        return True
    up = key.upper()
    return up.endswith(_SECRET_SUFFIXES) or any(s in up for s in _SECRET_SUBSTRINGS)


def is_session_marker(key: str) -> bool:
    """True for a Claude Code session marker (:data:`SESSION_MARKER_VARS`)."""
    return key in SESSION_MARKER_VARS


def is_server_hazard(key: str) -> bool:
    """True for a var a tmux server must not hand every window: a proxy, ``TMUX_TMPDIR``,
    the perf harness's ``PERF_*``/``H`` (CMX-21)."""
    return key in _SERVER_HAZARD_EXACT or key.startswith(_SERVER_HAZARD_PREFIXES)


def tmux_secret_names(show_environment_output: str,
                      forward: frozenset[str] | None = None) -> list[str]:
    """Secret names SET in ``tmux show-environment -g`` output (``KEY=value`` lines; a
    ``-KEY`` line is already unset). Values are never returned or logged."""
    forward = forwarded_names() if forward is None else forward
    names = []
    for line in (show_environment_output or "").splitlines():
        key, eq, _ = line.partition("=")
        if eq and key and not key.startswith("-") and is_secret(key, forward):
            names.append(key)
    return names


def tmux_scrub_names(show_environment_output: str,
                     forward: frozenset[str] | None = None) -> list[str]:
    """Every name SET in ``tmux show-environment -g`` output that no window should inherit:
    the secrets (:func:`tmux_secret_names`), pm2/Node leaks, Claude session markers and the
    server hazards (CMX-21). A name in ``forward`` is kept. Values are never returned."""
    forward = forwarded_names() if forward is None else forward
    names = []
    for line in (show_environment_output or "").splitlines():
        key, eq, _ = line.partition("=")
        if not eq or not key or key.startswith("-"):
            continue
        if is_leaked(key) or is_secret(key, forward) or (key not in forward and (
                is_session_marker(key) or is_server_hazard(key))):
            names.append(key)
    return names


def scrub_tmux_secrets() -> list[str]:
    """``tmux set-environment -gu`` every name :func:`tmux_scrub_names` flags in the tmux
    server's GLOBAL environment — the table every new window inherits — before a launch
    path opens one: secrets (CMX-425), and since CMX-21 the session markers, proxies and
    ``TMUX_TMPDIR`` a polluted server was born with. Best-effort (no server, no tmux:
    nothing to scrub). Returns the names unset."""
    try:
        out = subprocess.run(["tmux", "show-environment", "-g"],
                             capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0 or not isinstance(out.stdout, str):
        return []
    names = tmux_scrub_names(out.stdout)
    for name in names:
        subprocess.run(["tmux", "set-environment", "-gu", name], capture_output=True)
    return names


def is_leaked(key: str) -> bool:
    """True for a var :func:`child_env` drops."""
    if key in NODE_IPC_ENV_VARS or key in _PM2_EXACT:
        return True
    return key.startswith(_PM2_PREFIXES) and key not in _PM2_KEEP


def child_env(extra: Mapping[str, str] | None = None,
              base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A fresh copy of ``base`` (default ``os.environ``) without the leaked vars and the
    secrets (CMX-425), then ``extra``
    laid over it. ``extra`` wins, even over a stripped name — a caller that sets a var
    explicitly means it."""
    src = os.environ if base is None else base
    forward = forwarded_names(src)
    env = {k: v for k, v in src.items()
           if not is_leaked(k) and not is_secret(k, forward)
           and (k in forward or not is_session_marker(k))}
    if extra:
        env.update(extra)
    return env


def server_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The env a tmux SERVER is started from (CMX-21): :func:`child_env` minus the server
    hazards, because the server copies it into the global table every window inherits.

    ``TMUX_TMPDIR`` is the one hazard kept HERE: the tmux client reads it to pick the
    socket, and dropping it would aim the create at a different server. The caller unsets
    it from the new server's global table afterwards (:func:`scrub_tmux_secrets`)."""
    src = os.environ if base is None else base
    forward = forwarded_names(src)
    return {k: v for k, v in child_env(base=src).items()
            if k in forward or k == "TMUX_TMPDIR" or not is_server_hazard(k)}


def tmux_tmpdir_missing(env: Mapping[str, str] | None = None) -> str | None:
    """The ``TMUX_TMPDIR`` value when it is set but its directory does not exist, else None.

    That is exactly the orphaned supervisor's state on 2026-10-06 (CMX-21): tmux silently
    falls back to the DEFAULT socket when that dir is missing, so a "heal" from here lands
    on the live server, not the private one it was started for."""
    src = os.environ if env is None else env
    raw = src.get("TMUX_TMPDIR")
    if raw and not os.path.isdir(raw):
        return raw
    return None


# --- 🧯 CMX-21: the health check over a tmux server's global env -------------------------

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})
# Port 9 is "discard": a proxy aimed there is a deliberate egress kill switch (the perf
# harness's), never a real proxy.
_DEAD_PORTS = frozenset({9})
POLLUTION_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def tmux_env_pollution(env: Mapping[str, str],
                       port_open: Callable[[str, int], bool] = _port_open) -> dict[str, str]:
    """``{name: why}`` for each entry in a tmux server's GLOBAL env that breaks every window
    born from it — the 2026-10-06 state: a proxy aimed at a dead localhost port, a Claude
    session marker, a ``TMUX_TMPDIR`` whose directory is gone. Empty when clean."""
    found: dict[str, str] = {}
    for name in PROXY_ENV_VARS:
        raw = env.get(name)
        if not raw:
            continue
        parsed = urllib.parse.urlparse(raw if "://" in raw else f"http://{raw}")
        try:
            host, port = parsed.hostname, parsed.port
        except ValueError:
            continue
        if host not in _LOCAL_HOSTS:
            continue
        port = port or 80
        if port in _DEAD_PORTS or not port_open(host, port):
            found[name] = f"a proxy at {host}:{port}, where nothing listens"
    for name in POLLUTION_MARKERS:
        if name in env:
            found[name] = "a Claude session marker — transcripts are off in every window"
    missing = tmux_tmpdir_missing(env)
    if missing:
        found["TMUX_TMPDIR"] = f"{missing} does not exist — tmux falls back to the default socket"
    return found
