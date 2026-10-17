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


def _em_dashed(values):
    """The values that carry an em dash (U+2014). An en dash or a hyphen is fine (CMX-213)."""
    return [v for v in values if "\u2014" in v]


def _gate():
    spec = importlib.util.spec_from_file_location("chela_demo_gate", DEMO / "gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_scenes_are_the_real_gates_own_output(tmp_path):
    # The child inherits an OPERATOR's environment: a CHELA_DIR and a HOME that are not the
    # demo's. Unset, CHELA_DIR would fall back to $HOME/.chela — the very path gate.py sets —
    # so only a planted one can tell "gate.py set its own" from "gate.py forgot to".
    operator = tmp_path / "operator"
    (operator / ".chela").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHELA_")}
    env.update(CHELA_DEMO_ROOT=str(tmp_path / "root"), HOME=str(operator),
               CHELA_DIR=str(operator / ".chela"))
    proc = subprocess.run([sys.executable, str(DEMO / "gate.py"), str(tmp_path / "out")],
                          env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-3000:]
    scenes = json.loads((tmp_path / "out" / "scenes.json").read_text())
    assert [s["kind"] for s in scenes] == [
        "term", "comment", "term", "term", "comment", "term", "term", "telegram"]
    pushed, blocked, refused, rework, clean, merged, override, card = scenes
    agent, orch = "agent · ~/shop (cmx-7-free-shipping)", "orchestrator · ~/shop"
    assert [s.get("title") for s in scenes] == [agent, None, orch, agent, None, orch, orch, None]
    assert all(s["caption"] for s in scenes) and len({s["caption"] for s in scenes}) == 8
    assert pushed["lines"] == [
        "$ chela request-push CMX-7 --pr-title 'CMX-7: free shipping from $50'",
        "→ PR opened: https://github.com/acme/shop/pull/42"]
    assert blocked["body"].startswith(
        "## ⚖️ THE JUDGE: a guard on this PR SURVIVED DELIBERATE CORRUPTION")
    assert "- if total >= 50:\n+ if total > 50:" in blocked["body"]
    assert "STILL GREEN" in blocked["body"] and "KILLED" not in blocked["body"]
    for body in (blocked["body"], clean["body"]):     # the "What to do" tail is cut off
        assert "### What to do" not in body and body == body.rstrip()
    assert refused["lines"][0] == "$ chela merge CMX-7"
    assert refused["lines"][1].startswith("merge REFUSED: the judge is 'blocked' on this run")
    # The rework adds exactly the boundary test the clean verdict credits — nothing else.
    assert rework["lines"] == [
        "$ git diff --stat && git commit -qam 'pin the $50 boundary'", " test_shop.py | 5 +++++",
        "+def test_free_shipping_starts_at_exactly_50():", "+    assert shipping(50) == 0.0",
        "+    assert shipping(49.99) == 4.99", "$ chela request-push CMX-7",
        "→ pushed 8d21e7f0c3a9 to PR #42"]
    assert clean["body"].startswith("## ⚖️ THE JUDGE: every guard held")
    assert "| **KILLED** |" in clean["body"] and "SURVIVED" not in clean["body"]
    assert merged["lines"][:2] == ["$ chela merge CMX-7", "✅ Merged CMX-7 → dev (sha c7d93e15f08a)"]
    assert merged["lines"][2].endswith("CI green, judge clean, MERGEABLE")
    assert len(merged["lines"]) == 3
    # The override is asked for on the BLOCKED head (before the rework), three lines on camera.
    assert override["lines"][0] == ("$ chela merge CMX-7 --override --reason "
                                    "'boundary covered by the checkout e2e suite'")
    assert override["lines"][1].startswith("⏳ Override request override-")
    assert len(override["lines"]) == 4 and "('blocked' on 3f9c2a1b7d04)" in override["lines"][2]
    assert "Judge: blocked on this head" in card["text"] and "head 3f9c2a1b7d04" in card["text"]
    assert card["text"].endswith(")") and "Approve merges now" in card["text"]
    assert card["after"].endswith("✅ Approved by @operator, merged a41e0c9d2b7f")
    assert "Approve merges now" not in card["after"]
    # Nothing escaped the demo root: the runs DB and the overrides live in its own CHELA_DIR.
    chela_dir = tmp_path / "root" / "gate" / "home" / ".chela"
    assert (chela_dir / "scheduler.db").is_file()
    assert any((chela_dir / "overrides").iterdir())
    assert list(operator.rglob("*")) == [operator / ".chela"]   # the operator's: untouched
    # CMX-211: the frames are visitor-facing, so no string on camera carries an em dash,
    # neither a caption gate.py wrote nor a line of chela's own output it shows.
    on_camera = [v for s in scenes for v in (s["caption"], s.get("title"), s.get("body"),
                                             s.get("text"), s.get("after"), *s.get("lines", []))
                 if v]
    assert len(on_camera) > 30
    assert _em_dashed(on_camera) == []
    assert "Task: CMX-7, Free shipping from $50" in card["text"]   # chela's words, re-punctuated


@pytest.mark.parametrize(("swap", "why"), [
    ("g.WEAK_TEST = g.STRONG_TEST", "the weak test must let the corruption survive"),
    ("g.STRONG_TEST = g.WEAK_TEST + '\\n'", "the boundary test must kill the corruption"),
])
def test_scenes_stop_when_a_verdict_is_not_the_one_the_clip_narrates(swap, why, tmp_path):
    # The captions say "survived" then "killed"; a fixture that no longer earns either
    # verdict must stop the recording, not put a false caption on camera.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHELA_")}
    env.update(CHELA_DEMO_ROOT=str(tmp_path / "root"), HOME=str(tmp_path / "operator"))
    code = "\n".join([
        "import importlib.util, sys",
        f"spec = importlib.util.spec_from_file_location('g', {str(DEMO / 'gate.py')!r})",
        "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)",
        swap, f"sys.exit(g.main([{str(tmp_path / 'out')!r}]))"])
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                          text=True, timeout=300)
    assert proc.returncode != 0
    assert f"AssertionError: {why}" in proc.stderr, proc.stderr[-3000:]
    assert not (tmp_path / "out" / "scenes.json").exists()


def test_main_runs_the_scenes_under_its_own_home_and_chela_dir_only(monkeypatch, tmp_path):
    operator = tmp_path / "operator"
    monkeypatch.setenv("CHELA_DEMO_ROOT", str(tmp_path / "root"))
    for key, value in {"HOME": str(operator), "CHELA_DIR": str(operator / ".chela"),
                       "CHELA_OVERRIDE_WAIT_S": "1", "CHELA_ACTOR": "orchestrator",
                       "TELEGRAM_BOT_TOKEN": "123:real", "CHELA_NTFY_TOPIC": "real-topic",
                       "TMUX": "/tmp/tmux-1000/default,1,0", "TMUX_PANE": "%7"}.items():
        monkeypatch.setenv(key, value)         # restored after the test, whatever main sets
    gate = _gate()
    seen = {}

    def scenes(repo):
        seen.update(os.environ, repo=repo, chela_dir_exists=Path(os.environ["CHELA_DIR"]).is_dir())
        return []

    monkeypatch.setattr(gate, "scenes", scenes)
    assert gate.main([str(tmp_path / "out")]) == 0
    home = tmp_path / "root" / "gate" / "home"
    assert seen["HOME"] == str(home)
    assert seen["CHELA_DIR"] == str(home / ".chela") and seen["chela_dir_exists"]
    assert seen["CHELA_OVERRIDE_WAIT_S"] == "600"
    for leak in ("CHELA_ACTOR", "TELEGRAM_BOT_TOKEN", "CHELA_NTFY_TOPIC", "TMUX", "TMUX_PANE"):
        assert leak not in seen, leak
    assert seen["repo"] == tmp_path / "root" / "gate" / "shop"
    assert json.loads((tmp_path / "out" / "scenes.json").read_text()) == []


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


@pytest.mark.parametrize(("text", "want"), [
    ("## THE JUDGE \u2014 every guard held", "## THE JUDGE: every guard held"),
    ("### head\u2013tail", "### head: tail"),
    ("PENDING \u2014 nothing was merged", "PENDING, nothing was merged"),
    ("exit 0\u20131 passed", "exit 0, 1 passed"),
    ("a #tag \u2014 b", "a #tag, b"),
    ("## head\nbody \u2014 more\n# h2 \u2014 x", "## head\nbody, more\n# h2: x"),
    ("no dash - here: (kept)", "no dash - here: (kept)"),
])
def test_undash_swaps_only_em_and_en_dashes(text, want):
    assert _gate().undash(text) == want


def test_gate_mjs_puts_no_dash_on_camera():
    # Its only literal text on the frames is its own chrome (bar title, buttons); a dash
    # may sit in a // comment, never in code that renders.
    code = [line for line in (DEMO / "gate.mjs").read_text().splitlines()
            if not line.lstrip().startswith("//")]
    assert code and _em_dashed(code) == []


@pytest.mark.parametrize(("value", "caught"), [
    ("PENDING \u2014 nothing was merged", True),
    ("no spaces\u2014still an em dash", True),
    ("chela \u2013 Mission Control", False),     # CMX-213: an en dash is fine on camera
    ("exit 0\u20131 passed", False),
    ("a - hyphen", False),
])
def test_on_camera_check_rejects_only_the_em_dash(value, caught):
    assert _em_dashed(["clean", value]) == ([value] if caught else [])


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
