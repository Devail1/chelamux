"""CMX-93 — the merge-gate demo (``scripts/demo/gate.py``) is driven by chela's REAL gate.

The clip's whole claim is that what it shows is what chela does. So the scenes are
regenerated here, in a subprocess with its own demo root, and each beat must carry the
real code's own words — the judge's SURVIVED and clean comments, ``chela merge``'s refusal
and its merge, the override card before and after the tap. A UI change that rewords any
of them turns this red, which is the cue to re-record (``scripts/demo/record-gate.sh``).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

DEMO = Path(__file__).resolve().parent.parent / "scripts" / "demo"


def _gate():
    spec = importlib.util.spec_from_file_location("chela_demo_gate", DEMO / "gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_scenes_are_the_real_gates_own_output(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHELA_")}
    env["CHELA_DEMO_ROOT"] = str(tmp_path / "root")
    proc = subprocess.run([sys.executable, str(DEMO / "gate.py"), str(tmp_path / "out")],
                          env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-3000:]
    scenes = json.loads((tmp_path / "out" / "scenes.json").read_text())
    assert [s["kind"] for s in scenes] == [
        "term", "comment", "term", "term", "comment", "term", "term", "telegram"]
    _, blocked, refused, _, clean, merged, override, card = scenes
    assert blocked["body"].startswith(
        "## ⚖️ THE JUDGE — a guard on this PR SURVIVED DELIBERATE CORRUPTION")
    assert "- if total >= 50:\n+ if total > 50:" in blocked["body"]
    assert refused["lines"][1].startswith("merge REFUSED: the judge is 'blocked' on this run")
    assert clean["body"].startswith("## ⚖️ THE JUDGE — every guard held")
    assert "| **KILLED** |" in clean["body"]
    assert merged["lines"][1] == "✅ Merged CMX-7 → dev (sha c7d93e15f08a)"
    assert merged["lines"][2].endswith("CI green, judge clean, MERGEABLE")
    assert override["lines"][1].startswith("⏳ Override request override-")
    assert "Judge: blocked on this head" in card["text"]
    assert card["text"].endswith(")") and "Approve merges now" in card["text"]
    assert card["after"].endswith("✅ Approved by @operator — merged a41e0c9d2b7f")
    # Nothing escaped the demo root: the runs DB and the overrides live in its own CHELA_DIR.
    chela_dir = tmp_path / "root" / "gate" / "home" / ".chela"
    assert (chela_dir / "scheduler.db").is_file()
    assert any((chela_dir / "overrides").iterdir())


def test_make_repo_never_deletes_a_root_it_did_not_build(tmp_path):
    gate = _gate()
    precious = tmp_path / "precious.txt"
    precious.write_text("keep me")
    with pytest.raises(SystemExit, match="not a demo root"):
        gate.make_repo(tmp_path)
    assert precious.read_text() == "keep me"


def test_make_repo_rebuilds_its_own_root_from_scratch(tmp_path):
    gate = _gate()
    root = tmp_path / "gate"
    repo = gate.make_repo(root)
    (repo / "stray.txt").write_text("from the last run")
    repo = gate.make_repo(root)
    assert not (repo / "stray.txt").exists()
    assert (repo / "test_shop.py").read_text() == gate.WEAK_TEST
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout == ""


def test_comment_drops_only_the_what_to_do_tail():
    gate = _gate()
    assert gate.comment("## head\n\nbody\n\n### What to do\n\n1. fix it") == "## head\n\nbody"
    assert gate.comment("## head\n\nbody\n") == "## head\n\nbody"


@pytest.mark.parametrize("argv", [[], ["a", "b"]])
def test_main_wants_exactly_one_out_dir(argv, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CHELA_DEMO_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)                 # a mutant that runs on writes nothing here
    assert _gate().main(argv) == 2
    assert capsys.readouterr().err == "usage: gate.py <out-dir>\n"
    assert not (tmp_path / "gate").exists()     # refused before building anything


def test_verdict_of_maps_the_report_and_stops_on_cannot_verify():
    gate = _gate()
    assert gate.verdict_of(SimpleNamespace(cannot_verify="", blocking=["survivor"])) == "blocked"
    assert gate.verdict_of(SimpleNamespace(cannot_verify="", blocking=[])) == "clean"
    with pytest.raises(SystemExit, match="the judge could not verify: red baseline"):
        gate.verdict_of(SimpleNamespace(cannot_verify="red baseline", blocking=["survivor"]))
