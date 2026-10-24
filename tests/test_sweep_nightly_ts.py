"""⚖️🧹🌙 CMX-325 — the nightly sweep provisions its own checkout before the battery.

The first scheduled night (CMX-303) was CANNOT VERIFY: its worktree had no ``node_modules``, so
collecting a merged ``.ts`` file's mutations could not load ``typescript``. Every fixture here is
a throwaway repo whose ``origin`` is a local bare repo; its nightly checkout has NO
``node_modules`` until something provisions it. "Provisioning" is the REAL
:func:`chela.judge.provision_suite_env` driving a fake ``pnpm`` on ``PATH`` that links this
repo's pinned ``typescript`` in — so the reuse is measured, not a stub's word for it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from chela import sweep, sweep_nightly
from chela.workflow import load_workflow
from tests.test_sweep_nightly import OLD, Filer, _commit, _covered, _covered_record, _git

NODE_MODULES = Path(__file__).resolve().parents[1] / "node_modules"

FLAG = 'export function badge(n: number): string | null {\n  if (n > 3) return "many";\n' \
       '  return null;\n}\n'
# Transpiles src/flag.ts with typescript and checks only that it loads: every mutant survives.
WEAK_TEST = '''\
const ts = require("typescript"), fs = require("fs"), assert = require("assert");
const js = ts.transpileModule(fs.readFileSync("src/flag.ts", "utf8"), {compilerOptions: {
  module: ts.ModuleKind.CommonJS}}).outputText;
const m = {exports: {}};
new Function("exports", "module", js)(m.exports, m);
assert.strictEqual(typeof m.exports.badge, "function");
'''
FAKE_PNPM = f'''#!/bin/sh
printf '%s\\n' "$PWD $*" >> "$PNPM_LOG"
ln -s "{NODE_MODULES}" node_modules
'''


@pytest.fixture(autouse=True)
def _need_node():
    if not (NODE_MODULES / "typescript").is_dir() or shutil.which("node") is None:
        if os.environ.get("CHELA_REQUIRE_JS_TESTS"):
            pytest.fail("typescript/node missing — run `pnpm install --frozen-lockfile`")
        pytest.skip("typescript/node not installed")


@pytest.fixture
def pnpm(tmp_path, monkeypatch) -> Path:
    """A fake ``pnpm`` first on PATH; returns the log of ``<cwd> <argv>`` it was run with."""
    bin_dir, log = tmp_path / "bin", tmp_path / "pnpm.log"
    bin_dir.mkdir()
    (bin_dir / "pnpm").write_text(FAKE_PNPM)
    (bin_dir / "pnpm").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PNPM_LOG", str(log))
    return log


def _ts_repo(tmp_path: Path) -> Path:
    """``origin/dev``: an OLD base declaring ``typescript`` (package.json + a pnpm lockfile, no
    node_modules), then ONE standard-tier PR merged now that adds a ``.ts`` file."""
    bare, repo = tmp_path / "origin.git", tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(bare)], check=True)
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "remote", "add", "origin", str(bare))
    wf = (f"---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\nworkspace:\n"
          f"  root: {tmp_path / 'wt'}\n  base_branch: dev\njudge:\n"
          f"  test_cmd: node tests/run.test.cjs\n  suite_timeout_seconds: 120\n"
          f"  select_tests: false\n---\nbody\n")
    _commit(repo, {".gitignore": "node_modules\n", "WORKFLOW.md": wf,
                   "package.json": json.dumps({"devDependencies": {"typescript": "5"}}),
                   "pnpm-lock.yaml": "lockfileVersion: '9.0'\n"}, "base", date=OLD)
    _commit(repo, {"src/flag.ts": FLAG, "tests/run.test.cjs": WEAK_TEST}, "PR: badge (#1)")
    return repo


def _run(repo: Path, chela: Path, **kw) -> dict:
    return sweep_nightly.run(load_workflow(repo / "WORKFLOW.md"), chela_dir=chela,
                             filer=Filer(), **kw)


def test_a_merged_ts_file_is_mutated_once_the_checkout_is_provisioned(tmp_path, pnpm):
    """The acceptance case: no node_modules before provisioning, TS mutations run after."""
    repo, chela = _ts_repo(tmp_path), tmp_path / "chela"
    worktree = (tmp_path / "wt" / "nightly-sweep").resolve()
    res = _run(repo, chela)
    assert res["status"] == "survivors", res.get("why")
    assert res["mutations"] > 0 and res["unrun"] == 0 and res["unsupported"] == []
    assert {g["file"] for g in res["groups"]} == {"src/flag.ts"}
    # the real provisioner ran the locked install, once, in the nightly checkout
    assert pnpm.read_text().splitlines() == [f"{worktree} install --frozen-lockfile"]
    assert _covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): res["at"]}


def test_negative_control_an_unprovisioned_checkout_is_the_first_nights_cannot_verify(
        tmp_path, pnpm, monkeypatch):
    """Without the provisioning call the same night reproduces 10-24: CANNOT VERIFY, no
    `typescript`, and the window does not move."""
    monkeypatch.setattr(sweep_nightly.judge, "provision_suite_env", lambda wt, *a, **k: "")
    repo, chela = _ts_repo(tmp_path), tmp_path / "chela"
    res = _run(repo, chela)
    assert res["status"] == "cannot_verify"
    assert "no `typescript` resolvable" in res["why"] and "src/flag.ts" in res["why"]
    assert _covered_record(chela) == {}


def test_a_cannot_verify_night_leaves_a_recorded_window_unchanged(tmp_path, pnpm, monkeypatch):
    """A window an earlier night recorded is left EXACTLY as it was — the TS file was never
    measured, so its merge must still be inside the next night's window."""
    real = sweep_nightly.judge.provision_suite_env
    monkeypatch.setattr(sweep_nightly.judge, "provision_suite_env", lambda wt, *a, **k: "")
    repo, chela = _ts_repo(tmp_path), tmp_path / "chela"
    earlier = datetime.now().timestamp() - 2 * 86400
    _covered(chela, repo, earlier)
    assert _run(repo, chela)["status"] == "cannot_verify"
    assert _covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): earlier}
    # ...and the next night, provisioned, sweeps that same merge and moves it
    monkeypatch.setattr(sweep_nightly.judge, "provision_suite_env", real)
    res = _run(repo, chela)
    assert res["status"] == "survivors" and res["prs"][0].endswith("PR: badge (#1)")
    assert _covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): res["at"]}


def test_a_checkout_that_cannot_be_provisioned_is_cannot_verify_and_runs_no_battery(
        tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sweep_nightly.judge, "provision_suite_env",
                        lambda wt, *a, **k: seen.append(wt) or "`pnpm install` failed (exit 1)")
    monkeypatch.setattr(sweep_nightly.sweep, "sweep",
                        lambda *a, **k: pytest.fail("the battery ran on an unprovisioned tree"))
    repo, chela = _ts_repo(tmp_path), tmp_path / "chela"
    _covered(chela, repo, 123.0)
    res = _run(repo, chela)
    assert (res["status"], res["why"]) == (
        "cannot_verify",
        "the nightly worktree could not be PROVISIONED: `pnpm install` failed (exit 1)")
    assert seen == [(tmp_path / "wt" / "nightly-sweep").resolve()]
    assert _covered_record(chela) == {str((repo / "WORKFLOW.md").resolve()): 123.0}


def test_the_battery_does_not_provision_a_second_time(tmp_path, pnpm, monkeypatch):
    """Provisioned once, by the nightly; the battery is told not to repeat it."""
    seen = {}

    def fake(worktree, base, test_cmd, **kw):
        seen.update(kw, worktree=worktree)
        return sweep.SweepReport()
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    repo, chela = _ts_repo(tmp_path), tmp_path / "chela"
    assert _run(repo, chela)["status"] == "clean"
    assert seen["provision"] is False
    # provisioned BEFORE the battery: node_modules was there when it was handed the tree
    assert len(pnpm.read_text().splitlines()) == 1
