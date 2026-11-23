"""CMX-477: install the chela-measure mod, and say which producer feeds the usage bars.

The mod (``chela/mods/chela-measure``, shipped in the wheel) writes the figures Claude
Code's ``session.measure`` hook pushes (context fill, the 5h/7d plan limits, cost) to
``$CHELA_DIR/context/mod/<session_id>.json``, in the statusLine payload's shape and tagged
``"source": "mod"``. Unlike the statusLine it occupies nothing the person sees, so it never
competes with their own (or their organization's) status line.

**Install without touching settings.json.** Claude Code (>= 2.1.301) auto-loads a plugin
folder under ``~/.claude/skills/<name>`` (the ``@skills-dir`` plugins), so
``chela install-mod`` COPIES the packaged mod there, never a link into the install: a
``uvx``/``pipx`` install lives in a cache that can be pruned. The copy gets a
``chela.json`` naming this process's ``CHELA_DIR``, for sessions that do not export one.
See ``docs/spikes/SESSION_MEASURE.md`` for the payload, when it fires, and what an
organization's managed settings can block.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

MOD_NAME = "chela-measure"
CONFIG_FILE = "chela.json"          # the name measure.ts reads (CONFIG_FILE there)
SOURCES = ("statusline", "mod")


def packaged_dir() -> Path:
    """The mod as the package ships it."""
    return Path(__file__).resolve().parent / "mods" / MOD_NAME


def claude_config_dir() -> Path:
    """``$CLAUDE_CONFIG_DIR``, else ``~/.claude``: where Claude Code reads user settings."""
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def install_dir() -> Path:
    return claude_config_dir() / "skills" / MOD_NAME


def is_ours(dest: Path) -> bool:
    """True when ``dest`` holds a plugin named chela-measure (so replacing it is safe)."""
    try:
        manifest = json.loads((dest / ".claude-plugin" / "plugin.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == MOD_NAME


def _ignore(_dir: str, names: list[str]) -> set[str]:
    # The mod's own tests and their fixture stay in the package; a session loads none of them.
    return {n for n in names if n.endswith(".test.ts") or n.endswith("-cases.ts")
            or n == "__pycache__"}


def install(dest: Path, chela_dir: Path, src: Path | None = None) -> Path:
    """Copy the mod to ``dest`` and record ``chela_dir`` in its ``chela.json``.

    Refuses (``FileExistsError``) to replace a folder that is not this mod. A previous copy
    of this mod is replaced whole, through a sibling temp folder, so a session never loads
    half of one.
    """
    src = src or packaged_dir()
    if not (src / ".claude-plugin" / "plugin.json").is_file():
        raise FileNotFoundError(f"the packaged mod is missing at {src}")
    if dest.exists() and not is_ours(dest):
        raise FileExistsError(f"{dest} exists and is not the {MOD_NAME} mod; not replacing it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{MOD_NAME}.{os.getpid()}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(src, tmp, ignore=_ignore)
        (tmp / CONFIG_FILE).write_text(json.dumps({"chela_dir": str(chela_dir)}) + "\n")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)     # a failed copy leaves no temp folder
    return dest


def remove(dest: Path) -> bool:
    """Remove an installed copy. False when there is none; refuses a folder not ours."""
    if not dest.exists():
        return False
    if not is_ours(dest):
        raise FileExistsError(f"{dest} is not the {MOD_NAME} mod; not removing it")
    shutil.rmtree(dest)
    return True


def session_sources(cache_dir: Path, now: float | None = None,
                    stale_s: float | None = None) -> dict[str, dict[str, float]]:
    """``{session_id: {source: age_s}}`` for every session with a FRESH snapshot.

    ``statusline`` is ``by-session/<sid>.json``, ``mod`` is ``mod/<sid>.json``. A file
    older than ``stale_s`` (the context bar's own staleness) is not a feed.
    """
    from chela import config, context
    now = time.time() if now is None else now
    stale_s = config.cache_stale_seconds() if stale_s is None else stale_s
    out: dict[str, dict[str, float]] = {}
    for source, sub in (("statusline", context.SESSION_CACHE_SUBDIR),
                        ("mod", context.MOD_CACHE_SUBDIR)):
        for f in (cache_dir / sub).glob("*.json"):
            try:
                age = now - f.stat().st_mtime
            except OSError:
                continue
            if age <= stale_s:
                out.setdefault(f.stem, {})[source] = age
    return out


def feeding(ages: dict[str, float]) -> str:
    """The producer whose snapshot a reader serves: the NEWER one (smaller age)."""
    return min(ages, key=lambda s: (ages[s], SOURCES.index(s)))


def installed_state(dest: Path | None = None) -> dict | None:
    """What the installed copy says about itself, or None when no chela-measure is there.

    ``chela_dir`` is the one its ``chela.json`` records (None when unreadable), ``version``
    its manifest's.
    """
    dest = dest or install_dir()
    if not is_ours(dest):
        return None
    version = json.loads((dest / ".claude-plugin" / "plugin.json").read_text()).get("version")
    try:
        recorded = json.loads((dest / CONFIG_FILE).read_text()).get("chela_dir")
    except (OSError, ValueError, AttributeError):
        recorded = None
    return {"dir": dest, "version": version,
            "chela_dir": recorded if isinstance(recorded, str) else None}


def packaged_version() -> str | None:
    return json.loads((packaged_dir() / ".claude-plugin" / "plugin.json").read_text()).get("version")


def statusline_wired(settings: Path | None = None) -> bool:
    """True when user settings run chela's statusLine hook (``cache-statusline.sh``)."""
    settings = settings or claude_config_dir() / "settings.json"
    try:
        data = json.loads(settings.read_text())
    except (OSError, ValueError):
        return False
    sl = data.get("statusLine") if isinstance(data, dict) else None
    cmd = sl.get("command") if isinstance(sl, dict) else None
    return isinstance(cmd, str) and "cache-statusline.sh" in cmd


def _limit_text(entry: dict) -> str:
    if entry.get("used_pct") is None:
        return "unknown"
    return f"{entry['used_pct']:g}% ({entry.get('source')})"


def report(chela_dir: Path, state: dict) -> list:
    """``chela doctor``'s usage.sources lines, from :func:`read_state`'s ``state``."""
    from chela.runtime_truth import OK, WARN, Finding
    out = []
    mod = state["mod"]
    if mod is None:
        out.append(Finding(OK, f"{MOD_NAME} mod: not installed",
                           "`chela install-mod` feeds the 5h/7d limits, context and cost from "
                           "Claude Code's session.measure hook, with no statusLine."))
    else:
        out.append(Finding(OK, f"{MOD_NAME} mod {mod['version']} installed at {mod['dir']}"))
        if mod["chela_dir"] != str(chela_dir):
            out.append(Finding(
                WARN, f"{MOD_NAME} writes to {mod['chela_dir'] or '(unrecorded)'}, "
                      f"not this CHELA_DIR {chela_dir}",
                "A session without $CHELA_DIR feeds another chela. Re-run `chela install-mod`."))
        if mod["version"] != state["packaged_version"]:
            out.append(Finding(
                WARN, f"{MOD_NAME} installed is {mod['version']}, this chela ships "
                      f"{state['packaged_version']}", "Re-run `chela install-mod`."))
    out.append(Finding(OK, "statusLine hook: " + ("wired in user settings" if state["statusline"]
                                                    else "not in user settings")))
    lim = state["limits"]
    out.append(Finding(OK, f"limits: 5h {_limit_text(lim['five_hour'])} · "
                             f"7d {_limit_text(lim['seven_day'])}",
                       "; ".join(f"{k}: {lim[k].get('reason')}" for k in ("five_hour", "seven_day")
                                 if lim[k].get("used_pct") is None)))
    sessions = state["sessions"]
    if sessions:
        rows = [f"{sid[:8]}: {feeding(ages)}" + (" (both fresh)" if len(ages) > 1 else "")
                for sid, ages in sorted(sessions.items())]
        n_mod = sum(1 for ages in sessions.values() if feeding(ages) == "mod")
        out.append(Finding(OK, f"{len(sessions)} session(s) fed: {n_mod} by the mod, "
                                 f"{len(sessions) - n_mod} by the statusLine",
                           "\n".join(f"    - {r}" for r in rows[:20]).lstrip()))
    else:
        out.append(Finding(OK, "no session has a fresh statusLine or mod snapshot"))
    return out


def read_state() -> dict:
    """Everything :func:`report` reads, from the owners (files, settings, the cache dir)."""
    from chela import config, usage
    cache_dir = config.CHELA_DIR / "context"
    return {"mod": installed_state(), "packaged_version": packaged_version(),
            "statusline": statusline_wired(),
            "limits": usage.limits(cache_dir, history=False),
            "sessions": session_sources(cache_dir)}
