"""Launch settings — the extra CLI flags and allowlisted env a Claude session ran with (CMX-372).

**The gap.** :func:`chela.restore.resume` brings a session back as
``claude [--remote-control <name>] --resume <sid>`` and nothing else. A session that ran with
``--channels plugin:discord@claude-plugins-official``, a ``DISCORD_STATE_DIR`` naming its own
``access.json``, and ``~/.bun/bin`` on its PATH came back deaf to Discord after every reboot,
until someone relaunched it by hand.

**Record.** :func:`record_windows` runs on the telegram daemon's reconcile tick, right after
:func:`chela.roster.record`, over the windows that snapshot just identified (session id +
claude pid). For each live claude process it reads the process's own argv and environ and
keeps only:

* the flags in :data:`FLAGS` (each with its arity), never a positional — a prompt typed on
  the command line is not replayed, and an unknown flag is skipped;
* the env names in :data:`ENV_ALLOW` plus the operator's ``CHELA_RESTORE_ENV`` names — and
  even then never a secret-shaped name (:func:`chela.envutil.is_secret`, with no forward
  exemptions). ⛔ The whole environment is never captured or stored;
* the PATH entries the process had that chela's own PATH does not (``path_prefix``) —
  absolute directories only, and only alongside a flag or an env var: a pane's PATH differs
  from the daemon's for every session (shell rc files), so PATH alone is not a launch setting.

Keyed by session id, because a session id is what restore resumes. One writer
(the reconcile tick), its own file ``launch-settings.json`` — the same one-writer-per-file
rule :mod:`chela.roster` and :mod:`chela.sessionids` follow. A live process is the truth: a
session relaunched WITHOUT extras clears its record, so a removed flag is not brought back.
Windows a tick could not identify (no pid, no session) are left alone.

Because chela observes the process rather than its own launch call, this covers both a
window chela launched and one it adopted (a session a human, or a boot script, started).

**Replay.** :func:`replay` turns a record into ``(flags, env, path_prefix, notes)`` for
:func:`chela.spawn.spawn_window`. A channel plugin that is no longer installed drops the
replay entirely (restore falls back to a plain ``--resume``, and says why); a PATH entry
that is no longer a directory is dropped on its own. :func:`chela.restore.resume` also falls
back to a plain ``--resume`` when the replayed launch does not come up alive — a flag Claude
Code no longer knows makes it exit, and that must not keep the session down.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from chela import envutil
from chela.config import CHELA_DIR

log = logging.getLogger(__name__)

_STORE = CHELA_DIR / "launch-settings.json"
_MAX_ENTRIES = 500

# Flags worth replaying, with their arity: 0 = a switch, 1 = one value, "+" = one or more
# values (Commander's variadic `<values...>`). Chela's own flags (`--resume`, `--session-id`,
# `--remote-control`) are not here: restore and spawn add those themselves. `--settings` and
# `--mcp-config` are left out on purpose — they can carry inline JSON with credentials in it.
FLAGS: dict[str, int | str] = {
    "--channels": "+",
    "--model": 1,
    "--permission-mode": 1,
    "--add-dir": "+",
    "--plugin-dir": 1,
    "--agent": 1,
    "--dangerously-skip-permissions": 0,
    "--allow-dangerously-skip-permissions": 0,
}

# Env names a session's launch may carry that restore replays. Extend with
# `CHELA_RESTORE_ENV="NAME1 NAME2"`; a secret-shaped name is refused either way.
ENV_ALLOW = frozenset({"DISCORD_STATE_DIR"})
ENV_EXTRA = "CHELA_RESTORE_ENV"

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PLUGIN_CHANNEL = re.compile(r"plugin:([^@\s]+@[^@\s]+)\Z")


def allowed_env(extra: str | None = None) -> frozenset[str]:
    """:data:`ENV_ALLOW` plus ``CHELA_RESTORE_ENV``'s names, minus anything secret-shaped."""
    raw = os.environ.get("CHELA_RESTORE_ENV", "") if extra is None else extra
    names = ENV_ALLOW | frozenset(raw.replace(",", " ").split())
    return frozenset(n for n in names
                     if _ENV_NAME.match(n) and not envutil.is_secret(n, frozenset()))


def _claude_args(argv: list[str]) -> list[str]:
    """The arguments after the ``claude`` executable (or ``node …/claude…/cli.js``)."""
    for i, arg in enumerate(argv):
        if arg.startswith("-"):
            continue
        if "claude" in os.path.basename(arg) or ("claude" in arg and arg.endswith(".js")):
            return argv[i + 1:]
    return []


def parse_flags(args: list[str]) -> list[str]:
    """The :data:`FLAGS` in ``args``, normalized to ``--flag value…`` tokens, in order."""
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        name, eq, inline = arg.partition("=")
        arity = FLAGS.get(name)
        if arity is None:
            continue
        if arity == 0:
            if not eq:
                out.append(name)
            continue
        values = [inline] if eq else []
        while i < len(args) and not args[i].startswith("-") and (
                arity == "+" or len(values) < int(arity)):
            values.append(args[i])
            i += 1
        values = [v for v in values if v and "\n" not in v]
        if values:
            out += [name, *values]
    return out


def capture(argv: list[str], environ: dict[str, str], base_path: str | None = None,
            extra_env: str | None = None) -> dict:
    """One process's replayable launch settings — ``{}`` when it has none."""
    flags = parse_flags(_claude_args(argv))
    names = allowed_env(extra_env)
    env = {k: v for k, v in environ.items()
           if k in names and v and "\n" not in v}
    base = set((os.environ.get("PATH", "") if base_path is None else base_path).split(":"))
    path_prefix: list[str] = []
    for p in (environ.get("PATH") or "").split(":"):
        if p and os.path.isabs(p) and p not in base and p not in path_prefix and "\n" not in p:
            path_prefix.append(p)
    out: dict = {}
    if flags:
        out["flags"] = flags
    if env:
        out["env"] = env
    if path_prefix and (flags or env):
        out["path_prefix"] = path_prefix
    return out


def read_environ(pid: int) -> dict[str, str]:
    """``/proc/<pid>/environ`` as a dict; ``{}`` when it cannot be read (no /proc, gone)."""
    from chela import sessions
    try:
        raw = (sessions.PROC / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for item in raw.decode("utf-8", "replace").split("\0"):
        k, eq, v = item.partition("=")
        if eq and k:
            out[k] = v
    return out


def _load(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or _STORE).read_text())
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        data = {}
    return data


def _save(data: dict, path: Path | None = None) -> None:
    store = path or _STORE
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, store)


def record_windows(windows: dict, *, path: Path | None = None,
                   argv_for=None, environ_for=None, now: float | None = None) -> dict:
    """Record the launch settings of every identified window in ``windows``.

    ``windows`` is :func:`chela.roster.record`'s ``rec["windows"]``: ``{wid: {session_id,
    pid, …}}``. ``argv_for``/``environ_for`` are DI seams for a pid's argv and environ.
    Writes only when something changed. Returns the store.
    """
    if argv_for is None:
        from chela import sessions
        argv_for = sessions._cmdline_argv
    environ_for = environ_for or read_environ
    data = _load(path)
    changed = False
    stamp = time.time() if now is None else now
    for w in (windows or {}).values():
        sid, pid = w.get("session_id"), w.get("pid")
        if not sid or not pid:
            continue
        argv = argv_for(pid)
        if not argv:
            continue
        settings = capture(argv, environ_for(pid))
        prev = data.get(sid)
        if not settings:
            if prev is not None:
                del data[sid]
                changed = True
            continue
        if {k: v for k, v in (prev or {}).items() if k != "seen"} != settings:
            data[sid] = {**settings, "seen": stamp}
            changed = True
    if len(data) > _MAX_ENTRIES:
        for sid, _ in sorted(data.items(), key=lambda kv: kv[1].get("seen", 0))[
                :len(data) - _MAX_ENTRIES]:
            del data[sid]
        changed = True
    if changed:
        _save(data, path)
    return data


def for_session(session_id: str | None, *, path: Path | None = None) -> dict:
    """The recorded settings of ``session_id`` — ``{}`` when none (or unreadable)."""
    if not session_id:
        return {}
    rec = _load(path).get(session_id)
    return rec if isinstance(rec, dict) else {}


def installed_plugins(path: Path | None = None) -> set[str] | None:
    """``name@marketplace`` of every installed Claude Code plugin; ``None`` if unreadable."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        data = json.loads((path or base / "plugins" / "installed_plugins.json").read_text())
        plugins = data.get("plugins")
        if not isinstance(plugins, dict):
            return None
        return set(plugins)
    except (OSError, ValueError, AttributeError):
        return None


def _field(settings: dict, key: str, kind: type):
    value = settings.get(key)
    return value if isinstance(value, kind) else kind()


def replay(settings: dict, *, plugins=None, isdir=os.path.isdir
           ) -> tuple[list[str], dict[str, str], list[str], list[str]]:
    """``(flags, env, path_prefix, notes)`` to relaunch with, from a recorded ``settings``.

    Re-filters through the CURRENT allowlists (a record from an older chela is not trusted
    blindly). A ``--channels plugin:<name>@<marketplace>`` whose plugin is not installed
    drops the whole replay — ``notes`` says why — because the session must still come back.
    An unreadable plugin list is unknown, not "missing": the launch goes ahead, and the
    liveness fallback in :func:`chela.restore.resume` covers a launch that dies.
    """
    raw_flags = _field(settings, "flags", list)
    flags = parse_flags([str(f) for f in raw_flags])
    names = allowed_env()
    raw_env = _field(settings, "env", dict)
    env = {k: str(v) for k, v in raw_env.items()
           if k in names and v and "\n" not in str(v)}
    notes: list[str] = []
    dropped = sorted(set(raw_env) - set(env))
    if dropped:
        notes.append(f"env not replayed (not allowlisted): {', '.join(dropped)}")
    raw_path = _field(settings, "path_prefix", list)
    path_prefix = []
    for p in raw_path:
        if isinstance(p, str) and os.path.isabs(p) and ":" not in p and isdir(p):
            path_prefix.append(p)
        else:
            notes.append(f"PATH entry {p!r} is no longer a directory — left out")
    wanted = []
    for i, f in enumerate(flags):
        if f == "--channels":
            for v in flags[i + 1:]:
                if v.startswith("-"):
                    break
                m = _PLUGIN_CHANNEL.match(v)
                if m:
                    wanted.append(m.group(1))
    if wanted:
        have = installed_plugins() if plugins is None else plugins
        missing = [p for p in wanted if have is not None and p not in have]
        if missing:
            return [], {}, [], [f"plugin {', '.join(missing)} is not installed — resuming "
                                "without the recorded launch settings"]
    return flags, env, path_prefix, notes
