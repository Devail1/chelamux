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
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping

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


def scrub_tmux_secrets() -> list[str]:
    """``tmux set-environment -gu`` every secret name set in the tmux server's GLOBAL
    environment — the table every new window inherits — before a launch path opens one.
    Best-effort (no server, no tmux: nothing to scrub). Returns the names unset."""
    try:
        out = subprocess.run(["tmux", "show-environment", "-g"],
                             capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0 or not isinstance(out.stdout, str):
        return []
    names = tmux_secret_names(out.stdout)
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
    env = {k: v for k, v in src.items() if not is_leaked(k) and not is_secret(k, forward)}
    if extra:
        env.update(extra)
    return env
