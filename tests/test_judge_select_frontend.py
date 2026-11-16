"""⚡⚖️ CMX-383 — a dashboard FRONTEND mutation runs its related Vitest specs and the root JS
suites that reach it, never the Python suite, and a subset survivor is confirmed on every JS
suite instead of the full suite (whose bundle-staleness check fails on ANY source change).

Real timings and the measured nightly throughput are in the PR body."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import judge, sweep, sweep_nightly
from chela import judge_select as js

ROOT = Path(__file__).resolve().parent.parent
FE = "chela/dashboard/frontend/src"
DRAG = f"{FE}/features/sidebar/lib/drag.ts"
MODEL = f"{FE}/features/sidebar/model.ts"
VITEST_NODE = "tests/test_frontend.py::test_frontend_vitest_suite"
SUITES_PY = "tests/test_js_suites.py"

FILES = {
    MODEL: "export const key = (g: string): string => g;\n",
    DRAG: "import { key } from '../model';\nexport const same = (a: string, b: string) => "
          "key(a) === key(b);\n",
    f"{FE}/features/sidebar/tests/drag.test.tsx": "import { same } from '../lib/drag';\n",
    f"{FE}/features/other/lib/other.ts": "export const o = 1;\n",
    f"{FE}/features/other/tests/other.spec.ts": "import { o } from '../lib/other.js';\n",
    f"{FE}/uikit.test.ts": "const all = import.meta.glob('./**/*.ts', { query: '?raw' });\n",
    f"{FE}/plain.test.ts": "export {};\n",
    f"{FE}/features/sidebar/lib/notaspec.ts": "import { same } from './drag';\n",
    "tests/sidebar_groups.test.mjs":
        "import { same } from '../chela/dashboard/frontend/src/features/sidebar/lib/drag.ts';\n",
    "tests/unrelated.test.mjs": "import x from 'jsdom';\n",
    "tests/test_js_suites.py": "def test_js_suite():\n    pass\n",
    "tests/test_frontend.py": f"# reads {FE}/features/sidebar/model.ts\n",
    "tests/test_by_path.py": "SRC = 'frontend/src/features/sidebar/model.ts'\n",
    "tests/test_by_join.py": 'SRC = ROOT / "features" / "sidebar" / "model.ts"\n',
    "tests/test_by_basename.py": "SRC = 'settings/model.ts'  # another model.ts\n",
    "tests/test_python.py": "import os\n",
}
JS_CASES = ["tests/sidebar_groups.test.mjs", "tests/unrelated.test.mjs"]


def _repo(root: Path, files: dict[str, str] = FILES) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _cases(*, vitest: bool = True, passed: bool = True) -> list[js.Case]:
    cases = [js.Case(f, f"{f}::test_it", True) for f in (
        "tests/test_by_path.py", "tests/test_by_join.py", "tests/test_by_basename.py",
        "tests/test_python.py")]
    if vitest:
        cases.append(js.Case("tests/test_frontend.py", VITEST_NODE, passed))
    cases += [js.Case(SUITES_PY, f"{SUITES_PY}::test_js_suite[{s}]", True, s) for s in JS_CASES]
    cases.append(js.Case(SUITES_PY, f"{SUITES_PY}::test_js_suites_are_discovered", True))
    return cases


def _env(*specs: str) -> dict[str, str]:
    return {"CHELA_FRONTEND_VITEST_FILES": os.pathsep.join(specs)}


# --- the selection --------------------------------------------------------------------------


def test_a_mutated_sidebar_ts_selects_its_specs_and_suites_and_no_python(tmp_path):
    """ACCEPTANCE: the related Vitest specs (via the one pytest case, narrowed by env), the
    root JS suite that imports it, ``--dist load``, and not one Python test file."""
    root = _repo(tmp_path)
    sel = js.Selector(root, _cases()).select(DRAG)

    assert sel.nodes == [VITEST_NODE,
                         f"{SUITES_PY}::test_js_suite[tests/sidebar_groups.test.mjs]"]
    assert sel.env == _env("src/features/sidebar/tests/drag.test.tsx", "src/uikit.test.ts")
    assert sel.args == ["--dist", "load"]
    assert sel.argv == [*sel.nodes, "--dist", "load"]
    assert sel.expected == 2
    assert sel.why == f"2 Vitest spec(s), 1 JS suite(s), 0 Python file(s) observe {DRAG}"
    assert not sel.full and not sel.untested


def test_the_confirm_ring_is_every_js_suite_and_the_whole_vitest_suite(tmp_path):
    root = _repo(tmp_path)
    ring = js.Selector(root, _cases()).select(DRAG).confirm

    assert ring is not None
    assert ring.nodes == [VITEST_NODE, SUITES_PY]
    assert ring.env == {}               # unset: the WHOLE Vitest suite runs
    assert ring.args == ["--dist", "load"]
    assert ring.expected == 4           # the Vitest case + 3 cases in test_js_suites.py
    assert ring.why == (f"every JS suite (1 file(s) + the whole Vitest suite), no Python "
                        f"suite, confirms {DRAG}")


def test_a_python_test_is_selected_only_when_it_names_the_file_by_path(tmp_path):
    """A bare ``model.ts`` names a dozen files; ``sidebar/model.ts`` or a pathlib join names
    this one. ``tests/test_frontend.py`` is the Vitest case's own file: never a whole-file node."""
    root = _repo(tmp_path)
    sel = js.Selector(root, _cases()).select(MODEL)

    assert sel.nodes == [VITEST_NODE,
                         f"{SUITES_PY}::test_js_suite[tests/sidebar_groups.test.mjs]",
                         "tests/test_by_join.py", "tests/test_by_path.py"]
    assert sel.confirm.nodes == [VITEST_NODE, SUITES_PY,
                                 "tests/test_by_join.py", "tests/test_by_path.py"]


@pytest.mark.parametrize("text,want", [
    ("x = 'sidebar/model.ts'", True),
    ("x = 'frontend/src/features/sidebar/model.ts'", True),
    ('ROOT / "sidebar" / "model.ts"', True),
    ("ROOT / 'sidebar'/'model.ts'", True),
    ("x = 'model.ts'", False),
    ("x = 'settings/model.ts'", False),
    ("x = 'notsidebar/model.ts'", False),
    ("x = 'sidebar/model.tsx'", False),
    ("x = 'sidebar/model_ts'", False),
    ('ROOT / "settings" / "model.ts"', False),
])
def test_names_path(text, want):
    assert js.names_path(text, MODEL) is want


def test_names_path_of_a_top_level_file_is_the_whole_path():
    assert js.names_path("open('x.ts')", "x.ts") is True
    assert js.names_path("open('y.ts')", "x.ts") is False


def test_related_specs_are_the_importers_itself_and_every_glob_reader(tmp_path):
    root = _repo(tmp_path)
    s = js.Selector(root, _cases())

    assert s.frontend_specs() == [f"{FE}/features/other/tests/other.spec.ts",
                                  f"{FE}/features/sidebar/tests/drag.test.tsx",
                                  f"{FE}/plain.test.ts", f"{FE}/uikit.test.ts"]
    assert s.related_specs(f"{FE}/features/other/lib/other.ts") == [
        f"{FE}/features/other/tests/other.spec.ts", f"{FE}/uikit.test.ts"]
    assert s.related_specs(f"{FE}/plain.test.ts") == [f"{FE}/plain.test.ts",
                                                       f"{FE}/uikit.test.ts"]


def test_a_spec_under_node_modules_is_not_a_frontend_spec(tmp_path):
    root = _repo(tmp_path, {**FILES, f"{FE}/node_modules/pkg/x.test.ts": "export {};\n"})
    assert f"{FE}/node_modules/pkg/x.test.ts" not in js.Selector(root, _cases()).frontend_specs()


@pytest.mark.parametrize("vitest,passed", [(False, True), (True, False)],
                         ids=["no-vitest-case", "vitest-case-not-passed"])
def test_without_a_passing_vitest_case_a_frontend_file_selects_as_any_ts_file(
        tmp_path, vitest, passed):
    """Another repo, or a baseline that skipped the Vitest suite: the CMX-174 path, unchanged."""
    root = _repo(tmp_path)
    sel = js.Selector(root, _cases(vitest=vitest, passed=passed)).select(DRAG)

    assert sel.nodes == [f"{SUITES_PY}::test_js_suite[tests/sidebar_groups.test.mjs]"]
    assert (sel.env, sel.args, sel.confirm) == ({}, [], None)


def test_a_frontend_file_nothing_observes_runs_the_full_suite_as_untested(tmp_path):
    files = {k: v for k, v in FILES.items() if k != f"{FE}/uikit.test.ts"}
    root = _repo(tmp_path, files)
    sel = js.Selector(root, _cases()).select(f"{FE}/features/sidebar/lib/lonely.ts")

    assert sel.full and sel.untested and sel.confirm is None
    assert sel.why == "no test in the baseline observes it — full suite"


def test_a_frontend_selection_that_raises_runs_the_full_suite(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    s = js.Selector(root, _cases())
    monkeypatch.setattr(js.Selector, "related_specs",
                        lambda self, rel: (_ for _ in ()).throw(RuntimeError("graph broke")))
    sel = s.select(DRAG)

    assert sel.full and not sel.untested and sel.confirm is None
    assert sel.why == "frontend selection failed (graph broke) — full suite"


def test_a_python_file_keeps_its_selection(tmp_path):
    """Python mutations are untouched: no env, no args, the full suite confirms."""
    root = _repo(tmp_path, {**FILES, "chela/x.py": "X = 1\n",
                            "tests/test_python.py": "import chela.x\n"})
    sel = js.Selector(root, _cases()).select("chela/x.py")

    assert sel.nodes == ["tests/test_python.py"]
    assert (sel.env, sel.args, sel.confirm) == ({}, [], None)
    assert sel.argv == ["tests/test_python.py"]


@pytest.mark.parametrize("spec,want", [
    ("./style.css?raw", "src/style.css"),
    ("./a.ts?url", "src/a.ts"),
    ("./missing.css?raw", None),
])
def test_a_vite_query_is_not_part_of_the_path(tmp_path, spec, want):
    _repo(tmp_path, {"src/style.css": "x{}\n", "src/a.ts": "export {};\n", "src/m.ts": ""})
    assert js._resolve_js(tmp_path, "src/m.ts", spec) == want


# --- the real repo --------------------------------------------------------------------------


def _real_cases() -> list[js.Case]:
    """This repo's test files as a baseline universe — less this file, which names drag.ts."""
    skip = {"test_frontend.py", Path(__file__).name}
    cases = [js.Case(p.relative_to(ROOT).as_posix(), p.relative_to(ROOT).as_posix(), True)
             for p in sorted((ROOT / "tests").glob("test_*.py")) if p.name not in skip]
    cases.append(js.Case("tests/test_frontend.py", VITEST_NODE, True))
    for p in sorted((ROOT / "tests").glob("*.test.mjs")):
        rel = p.relative_to(ROOT).as_posix()
        cases.append(js.Case(SUITES_PY, f"{SUITES_PY}::test_js_suite[{rel}]", True, rel))
    return cases


def test_the_real_sidebar_drag_ts_selects_its_killer_and_no_python_file():
    """ACCEPTANCE on this repo: ``drag.ts`` selects ``drag.test.tsx`` (the spec that kills
    ``if (false && (!a || !b)) return a === b;`` at its ``sameTarget`` guard, measured in the
    PR) and the sidebar root suites, and no Python test file but the Vitest case."""
    sel = js.Selector(ROOT, _real_cases()).select(
        "chela/dashboard/frontend/src/features/sidebar/lib/drag.ts")

    specs = sel.env["CHELA_FRONTEND_VITEST_FILES"].split(os.pathsep)
    assert "src/features/sidebar/tests/drag.test.tsx" in specs
    assert "src/features/sidebar/tests/dragWrite.test.ts" in specs
    assert f"{SUITES_PY}::test_js_suite[tests/sidebar_click_context.test.mjs]" in sel.nodes
    assert [n for n in sel.nodes
            if not n.startswith(f"{SUITES_PY}::test_js_suite[")] == [VITEST_NODE]
    assert sel.confirm.nodes[:2] == [VITEST_NODE, SUITES_PY]


# --- the judge: subset, then the ring -------------------------------------------------------


@pytest.fixture
def runs(monkeypatch):
    """A scripted ``run_suite``: records ``(cmd, env)`` and returns the next result."""
    seen: list[tuple[str, dict | None]] = []
    script: list[judge.SuiteResult] = []

    def fake(test_cmd, cwd, timeout=judge.SUITE_TIMEOUT_SECONDS, extra_env=None):
        seen.append((test_cmd, extra_env))
        return script.pop(0)

    monkeypatch.setattr(judge, "run_suite", fake)
    return SimpleNamespace(seen=seen, script=script)


GREEN = judge.SuiteResult(True, 0, 4, 0, 0, "4 passed")
RED = judge.SuiteResult(True, 1, 3, 1, 0, "1 failed, 3 passed")


def _measure(root: Path, sel: js.Selection) -> judge.Outcome:
    exp = judge.Experiment(guard="g", file=DRAG, before="a", after="b")
    return judge._measure(root, "uv run pytest -q", exp, "applied", True, "", GREEN, 60,
                          None, sel)


def test_a_killed_frontend_subset_is_final_and_runs_with_its_env(tmp_path, runs):
    root = _repo(tmp_path)
    sel = js.Selector(root, _cases()).select(DRAG)
    runs.script[:] = [RED]
    out = _measure(root, sel)

    assert out.verdict == judge.KILLED and not out.confirmed_full
    assert runs.seen == [(js.extend("uv run pytest -q", sel.argv), sel.env)]


@pytest.mark.parametrize("ring,verdict", [(RED, judge.KILLED), (GREEN, judge.SURVIVED)])
def test_a_frontend_subset_survivor_is_confirmed_on_the_ring_never_the_full_suite(
        tmp_path, runs, ring, verdict):
    root = _repo(tmp_path)
    sel = js.Selector(root, _cases()).select(DRAG)
    runs.script[:] = [GREEN, ring]
    out = _measure(root, sel)

    assert out.verdict == verdict and out.confirmed_full
    assert out.subset_verdict == judge.SURVIVED
    assert runs.seen[1] == (js.extend("uv run pytest -q", sel.confirm.argv), None)
    assert "uv run pytest -q" not in [cmd for cmd, _ in runs.seen]
    assert out.selection == f"{sel.why}; {sel.confirm.why}"
    assert out.measured_by.startswith(
        "2 selected test(s) green → confirmed on every JS suite, no Python suite (CMX-383)")


def test_a_python_subset_survivor_still_confirms_on_the_full_suite(tmp_path, runs):
    root = _repo(tmp_path)
    sel = js.Selection(["tests/test_python.py"], 1, "1 node(s) observe chela/x.py")
    runs.script[:] = [GREEN, GREEN]
    out = _measure(root, sel)

    assert runs.seen == [("uv run pytest -q tests/test_python.py", None),
                         ("uv run pytest -q", None)]
    assert out.verdict == judge.SURVIVED
    assert out.selection == "1 node(s) observe chela/x.py"
    assert "confirmed on the full suite" in out.measured_by


# --- ⚡ the night's throughput --------------------------------------------------------------


def _report(statuses: list[str], battery: float) -> sweep.SweepReport:
    m = sweep.Mutation("a.ts", 1, "if-false", "x", "y")
    return sweep.SweepReport(results=[sweep.SweepResult(m, s) for s in statuses],
                             battery_seconds=battery)


@pytest.mark.parametrize("statuses,battery,measured,rate", [
    ([judge.KILLED] * 3, 360.0, 3, 30.0),
    ([judge.KILLED, judge.SURVIVED, sweep.UNRUN], 180.0, 2, 40.0),
    ([sweep.UNRUN], 180.0, 0, None),
    ([judge.KILLED], 0.0, 1, None),
    ([], 100.0, 0, None),
])
def test_per_hour_counts_measured_mutations_over_battery_time(statuses, battery, measured,
                                                               rate):
    r = _report(statuses, battery)
    assert (r.measured, r.per_hour) == (measured, rate)
    d = r.as_dict()
    assert (d["measured"], d["per_hour"], d["battery_seconds"]) == (measured, rate, battery)


def test_the_battery_clock_excludes_the_baseline(tmp_path, monkeypatch):
    """Only the mutations' own time is the battery's: a 500s baseline is not in it."""
    clock = [1000.0]
    muts = [sweep.Mutation("a.py", n, "if-false", f"l{n}", f"m{n}") for n in (1, 2, 3)]
    monkeypatch.setattr(sweep, "_now", lambda: clock[0])
    monkeypatch.setattr(sweep, "collect_mutations", lambda wt, base: (list(muts), ""))
    monkeypatch.setattr(sweep, "unsupported_files", lambda wt, base: ([], ""))
    monkeypatch.setattr(sweep, "generated_files", lambda wt, base: ([], ""))
    monkeypatch.setattr(sweep.judge, "provision_suite_env", lambda wt: "")

    def baseline(*a, **kw):
        clock[0] += 500
        return SimpleNamespace(green=True, detail="", tail="", exit_code=0), None, ""

    def run_one(worktree, test_cmd, mut, base, timeout, selector):
        clock[0] += 120
        return sweep.SweepResult(mut, judge.KILLED, "stub"), ""

    monkeypatch.setattr(sweep.judge, "run_baseline", baseline)
    monkeypatch.setattr(sweep, "_run_one", run_one)
    report = sweep.sweep(tmp_path, "origin/dev", "cmd", timeout=5,
                         budget=sweep.SweepBudget(seconds=10_000, max_mutations=10))

    assert report.battery_seconds == 360
    assert report.seconds == 860
    assert report.per_hour == 30.0


def _result(**kw) -> dict:
    base = {"workflow": "/x/chelamux/WORKFLOW.md", "status": sweep_nightly.UNRUN,
            "filed": [], "already_filed": 0, "failed": [], "would_file": [], "warnings": [],
            "prs": ["a"], "mutations": 436, "unrun": 411, "survivors": 0, "groups": []}
    return {**base, **kw}


def test_the_notice_reports_the_nights_throughput():
    line = sweep_nightly.summary(_result(measured=25, battery_seconds=11_520.0, per_hour=7.8))
    assert ("— 1 standard-tier PR(s), 436 mutation(s), 411 UNRUN — 25 measured in 3.2 h: "
            "7.8 mutations/hour") in line


@pytest.mark.parametrize("kw", [{}, {"measured": 0, "battery_seconds": 0.0, "per_hour": None},
                                {"measured": 3, "battery_seconds": 0.0, "per_hour": None}])
def test_a_night_with_no_rate_says_none(kw):
    assert "mutations/hour" not in sweep_nightly.summary(_result(**kw))
    assert sweep_nightly.throughput(_result(**kw)) == ""
