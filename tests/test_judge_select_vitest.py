"""⚡⚖️ CMX-174 — a TS mutation runs only the Vitest test files whose import graph reaches it.

The fixture runner is a stand-in ``vitest`` (Python): it honours file filters and
``--outputFile.json=`` the way Vitest 3 does, and prints Vitest's ``Tests  N passed`` line. Real
Vitest timings are in the PR; this repo ships no Vitest.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from chela import judge, sweep
from chela import judge_select as js
from tests.test_judge_select import calls  # noqa: F401 — the run_suite spy fixture
from tests.test_judge_sweep import _cli_main

FAKE_VITEST = '''\
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
out = next((a.split("=", 1)[1] for a in args if a.startswith("--outputFile.json=")), None)
filters = [a for a in args if not a.startswith("-") and a != "run"]
files = sorted(p.as_posix() for p in Path("src").rglob("*.test.ts"))
ran = [f for f in files if not filters or any(x in f for x in filters)]
print(f"Tests  {len(ran)} passed ({len(ran)})")
if out and not os.environ.get("FAKE_NO_REPORT"):
    Path(out).write_text(json.dumps({"testResults": [
        {"name": str(Path(f).resolve()), "assertionResults": [{"status": "passed"}]}
        for f in ran]}))
'''
CMD = f'"{sys.executable}" bin/vitest run'
FILES = {
    "bin/vitest": FAKE_VITEST,
    "src/a.ts": "export const a = (x: number): boolean => x > 1;\n",
    "src/b.ts": 'import { a } from "./a.js";\nexport const b = (x: number) => a(x + 1);\n',
    "src/c.ts": "export const c = (x: number): number => x * 2;\n",
    "src/d.ts": "export const d = (x: number): boolean => x === 3;\n",
    "src/e.js": "export const e = (x) => x === 4;\n",
    "src/a.test.ts": 'import { a } from "./a";\n',
    "src/b.test.ts": 'import { b } from "./b";\n',
    "src/c.test.ts": 'import { c } from "./c";\n',
}


def _repo(root: Path) -> Path:
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                        *args], check=True, capture_output=True)
    return root


def _mutate(root: Path, file: str, before: str, after: str, **kw) -> judge.Report:
    exp = {"guard": "g", "file": file, "before": before, "after": after}
    return judge.run_experiments(root, CMD, {"experiments": [exp]}, timeout=60, **kw)


def _only_related(cmd: str) -> None:
    assert "src/a.test.ts" in cmd and "src/b.test.ts" in cmd, cmd
    assert "src/c.test.ts" not in cmd, cmd


def test_a_ts_mutation_runs_only_the_tests_that_reach_it_and_full_suite_selection_fails_that(
    tmp_path, calls,  # noqa: F811
):
    """GUARD: ``a.ts`` is imported by ``a.test.ts`` directly and by ``b.test.ts`` through
    ``b.ts`` (as ``./a.js``); ``c.test.ts`` cannot see it. NEGATIVE CONTROL: with selection
    forced off, the very same "only related" assertion goes red."""
    root = _repo(tmp_path / "r")
    report = _mutate(root, "src/a.ts", "x > 1", "x <= 1")
    assert "Vitest selection over the baseline's 3 test(s)" in report.selection
    assert report.outcomes[0].selected == 2
    assert calls[1] == f"{CMD} src/a.test.ts src/b.test.ts"
    _only_related(calls[1])

    calls.clear()
    _mutate(root, "src/a.ts", "x > 1", "x <= 1", select_tests=False)
    with pytest.raises(AssertionError):
        _only_related(calls[1])


def test_a_ts_file_no_test_reaches_is_UNTESTED_never_skipped(tmp_path, calls):  # noqa: F811
    root = _repo(tmp_path / "r")
    report = _mutate(root, "src/d.ts", "x === 3", "x !== 3")
    [o] = report.outcomes
    assert o.verdict == judge.SURVIVED and report.blocking == [o]
    assert o.reason.startswith("UNTESTED — no test file imports src/d.ts. ")
    assert calls[1:] == [CMD]               # it still RAN — on the full suite


def test_only_a_ts_file_is_UNTESTED(tmp_path):
    root = _repo(tmp_path / "r")
    sel = js.Selector(root, [js.Case(f, f, True, f) for f in ("src/a.test.ts", "src/c.test.ts")])
    assert sel.select("src/d.ts").untested and sel.select("src/d.tsx").untested
    assert not sel.select("src/e.js").untested and sel.select("src/e.js").full
    assert not sel.select("src/a.ts").untested


def test_the_vitest_baseline_asks_for_a_json_report_and_reads_its_universe(tmp_path, calls,  # noqa: F811
                                                                         monkeypatch):
    root = _repo(tmp_path / "r")
    baseline, selector, why = judge.run_baseline(root, CMD, 60, tmp_path)
    report = tmp_path / "baseline-vitest.json"
    assert calls == [f"{CMD} --reporter=default --reporter=json --outputFile.json={report}"]
    assert baseline.green and selector is not None, why
    assert {c.js for c in selector.cases} == {"src/a.test.ts", "src/b.test.ts", "src/c.test.ts"}

    monkeypatch.setenv("FAKE_NO_REPORT", "1")
    report.unlink()
    _, selector, why = judge.run_baseline(root, CMD, 60, tmp_path)
    assert selector is None and "no readable Vitest JSON report" in why


def test_a_red_vitest_baseline_selects_nothing(tmp_path):
    root = _repo(tmp_path / "r")
    baseline, selector, why = judge.run_baseline(root, f'"{sys.executable}" -c "exit(1)" vitest',
                                                 60, tmp_path)
    assert not baseline.green and selector is None and why == ""


@pytest.mark.parametrize("cmd, ok", [
    ("vitest run", True), ("npx vitest", True), ("pnpm exec vitest run --coverage", True),
    ("./node_modules/.bin/vitest run", True), ("vitest watch", False), ("vitest --watch", False),
    ("vitest -w", False), ("vitest related src/a.ts", False), ("vitest run && true", False),
    ("pnpm test", False), ("vitest-x run", False), ("vitest 'run", False), ("", False),
])
def test_is_vitest(cmd, ok):
    assert js.is_vitest(cmd) is ok


def test_parse_vitest_json(tmp_path):
    root = tmp_path / "r"
    (root / "t").mkdir(parents=True)
    report = tmp_path / "v.json"

    def parse(data):
        report.write_text(data if isinstance(data, str) else json.dumps(data))
        return js.parse_vitest_json(root, report)

    ok = [{"status": "passed"}, {"status": "failed"}]
    assert parse({"testResults": [{"name": str(root / "t/x.test.ts"), "assertionResults": ok}]}) \
        == [js.Case("t/x.test.ts", "t/x.test.ts", True, "t/x.test.ts"),
            js.Case("t/x.test.ts", "t/x.test.ts", False, "t/x.test.ts")]
    for bad in ("{", {}, {"testResults": []}, {"testResults": [{"name": "/elsewhere/x.test.ts",
                                                                "assertionResults": ok}]},
                {"testResults": [{"name": str(root / "t/x.test.ts")}]},
                {"testResults": [{"name": 3, "assertionResults": ok}]},
                {"testResults": [{"name": str(root / "x"), "assertionResults": [3]}]}):
        assert parse(bad) is None, bad
    assert js.parse_vitest_json(root, tmp_path / "missing.json") is None


@pytest.mark.parametrize("spec, target", [
    ("./a", "src/a.ts"), ("./a.js", "src/a.ts"), ("./v", "src/v.tsx"), ("./v.js", "src/v.tsx"),
    ("./lib", "src/lib/index.ts"), ("./w", "src/w/index.tsx"), ("./n", "src/n/index.js"),
])
def test_ts_specifiers_resolve(tmp_path, spec, target):
    for rel in ("src/a.ts", "src/v.tsx", "src/lib/index.ts", "src/w/index.tsx",
                "src/n/index.js"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    assert js._resolve_js(tmp_path, "src/x.test.ts", spec) == target


def test_the_sweep_cli_tags_an_untested_survivor(monkeypatch, capsys):
    mut = sweep.Mutation("src/d.ts", 1, "compare-flip", "x === 3", "x !== 3")
    report = sweep.SweepReport(results=[
        sweep.SweepResult(mut, judge.SURVIVED, "UNTESTED — no test file imports src/d.ts."),
        sweep.SweepResult(sweep.Mutation("src/a.ts", 2, "if-false", "a", "b"), judge.SURVIVED,
                          "the suite stayed green")])
    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: report.as_dict())
    assert _cli_main("--cwd", ".") == 1
    out = capsys.readouterr().out
    assert "  [UNTESTED] src/d.ts:1 compare-flip" in out and "  [SURVIVED] src/a.ts:2" in out
