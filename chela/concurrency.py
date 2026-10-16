"""How many agents and judges run at once (CMX-164): the Settings rows behind them.

Both are read per call (every dispatcher tick), never latched at boot, so a change in
Settings applies on the next tick with no file edit and no restart.

- **Agents at once, per workflow.** WORKFLOW.md's ``concurrency.max`` is the default. An
  operator-local override in ``~/.chela/config.json`` (``agents_at_once``, a map keyed by
  the resolved WORKFLOW.md path) wins for THAT workflow only. Lowering it never stops a
  run: the dispatcher only stops claiming until the active count drops under it.
- **Judges at once.** The stored value (key ``judge_max_concurrent``) wins over
  ``CHELA_JUDGE_MAX_CONCURRENT``, which wins over the default ``1``. Settings beats env
  here on purpose: the env var lives in ``chela.env`` and only applies after a restart,
  which is the friction this row removes.
"""
from __future__ import annotations

import os
from pathlib import Path

from chela import event_log, userconfig

MIN, MAX = 1, 8
AGENTS_KEY = "agents_at_once"
JUDGES_KEY = "judge_max_concurrent"
JUDGES_ENV = "CHELA_JUDGE_MAX_CONCURRENT"
JUDGES_DEFAULT = 1
# Peak cgroup memory of one judge's test run (coordinator + xdist workers), as measured in
# docs/RESOURCE_ISOLATION.md. Shown in Settings as N x this, so raising a count is informed.
JUDGE_PEAK_BYTES = int(0.77 * 1024 ** 3)


def parse(raw) -> int | None:
    """An integer in [MIN, MAX], or None. Rejects bools, floats and non-digit strings."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        n = int(raw.strip())
    else:
        return None
    return n if MIN <= n <= MAX else None


def _wf_key(path) -> str:
    return str(Path(path).resolve())


def _overrides() -> dict:
    stored = userconfig.get(AGENTS_KEY)
    return stored if isinstance(stored, dict) else {}


def agent_override(path) -> int | None:
    """The stored override for one workflow, or None (unset or invalid)."""
    return parse(_overrides().get(_wf_key(path)))


def agents_at_once(wf) -> int:
    """The dispatcher's claim limit for ``wf``: its override, else WORKFLOW.md, else 1."""
    override = agent_override(wf.path)
    if override is not None:
        return override
    return wf.get("concurrency", "max", default=1) or 1


def judges_at_once() -> tuple[int, str]:
    """``(value, source)``: stored setting > env var > default. Invalid levels fall through."""
    stored = parse(userconfig.get(JUDGES_KEY))
    if stored is not None:
        return stored, "settings"
    try:
        return max(1, int(os.environ.get(JUDGES_ENV, ""))), "env"
    except ValueError:
        return JUDGES_DEFAULT, "default"


def _record(setting: str, old, new, by: str, workflow: str | None = None) -> None:
    where = f" for {Path(workflow).parent.name}" if workflow else ""
    event_log.append("settings.concurrency", f"{setting}{where}: {old} -> {new} (by {by})",
                     {"setting": setting, "workflow": workflow, "old": old, "new": new,
                      "by": by})


def set_agents(path, raw, by: str) -> str | None:
    """Set (``raw`` in range) or clear (``raw`` None/"") one workflow's override.
    Returns an error message, or None on success."""
    key = _wf_key(path)
    value = None if raw in (None, "") else parse(raw)
    if raw not in (None, "") and value is None:
        return f"agents at once must be an integer from {MIN} to {MAX}"
    overrides = dict(_overrides())
    old = parse(overrides.pop(key, None))
    if value is not None:
        overrides[key] = value
    userconfig.set_(AGENTS_KEY, overrides or None)
    _record("agents_at_once", old, value, by, workflow=key)
    return None


def set_judges(raw, by: str) -> str | None:
    """Set or clear the stored judge count. Returns an error message, or None."""
    value = None if raw in (None, "") else parse(raw)
    if raw not in (None, "") and value is None:
        return f"judges at once must be an integer from {MIN} to {MAX}"
    old = parse(userconfig.get(JUDGES_KEY))
    userconfig.set_(JUDGES_KEY, value)
    _record("judges_at_once", old, value, by)
    return None
