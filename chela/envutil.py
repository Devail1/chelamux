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
"""
from __future__ import annotations

import os
from collections.abc import Mapping

# Node's own IPC / cluster markers: a Node process that sees these believes it is a forked
# IPC child (or a cluster worker) and wires itself to a channel that is not its own.
NODE_IPC_ENV_VARS = ("NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE", "NODE_UNIQUE_ID")

# pm2's per-process bookkeeping. Exact names plus prefixes; PM2_HOME is the one exception.
_PM2_EXACT = ("NODE_APP_INSTANCE",)
_PM2_PREFIXES = ("pm_", "PM2_")
_PM2_KEEP = frozenset({"PM2_HOME"})


def is_leaked(key: str) -> bool:
    """True for a var :func:`child_env` drops."""
    if key in NODE_IPC_ENV_VARS or key in _PM2_EXACT:
        return True
    return key.startswith(_PM2_PREFIXES) and key not in _PM2_KEEP


def child_env(extra: Mapping[str, str] | None = None,
              base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A fresh copy of ``base`` (default ``os.environ``) without the leaked vars, then ``extra``
    laid over it. ``extra`` wins, even over a stripped name — a caller that sets a var
    explicitly means it."""
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items() if not is_leaked(k)}
    if extra:
        env.update(extra)
    return env
