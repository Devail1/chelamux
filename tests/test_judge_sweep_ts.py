"""⚖️🧹 CMX-173 — ``chela judge sweep`` mutates TS/TSX, and proves every mutant still parses.

Fixture repos only: each one symlinks this repo's ``node_modules`` (the pinned ``typescript``)
so its suite can transpile and its mutants can be parse-checked.
"""
from __future__ import annotations

import os
import shutil
import signal
import time
from pathlib import Path

import pytest

from chela import dispatcher, judge, sweep
from tests.test_judge_sweep import _cli, _git, _plain_repo

NODE_MODULES = Path(__file__).resolve().parents[1] / "node_modules"

FLAG = '''\
export function badge(n: number, on: boolean): string | null {
  if (n > 3) return "many";
  return on && n === 1 ? "one" : null;
}
export const View = (p: { on: boolean }) => <p>{p.on && <b />}</p>;
export type Big<A> = A extends string ? 1 : 2;
'''

# The suite transpiles src/flag.tsx with typescript and runs it, with `h` as the JSX factory.
RUN = '''\
const ts = require("typescript"), fs = require("fs"), assert = require("assert");
const js = ts.transpileModule(fs.readFileSync("src/flag.tsx", "utf8"), {compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, jsxFactory: "h"}}).outputText;
const m = {exports: {}};
new Function("exports", "module", "h", js)(m.exports, m, (t, _p, ...c) => ({t, c}));
const {badge, View} = m.exports;
'''
REAL = RUN + '''\
assert.strictEqual(badge(5, false), "many"); assert.strictEqual(badge(3, false), null);
assert.strictEqual(badge(1, true), "one"); assert.strictEqual(badge(1, false), null);
assert.strictEqual(badge(2, true), null);
assert.deepStrictEqual(View({on: true}).c, [{t: "b", c: []}]);
assert.deepStrictEqual(View({on: false}).c, [false]);
'''
WEAK = RUN + 'assert.strictEqual(typeof badge, "function");\n'


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    if not (NODE_MODULES / "typescript").is_dir() or shutil.which("node") is None:
        if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
            pytest.fail("typescript/node missing — run `pnpm install --frozen-lockfile`")
        pytest.skip("typescript/node not installed")
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela-dir"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _ts_repo(root: Path, test: str) -> Path:
    root = _plain_repo(root, {".gitignore": "node_modules\n"},
                       {"src/flag.tsx": FLAG, "tests/run.test.cjs": test})
    (root / "node_modules").symlink_to(NODE_MODULES)
    return root


def _statuses(root: Path, test_cmd: str = "node tests/run.test.cjs") -> dict:
    report = sweep.sweep(root, "origin/dev", test_cmd, timeout=60, select_tests=False)
    assert report.cannot_verify == ""
    return {(r.mutation.line, r.mutation.kind): r.status for r in report.results}


LIVE = {(2, "if-false"), (2, "compare-flip"), (3, "compare-flip"), (3, "logic-flip"),
        (3, "ternary-false"), (3, "ternary-true"), (5, "logic-flip"), (5, "jsx-and")}
BROKEN = {(6, "ternary-false"), (6, "ternary-true")}   # `false && (A extends string)` as a TYPE


def test_a_real_test_kills_every_ts_mutant_and_a_non_parsing_one_is_skipped(tmp_path):
    got = _statuses(_ts_repo(tmp_path / "r", REAL))
    assert got == {**{k: judge.KILLED for k in LIVE}, **{k: sweep.UNPARSEABLE for k in BROKEN}}


def test_a_weak_test_lets_every_ts_mutant_survive_and_reports_it(tmp_path):
    root = _ts_repo(tmp_path / "r", WEAK)
    report = sweep.sweep(root, "origin/dev", "node tests/run.test.cjs", timeout=60,
                         select_tests=False)
    d = report.as_dict()
    assert {(s["line"], s["kind"]) for s in d["survivors"]} == LIVE
    assert d["verdict"] == sweep.V_SURVIVORS and d["unsupported"] == []
    assert (root / "src/flag.tsx").read_text() == FLAG and _git(root, "status", "--porcelain") == ""


def test_no_typescript_to_parse_with_fails_closed(tmp_path):
    root = _ts_repo(tmp_path / "r", REAL)
    (root / "node_modules").unlink()
    report = sweep.sweep(root, "origin/dev", "true", timeout=60, select_tests=False)
    assert "src/flag.tsx is a changed source file that could not be parsed" in report.cannot_verify
    assert "no `typescript` resolvable" in report.cannot_verify and report.results == []


def test_ts_parse_check_reports_each_outcome(tmp_path):
    (tmp_path / "node_modules").symlink_to(NODE_MODULES)
    for name, body, ok in (("a.tsx", "const a = <b />;\n", True), ("a.ts", "f(;\n", False)):
        (tmp_path / name).write_text(body)
        assert judge.parse_check(tmp_path / name)[0] is ok, name
    (tmp_path / "node_modules").unlink()
    assert judge.ts_parse(tmp_path / "a.ts")[0] == judge.TS_NO_PARSER
    assert judge.parse_check(tmp_path / "a.ts")[1].startswith("no parse check ran")


def test_a_sigkilled_sweep_leaves_no_tsx_mutation(tmp_path):
    """CMX-131 for a .tsx: SIGKILL mid-mutation, the guardian restores the file."""
    root = _ts_repo(tmp_path / "r", REAL)
    flag = tmp_path / "flag"
    proc = _cli(root, f"sh -c 'grep -q \"false &&\" src/flag.tsx && touch {flag} && sleep 120"
                      "; true'", tmp_path / "cd", start_new_session=True)
    deadline = time.monotonic() + 60
    while not flag.exists():
        assert proc.poll() is None and time.monotonic() < deadline, "never reached a mutation"
        time.sleep(0.1)
    assert sweep.marker_path(root).exists()
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)
    deadline = time.monotonic() + 30
    while (root / "src/flag.tsx").read_text() != FLAG or sweep.marker_path(root).exists():
        assert time.monotonic() < deadline, "the guardian never restored the .tsx"
        time.sleep(0.1)
    assert _git(root, "status", "--porcelain") == ""


def _ts(src: str) -> list[tuple[str, str]]:
    return [(m.kind, m.after) for m in sweep.js_mutations("m.tsx", src, {1}, ts=True)]


@pytest.mark.parametrize("line, kinds", [
    ("x = a ?? b;", []), ("f(a?.b);", []), ("function f(x?: T) {}", []),
    ("return true;", [("return-bool", "return false;")]),
    ("return false;", [("return-bool", "return true;")]),
    ('s = "a && b ? c : d";', []),
    ("x = c ? a : b;", [("ternary-false", "x = false && (c) ? a : b;"),
                        ("ternary-true", "x = true || (c) ? a : b;")]),
    ("x = a || b;", [("logic-flip", "x = a && b;")]),
])
def test_ts_only_mutations(line, kinds):
    assert _ts(line) == kinds
    assert sweep.js_mutations("m.js", line, {1}) == []      # plain JS gets none of them


@pytest.mark.parametrize("line, cond", [
    ("f(a, c ? x : y)", "c"), ("v = c ? x : y", "c"), ("v => c ? x : y", "c"),
    ("return c ? x : y", "c"), ("throw c ? x : y", "c"), ("{a: c ? x : y}", "c"),
    ("p ? q : c ? x : y", "c"), ("a == b ? x : y", "a == b"), ("a <= b ? x : y", "a <= b"),
    ("g(h(1)) ? x : y", "g(h(1))"), ("a?.b ? x : y", "a?.b"), ("a ?? b ? x : y", "a ?? b"),
    ("a; c ? x : y", "c"), ("[c ? x : y]", "c"), ('f("(", c) ? x : y', 'f("(", c)'),
])
def test_cond_start_stops_at_each_delimiter(line, cond):
    q = line.rindex(" ? ")
    assert line[sweep._cond_start(line, q):q] == cond


def test_ts_and_tsx_are_swept_never_unknown():
    for rel in ("a.ts", "src/b.tsx"):
        assert sweep._sweepable(rel) and not sweep._unsupported(rel), rel
    for rel in ("a.d.ts", "a.spec.ts", "__tests__/a.tsx", "a.test.tsx", "x.TS"):
        assert not sweep._sweepable(rel), rel
    assert sweep._unsupported("x.TS") and sweep._unsupported("y.Tsx")
