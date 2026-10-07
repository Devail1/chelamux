"""CMX-16: ``collected_js_suites`` walks the repo ONCE per read, not once per output line.

The CMX-15 profile found the walk inside the comprehension: one ``os.walk`` of the whole
checkout per line of ``pytest --collect-only`` output (~5,900 lines) — ~35 s of CPU on
every ``chela doctor``, daemon start and hourly check. These pin the hoist without
shelling out: the collector's stdout is a fixture, the walk is a counted stub.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from chela import runtime_truth

_ON_DISK = ["a/one.test.mjs", "b/two.test.mjs", "tests/never_collected.test.mjs"]

# Many lines, so a per-line walk is unmistakable; two suites reached, one not.
_COLLECT_STDOUT = "\n".join(
    [f"tests/test_x.py::test_{i}" for i in range(200)]
    + ["tests/test_js.py::test_suite[a/one.test.mjs]",
       "tests/test_js.py::test_suite[b/two.test.mjs]",
       "", "205 tests collected in 0.10s"])


def _read(monkeypatch) -> tuple[runtime_truth.Observation, list[int]]:
    calls = [0]

    def walk() -> list[str]:
        calls[0] += 1
        return list(_ON_DISK)

    monkeypatch.setattr(runtime_truth, "_js_suites_on_disk", walk)
    monkeypatch.setattr(
        runtime_truth.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=_COLLECT_STDOUT, stderr=""))
    return runtime_truth.collected_js_suites(Path(".")), calls


def test_the_disk_walk_runs_exactly_once_per_read(monkeypatch):
    _, calls = _read(monkeypatch)
    assert calls[0] == 1, (
        f"_js_suites_on_disk ran {calls[0]} times for one read — the walk is back inside "
        "the per-line loop (CMX-16: ~35 s CPU per `chela doctor`)")


def test_the_hoisted_read_reports_exactly_the_collected_suites(monkeypatch):
    obs, _ = _read(monkeypatch)
    assert obs == runtime_truth.observed({"a/one.test.mjs", "b/two.test.mjs"})
