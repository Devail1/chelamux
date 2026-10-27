"""🧪 CMX-282 — the known-flake quarantine registry, ``tests/flaky.toml``.

A CI red whose EVERY failed test is a registered, unexpired entry whose tracker issue is
still open is re-run up to :data:`FLAKE_RERUNS` more times (after CMX-255's one), and if it
stays red only on those tests the run is NOT sent back: it proceeds to the judge with
``ci_known_flake`` recorded. The flaky test is never skipped — it still runs and reports;
only the dispatcher's reaction to its red changes. Anything else is a real red.

The registry is read from the ORCHESTRATOR's checkout (``wf.path.parent``), never the PR's
head: a PR cannot quarantine its own red. A known-flake head goes to the judge on a red CI,
but ``chela merge`` still needs green: someone re-runs CI (or overrides) before it merges.

The registry must not become a hiding place: every entry names its issue and an expiry, and
:func:`problems` (asserted by ``tests/test_ci_known_flake.py``, so by CI) refuses one that
is malformed, expired, or whose issue is Done/Canceled or missing.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import tomllib
from pathlib import Path

log = logging.getLogger(__name__)

REGISTRY = Path("tests") / "flaky.toml"
FLAKE_RERUNS = 2
_ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)")
# `gh run view --log-failed` prefixes each line `<job>\t<step>\t<timestamp> `.
_LINE_RE = re.compile(r"^(?:([^\t]*\t[^\t]*)\t﻿?(?:\d{4}-\d\d-\d\dT[\d:.]+Z ?)?)?(.*)$")
# 🔎 CMX-351: the only lines that NAME a failing test. pytest's short summary
# (`FAILED <nodeid> - why`; `ERROR <file>.py` for a collection error) and Vitest's failure
# header (`FAIL  <spec> > <suite> > <test>`, also inside a pytest `E` block when a JS suite
# runs under pytest). Never a line that merely says "failed" (`Test Files  1 failed (1)`, a
# test's own stderr): a flake has to be countable by name.
_PYTEST_ID_RE = re.compile(r"^(?:FAILED|ERROR) (\S+\.py(?:::.+?)?)(?: - .*)?$")
_VITEST_ID_RE = re.compile(
    r"^(?:E\s+)?FAIL\s+(\S+\.(?:test|spec)\.[cm]?[jt]sx?(?: > .+?)?)(?:\s+\[ .+ \])?$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def load(repo: Path) -> list[dict]:
    """The registry's entries, ``[]`` when there is none. Raises ``ValueError`` (a
    ``TOMLDecodeError`` included) on a file that is not an array of tables."""
    path = Path(repo) / REGISTRY
    if not path.exists():
        return []
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("flaky", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError(f"{path}: `flaky` must be an array of tables")
    return entries


def _expires(entry: dict) -> dt.date | None:
    v = entry.get("expires")
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v
    try:
        return dt.date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def problems(entries: list[dict], today: dt.date,
             states: dict[str, str | None] | None = None) -> list[str]:
    """Why each bad entry is bad. ``states`` maps issue → its terminal state (None while
    open); an issue absent from it was not found. ``states=None`` skips the tracker half."""
    out = []
    for e in entries:
        test, issue, exp = e.get("test"), e.get("issue"), _expires(e)
        if not isinstance(test, str) or "::" not in test:
            out.append(f"{test!r}: `test` must be a pytest node id")
            continue
        if not isinstance(issue, str) or not _ISSUE_RE.match(issue):
            out.append(f"{test}: no tracker issue")
        elif states is not None and issue not in states:
            out.append(f"{test}: {issue} not found in the tracker")
        elif states is not None and states[issue]:
            out.append(f"{test}: {issue} is {states[issue]}")
        if exp is None:
            out.append(f"{test}: `expires` must be YYYY-MM-DD")
        elif exp < today:
            out.append(f"{test}: expired {exp}")
    return out


def failed_tests(log_text: str) -> list[str] | None:
    """The pytest node ids a failed-jobs log names, or ``None`` when ANY failed step names
    none, or any FAILED/ERROR line is not a node id (a collection error, a ruff error, a
    crash, an unfetched or truncated log): never a known flake."""
    if not log_text or log_text.startswith("… (log truncated"):
        return None
    steps: dict[str, set[str]] = {}
    for raw in log_text.splitlines():
        if not raw.strip():
            continue
        step, line = _LINE_RE.match(raw).groups()
        ids = steps.setdefault(step or "", set())
        m = _FAILED_RE.match(line.strip())
        if m and "::" not in m.group(1):
            return None
        if m:
            ids.add(m.group(1))
    if not steps or not all(steps.values()):
        return None
    return sorted(set().union(*steps.values()))


def failing_test(log_text: str) -> str | None:
    """🔎 CMX-351. The id of the first failing test a CI log names, or None when no line
    names one. A Vitest id wins over pytest's: when a JS suite runs under pytest, pytest's
    node id is the wrapper (`test_js_suite[<spec>]`) and Vitest's names the test inside it."""
    pytest_id = None
    for raw in (log_text or "").splitlines():
        line = _ANSI_RE.sub("", _LINE_RE.match(raw).group(2)).strip()
        if m := _VITEST_ID_RE.match(line):
            return m.group(1)
        if pytest_id is None and (m := _PYTEST_ID_RE.match(line)):
            pytest_id = m.group(1)
    return pytest_id


def is_test_id(value: object) -> bool:
    """True for an id :func:`failing_test` can return — what a flake is counted by."""
    return isinstance(value, str) and failing_test(f"FAILED {value}\nFAIL {value}") == value


def file_of(test_id: str) -> str:
    """The file half of a pytest node id or a Vitest id."""
    return re.split(r"::| > ", test_id, maxsplit=1)[0]


def known(ids: list[str] | None, entries: list[dict], today: dt.date) -> list[dict] | None:
    """``[{test, issue}]`` — the valid, unexpired entry behind EACH id — or None when any id
    has none. A parametrized id ``t[x]`` is covered by an entry for ``t``."""
    hits = []
    for tid in ids or ():
        entry = next((e for e in entries if not problems([e], today)
                      and (tid == e["test"] or tid.startswith(e["test"] + "["))), None)
        if entry is None:
            return None
        hits.append({"test": tid, "issue": entry["issue"]})
    return hits or None


def issue_states(wf, issues: list[str]) -> dict[str, str | None] | None:
    """issue → its terminal state (None while open) per ``wf``'s tracker; None unreadable."""
    from chela.sources import get_source
    try:
        tasks = get_source(wf).fetch_by_ids(sorted(set(issues)))
    except Exception:  # noqa: BLE001 — an unreadable tracker is not an open issue
        log.warning("flaky: could not read %s from the tracker", issues, exc_info=True)
        return None
    return None if tasks is None else {t.id: t.terminal_state for t in tasks}


def classify(wf, log_text: str, today: dt.date | None = None) -> list[dict] | None:
    """The registry entry behind EVERY failed test of this red, or None — one unregistered,
    expired or closed-issue failure, or an unreadable registry or tracker, is a real red."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    try:
        entries = load(wf.path.parent)
    except (OSError, ValueError) as e:
        log.warning("flaky: %s is unreadable — every red is real: %s", REGISTRY, e)
        return None
    hits = known(failed_tests(log_text), entries, today)
    if not hits:
        return None
    states = issue_states(wf, [h["issue"] for h in hits])
    if states is None or any(states.get(h["issue"], "missing") for h in hits):
        return None
    return hits
