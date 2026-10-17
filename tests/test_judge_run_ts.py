"""⚖️ CMX-175 — ``chela judge run`` and ``chela judge self-check`` grade TS/TSX experiments.

The fixture repo's suite is a stand-in ``vitest run`` in node: it transpiles each selected
``*.test.ts`` with this repo's pinned ``typescript`` and REALLY runs it, so a mutation that
breaks the code fails a real assertion. It prints Vitest's two summary lines and honours file
filters and ``--outputFile.json=`` as Vitest 3 does. Fixture repos only — never this checkout.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, judge
from tests.test_judge import _run_row
from tests.test_judge_sweep import _plain_repo

NODE_MODULES = Path(__file__).resolve().parents[1] / "node_modules"
CMD = "node bin/vitest run"

VITEST = r'''const ts = require("typescript"), fs = require("fs"), path = require("path");
const args = process.argv.slice(2);
const out = (args.find(a => a.startsWith("--outputFile.json=")) || "").split("=")[1];
const filters = args.filter(a => !a.startsWith("-") && a !== "run");
const files = fs.readdirSync("src").map(f => "src/" + f).filter(f => f.endsWith(".test.ts"))
  .filter(f => !filters.length || filters.some(x => f.includes(x)));
require.extensions[".ts"] = (m, f) => m._compile(ts.transpileModule(fs.readFileSync(f, "utf8"),
  {compilerOptions: {module: ts.ModuleKind.CommonJS, esModuleInterop: true}}).outputText, f);
let passed = 0, failed = 0, bad = 0; const results = [];
for (const f of files) {
  const tests = []; global.test = (n, fn) => tests.push(fn);
  require(path.resolve(f));
  const ar = tests.map(fn => { try { fn(); passed++; return {status: "passed"}; }
                               catch (e) { failed++; return {status: "failed"}; } });
  bad += ar.some(a => a.status === "failed");
  results.push({name: path.resolve(f), assertionResults: ar});
}
console.log(` Test Files  ${bad ? bad + " failed | " : ""}${files.length - bad} passed (${files.length})`);
console.log(`      Tests  ${failed ? failed + " failed | " : ""}${passed} passed (${passed + failed})`);
if (out) fs.writeFileSync(out, JSON.stringify({testResults: results}));
process.exit(failed ? 1 : 0);
'''
CLAMP = """export function clamp(n: number, lo: number, hi: number): number {
  if (n > hi) return hi;
  if (n < lo) return lo;
  return n;
}
"""
BADGE = """export const badge = (n: number): string => (n === 1 ? "one" : "many");
export const show = (on: boolean, n: number): boolean => on && n > 0;
"""
REAL = """import assert from "assert";
import { clamp } from "./clamp";
import { badge, show } from "./badge";
test("clamp", () => assert.deepStrictEqual([clamp(9, 0, 5), clamp(-1, 0, 5), clamp(3, 0, 5)], [5, 0, 3]));
test("badge", () => assert.deepStrictEqual([badge(1), badge(2)], ["one", "many"]));
test("show", () => assert.deepStrictEqual([show(true, 1), show(false, 1), show(true, 0)], [true, false, false]));
"""
WEAK = """import assert from "assert";
import { clamp } from "./clamp";
test("clamp exists", () => assert.strictEqual(typeof clamp, "function"));
"""
CAP = {"guard": "clamp caps at hi", "file": "src/clamp.ts",
       "before": "if (n > hi)", "after": "if (false && n > hi)"}
FIVE = [CAP,
        {"guard": "clamp floors at lo", "file": "src/clamp.ts",
         "before": "if (n < lo)", "after": "if (n >= lo)"},
        {"guard": "clamp passes n through", "file": "src/clamp.ts",
         "before": "return n;", "after": "return hi;"},
        {"guard": "badge says one", "file": "src/badge.ts",
         "before": "n === 1", "after": "n !== 1"},
        {"guard": "show needs on", "file": "src/badge.ts",
         "before": "on && n", "after": "on || n"}]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    if not (NODE_MODULES / "typescript").is_dir() or shutil.which("node") is None:
        if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
            pytest.fail("typescript/node missing — run `pnpm install --frozen-lockfile`")
        pytest.skip("typescript/node not installed")
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _ts_repo(root: Path, test: str, *, typescript: bool = True, extra: dict | None = None):
    root = _plain_repo(root, {".gitignore": "node_modules\n", "bin/vitest": VITEST},
                       {"src/clamp.ts": CLAMP, "src/badge.ts": BADGE, "src/clamp.test.ts": test,
                        **(extra or {})})
    if typescript:
        (root / "node_modules").symlink_to(NODE_MODULES)
    else:   # the suite still runs (its runner finds typescript); the PARSE check cannot
        (root / "bin" / "node_modules").symlink_to(NODE_MODULES)
    return root


def _judge_run(tmp_path: Path, test: str, task_id: str = "ts1") -> tuple[dict, dict | None]:
    """The real `chela judge run`: WORKFLOW.md, run row, judge worktree, real TS suite."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: TEST\ntracker:\n  kind: markdown\n  path: TODO.md\n"
        f"workspace:\n  root: {tmp_path / 'wts'}\n  base_branch: dev\n"
        f"judge:\n  test_cmd: {json.dumps(CMD)}\n  suite_timeout_seconds: 120\n---\n\nx\n")
    _ts_repo(tmp_path / "wts" / f"judge-{task_id}", test)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp = tmp_path / "experiments.json"
    exp.write_text(json.dumps({"experiments": [CAP]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp, cleanup=False)
    return result, dispatcher.resolve_run(task_id)


@pytest.mark.parametrize("test, state, verdict, status", [
    (WEAK, judge.J_BLOCKED, judge.SURVIVED, "changes_requested"),
    (REAL, judge.J_CLEAN, judge.KILLED, "awaiting_review"),
], ids=["weak", "real"])
def test_judge_run_grades_a_ts_only_pr(tmp_path, test, state, verdict, status):
    """GUARD: a weak test lets the TS mutant survive → blocked and sent back; a real one kills
    it → clean. Neither is cannot_verify just because the PR is TypeScript."""
    result, run = _judge_run(tmp_path, test)
    assert (result["state"], result["cannot_verify"]) == (state, "")
    assert [o["verdict"] for o in result["outcomes"]] == [verdict]
    assert "the mutated TS still parses" in result["outcomes"][0]["parse"]
    assert result["outcomes"][0]["selection"] == "1 node(s) observe src/clamp.ts"  # B's selection
    assert run and (run["status"], run["judge_state"]) == (status, state)


@pytest.mark.parametrize("why, typescript, extra, says", [
    ("an unrelated test is red", True,
     {"src/other.test.ts": 'test("x", () => { throw new Error("nope"); });\n'},
     "NOT GREEN before any mutation"),
    ("no typescript to parse with", False, {}, "src/clamp.ts is TypeScript and could not"),
], ids=["red-baseline", "no-typescript"])
def test_an_unknown_on_a_ts_pr_never_reads_clean(tmp_path, why, typescript, extra, says):
    """GUARD: a failure the PR's TS did not cause — a red baseline, or no ``typescript`` to
    prove a mutant parses — is cannot_verify in the judge AND the self-check, never clean."""
    root = _ts_repo(tmp_path / "r", REAL, typescript=typescript, extra=extra)
    for report in (judge.run_experiments(root, CMD, {"experiments": [CAP]}, timeout=60,
                                         base_branch="dev"),
                   judge.self_check(root, CMD, {"experiments": [CAP]}, timeout=60)):
        assert report.state == judge.J_CANNOT_VERIFY, why
        assert says in report.cannot_verify and report.outcomes == []


def test_the_self_check_reaches_all_five_ts_experiments_within_its_budget(tmp_path):
    """GUARD (measured): every experiment of a 5-experiment TS fixture is reached and KILLED
    inside the default self-check budget — none UNRUN. ~4s here vs a 900s budget."""
    root = _ts_repo(tmp_path / "r", REAL)
    report = judge.self_check(root, CMD, {"experiments": FIVE}, timeout=60)
    print(f"5 TS experiments self-checked in {report.battery_seconds:.1f}s")
    assert [o.verdict for o in report.outcomes] == [judge.KILLED] * 5
    assert (report.unrun, report.exhausted, report.state) == ([], "", judge.J_CLEAN)
    assert report.battery_seconds < judge.SELF_CHECK_BUDGET_SECONDS
    assert (root / "src/clamp.ts").read_text() == CLAMP     # every mutant restored


def test_ts_unparseable_checks_only_ts_files_that_exist_and_hides_held_out_names(tmp_path):
    """GUARD: only an existing ``.ts``/``.tsx`` (any case) is parse-checked; the first that
    fails names its file — unless it is held out — and an all-parsing list is ``""``."""
    (tmp_path / "src").mkdir()
    for f in ("a.py", "B.TS", "c.tsx"):
        (tmp_path / "src" / f).write_text("x\n")
    items = ["junk", {"file": 3}, {"file": "src/a.py"}, {"file": "src/gone.ts"},
             {"file": " src/B.TS "}, {"file": "src/c.tsx", "held_out": True}]
    seen: list[Path] = []

    def parse(status):
        return lambda p: (seen.append(p), (status, "why"))[1]

    with patch.object(judge, "ts_parse", side_effect=parse(judge.TS_PARSES)):
        assert judge.ts_unparseable(tmp_path, items) == ""
    assert seen == [tmp_path / "src/B.TS", tmp_path / "src/c.tsx"]
    with patch.object(judge, "ts_parse", side_effect=parse(judge.TS_BROKEN)):
        assert judge.ts_unparseable(tmp_path, items).startswith(
            "src/B.TS is TypeScript and could not be parse-checked before mutating it "
            "(broken: why)")
        msg = judge.ts_unparseable(tmp_path, items[-1:])
    assert msg.startswith("a held-out experiment's file is TypeScript") and "c.tsx" not in msg


def test_counts_drop_vitests_test_files_line_coloured_or_not():
    """GUARD: Vitest's ``Test Files`` line (a FILE count) is not counted as tests — even
    behind the SGR codes ``FORCE_COLOR`` adds — while a ``Test Files``-lookalike still is."""
    sgr = "\x1b[2m Test Files \x1b[22m \x1b[1m\x1b[31m1 failed\x1b[39m | 2 passed (3)\n"
    assert judge._counts(sgr + "      Tests  1 failed | 5 passed (6)\n") == (5, 1, 0)
    assert judge._counts(" Test Files  3 passed (3)\n      Tests  6 passed (6)\n") == (6, 0, 0)
    assert judge._counts("Test Filesystem 2 passed\nx Test Files 1 passed\n") == (3, 0, 0)
