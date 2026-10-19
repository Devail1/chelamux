"""🏭 CMX-284 — generated files (the committed Vite bundle, lockfiles, ``*.min.js``) are never
mutated by the sweep or the judge, never counted by the size gate, and each skip is said out
loud."""
from __future__ import annotations

from pathlib import Path

import pytest

from chela import dispatcher, generated, judge, sizegate, sweep
from tests.test_judge_sweep import _hostile_judge_prompt, _plain_repo

TSX = '''\
export function badge(n: number): string | null {
  if (n > 3) return "many";
  return null;
}
'''
BUNDLE = 'function a(n){if(n>3)return"x";return n===1?"y":null}\n'
BUNDLE_PATH = "chela/dashboard/static/app/assets/index-abc123.js"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela-dir"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    # No typescript needed: the mutators, not the parser, are under test here.
    monkeypatch.setattr(judge, "ts_parse", lambda path: (judge.TS_PARSES, ""))


@pytest.mark.parametrize("rel", [
    "chela/dashboard/static/app/index.html",
    "chela/dashboard/static/app/assets/index-abc123.js",
    "./chela/dashboard/static/app/assets/x.css",
    "uv.lock",
    "chela/dashboard/frontend/pnpm-lock.yaml",
    "vendor.min.js",
    "chela/dashboard/static/vendor/xterm.min.js",
])
def test_generated_paths(rel):
    assert generated.is_generated(rel)


@pytest.mark.parametrize("rel", [
    "chela/dashboard/static/app.js",            # a sibling of the bundle dir, not inside it
    "chela/dashboard/static/apps/x.js",
    "chela/dashboard/frontend/src/App.tsx",
    "sub/uv.lock",                              # only the ROOT lockfile is anchored
    "chela/dashboard/frontend/pnpm-lock.yaml.bak",
    "vendor.min.js.map",
    "admin.js",
    "chela/sweep.py",
])
def test_source_paths_are_not_generated(rel):
    assert not generated.is_generated(rel)


def _port_slice(tmp_path: Path) -> Path:
    return _plain_repo(tmp_path / "r", {}, {"chela/dashboard/frontend/src/badge.tsx": TSX,
                                           BUNDLE_PATH: BUNDLE,
                                           "chela/dashboard/static/app/x.cjs": "1\n"})


def test_a_port_slice_mutates_only_its_tsx_never_the_bundle(tmp_path):
    muts, err = sweep.collect_mutations(_port_slice(tmp_path), "origin/dev")
    assert err == ""
    assert muts, "the .tsx must still be mutated, else the bundle check below is vacuous"
    assert {m.file for m in muts} == {"chela/dashboard/frontend/src/badge.tsx"}


def test_a_generated_file_is_never_unknown_but_is_counted(tmp_path):
    root = _port_slice(tmp_path)
    (root / "uv.lock").write_text("untracked\n")      # untracked generated files count too
    assert sweep.unsupported_files(root, "origin/dev") == ([], "")
    assert sweep.generated_files(root, "origin/dev") == (
        sorted([BUNDLE_PATH, "chela/dashboard/static/app/x.cjs", "uv.lock"]), "")


def test_the_sweep_note_names_the_skipped_count(tmp_path):
    root = _plain_repo(tmp_path / "r", {}, {BUNDLE_PATH: BUNDLE, "uv.lock": "x\n"})
    report = sweep.sweep(root, "origin/dev", "false", timeout=10, provision=False)
    assert report.cannot_verify == ""
    assert report.results == []
    assert report.notes[0].startswith("skipped 2 generated file(s) (")
    assert sweep.verdict(report.as_dict()) == sweep.V_CLEAN


def test_no_generated_file_means_no_note(tmp_path):
    root = _plain_repo(tmp_path / "r", {}, {"README.md": "changed\n"})
    report = sweep.sweep(root, "origin/dev", "false", timeout=10, provision=False)
    assert not any("generated" in n for n in report.notes)
    assert generated.note(0) == ""


def test_an_unreadable_generated_list_fails_closed(tmp_path, monkeypatch):
    root = _plain_repo(tmp_path / "r", {}, {BUNDLE_PATH: BUNDLE})
    monkeypatch.setattr(sweep, "generated_files", lambda wt, base: ([], "git ls-files failed"))
    report = sweep.sweep(root, "origin/dev", "false", timeout=10, provision=False)
    assert report.cannot_verify == "git ls-files failed"
    assert sweep.verdict(report.as_dict()) == sweep.V_CANNOT_VERIFY


def _exp(file: str) -> dict:
    return {"guard": "g", "file": file, "before": "n>3", "after": "n<3"}


def test_the_judge_drops_experiments_on_generated_files_and_counts_them():
    items = [_exp(BUNDLE_PATH), _exp("src/a.tsx"), "garbage", {"file": 7}, _exp("x.min.js")]
    assert judge._drop_generated(items) == ([_exp("src/a.tsx"), "garbage", {"file": 7}], 2)


def test_a_judge_round_names_the_skipped_count_in_its_notes(tmp_path):
    root = _plain_repo(tmp_path / "r", {}, {BUNDLE_PATH: BUNDLE})
    report = judge.run_experiments(root, "false", {"experiments": [_exp(BUNDLE_PATH)]},
                                   timeout=10, base_branch="dev")
    # Every experiment was on the bundle, so nothing ran — never a pass.
    assert "proposed NO experiments" in report.cannot_verify
    assert report.outcomes == []
    assert report.notes[-1] == {"title": "Generated files not mutated",
                                "body": generated.note(1)}
    assert generated.note(1).startswith("skipped 1 generated file(s) (")


def test_the_judge_prompt_names_the_generated_paths(tmp_path):
    prompt = _hostile_judge_prompt(tmp_path, [])
    assert ("Never mutate a GENERATED file** (`chela/dashboard/static/app/**`, `uv.lock`, "
            "`chela/dashboard/frontend/pnpm-lock.yaml`, `**/*.min.js`)") in prompt


def test_the_size_gate_never_counts_generated_files(tmp_path):
    root = _plain_repo(tmp_path / "r", {}, {"chela/x.py": "a\nb\n", BUNDLE_PATH: "x\n" * 40,
                                           "uv.lock": "y\n" * 30})
    assert sizegate.measure(root, "dev", ()) == 2
