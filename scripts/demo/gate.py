#!/usr/bin/env python3
"""The merge-gate demo's SCENES, produced by chela's real code against a throwaway repo.

    python scripts/demo/gate.py <out-dir>       # writes <out-dir>/scenes.json

Every verdict, refusal, merge line and card on camera is chela's own output — only the
agent's two beats (``request-push``, the rework commit) are narration, and only its dashes
are swapped (``undash``: frames are visitor-facing, CMX-160). A fresh git repo
(``shop``) gets a feature with a deliberately weak test, and then:

1. the REAL judge (``judge.self_check``) corrupts the guard, the suite stays green, and the
   scene is the exact PR comment it would post (``judge.block_body``);
2. the REAL ``chela merge`` (``main.cmd_merge``) refuses on that verdict;
3. a test that pins the boundary is added, the judge re-runs and every guard holds
   (``judge.comment_body``), and ``chela merge`` merges;
4. the alternative ending: ``chela merge --override`` opens a request, the Telegram card is
   the real ``format_override_message``, and the tap is the real ``overrides.decide``.

GitHub is the one thing faked: the PR's base, checks and mergeability, and ``gh pr merge``
itself. Everything runs under a fresh ``HOME`` + ``CHELA_DIR`` inside the demo root, set
BEFORE chela is imported (its paths latch at import), so the operator's real runs DB,
overrides and event log are never read or written.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get("CHELA_DEMO_ROOT") or "/tmp/demo") / "gate"
MARKER = ".chela-demo-gate"
TASK, PR = "CMX-7", "https://github.com/acme/shop/pull/42"
TEST_CMD = f"{sys.executable} -m pytest -q -p no:cacheprovider"

CODE = '''def shipping(total: float) -> float:
    """Orders of $50 or more ship free."""
    if total >= 50:
        return 0.0
    return 4.99
'''
WEAK_TEST = '''from shop import shipping


def test_free_shipping():
    assert shipping(80) == 0.0
    assert shipping(10) == 4.99
'''
STRONG_TEST = WEAK_TEST + '''

def test_free_shipping_starts_at_exactly_50():
    assert shipping(50) == 0.0
    assert shipping(49.99) == 4.99
'''
EXPERIMENT = {"guard": "orders of exactly $50 ship free", "file": "shop.py",
              "before": "if total >= 50:", "after": "if total > 50:"}


def make_repo(root: Path = ROOT) -> Path:
    """A fresh ``shop`` repo under ``root``, which is only ever deleted when it carries the
    marker this function wrote — a mistyped CHELA_DEMO_ROOT can never rmtree real data."""
    if root.exists():
        if not (root / MARKER).exists():
            raise SystemExit(f"gate.py: refusing to reuse {root} — it is not a demo root")
        shutil.rmtree(root)
    repo = root / "shop"
    repo.mkdir(parents=True)
    (root / MARKER).write_text("")
    (repo / "WORKFLOW.md").write_text("---\nproject_key: CMX\nworkspace:\n  base_branch: dev\n---\n")
    (repo / "shop.py").write_text(CODE)
    (repo / "test_shop.py").write_text(WEAK_TEST)
    for argv in (["init", "-q", "-b", "dev"], ["add", "."], ["commit", "-qm", "free shipping"]):
        subprocess.run(["git", *argv], cwd=repo, check=True, env={
            **os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
            "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@example.com"})
    return repo


def undash(text: str) -> str:
    """chela's own words with no em or en dash, which visitor-facing text must not carry
    (CMX-160): a heading's dash becomes a colon, any other a comma."""
    return "\n".join(re.sub(r"\s*[\u2014\u2013]\s*", ": " if line.startswith("#") else ", ", line)
                     for line in text.split("\n"))


def comment(markdown: str) -> str:
    """The comment as posted, minus its long "What to do" tail — off screen in the clip."""
    return undash(markdown.split("\n### What to do", 1)[0].rstrip())


def verdict_of(report) -> str:
    """The judge state the run row gets — and a judge that could not verify stops the
    recording, since a clip of a red baseline would show nothing true."""
    if report.cannot_verify:
        raise SystemExit(f"gate.py: the judge could not verify: {report.cannot_verify}")
    return "blocked" if report.blocking else "clean"


def run_cli(main, argv: SimpleNamespace) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        main.cmd_merge(argv)
    return undash(out.getvalue().rstrip())


def scenes(repo: Path) -> list[dict]:
    from unittest.mock import patch

    from chela import contract, dispatcher, judge, main, overrides
    from chela.dispatcher import CI_PASSING, CIStatus
    from chela.telegram.overridewatch import format_override_message

    head = {"sha": "3f9c2a1b7d04e6c5a8b9"}
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, started_at,"
            " attempt, pr_url, pr_state, judge_state, judge_sha, branch_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (TASK, str(repo / "WORKFLOW.md"), "Free shipping from $50", "awaiting_review",
             "@3", dispatcher._now(), 1, PR, "open", None, None, "cmx-7-free-shipping"))
        conn.commit()

    def verdict(state: str) -> None:
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET judge_state=?, judge_sha=? WHERE task_id=?",
                         (state, head["sha"], TASK))
            conn.commit()

    def judged() -> tuple[judge.Report, str]:
        report = judge.self_check(repo, TEST_CMD, {"experiments": [EXPERIMENT]})
        return report, verdict_of(report)

    agent, orch = "agent · ~/shop (cmx-7-free-shipping)", "orchestrator · ~/shop"

    def term(caption: str, title: str, *lines: str) -> dict:
        return {"kind": "term", "caption": caption, "title": title, "lines": list(lines)}

    out = [term("An agent finishes CMX-7 in its own worktree and asks for a PR", agent,
                "$ chela request-push CMX-7 --pr-title 'CMX-7: free shipping from $50'",
                f"→ PR opened: {PR}")]
    report, state = judged()
    assert state == "blocked", "the weak test must let the corruption survive"
    verdict(state)
    out.append({"kind": "comment", "caption": "The judge corrupts the guard itself, "
                "and the PR's test stays green",
                "body": comment(judge.block_body(report, PR, "pytest -q"))})

    github = [patch.object(contract, "_read_pr_base", return_value="dev"),
              patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")),
              patch.object(dispatcher, "_read_pr_checks",
                           side_effect=lambda *_: CIStatus(CI_PASSING, head_sha=head["sha"])),
              patch.object(contract, "_squash_merge", side_effect=[
                  {"ok": True, "merge_commit_sha": sha} for sha in ("a41e0c9d2b7f", "c7d93e15f08a")])]
    with contextlib.ExitStack() as stack:
        for p in github:
            stack.enter_context(p)
        merge = SimpleNamespace(run=TASK, reason="", override=False)
        out.append(term("So the merge is refused: CI is green, but the judge blocked this head",
                        orch, "$ chela merge CMX-7", *run_cli(main, merge).splitlines()))

        # The alternative ending, from the refused state: ask a human, one tap.
        why = "boundary covered by the checkout e2e suite"
        ask = SimpleNamespace(run=TASK, reason=why, override=True)
        alt = [term("Or a human overrides it, asked for on the record", orch,
                    f"$ chela merge CMX-7 --override --reason '{why}'",
                    *run_cli(main, ask).splitlines()[:3])]
        req = overrides.pending()[0]["id"]
        alt.append({"kind": "telegram", "caption": "One tap on Telegram: the gate is "
                    "re-checked live, then it merges",
                    "text": undash(format_override_message(overrides.load(req) or {}))})
        overrides.decide(req, True, "@operator", channel="telegram")
        alt[-1]["after"] = undash(format_override_message(overrides.load(req) or {}))

        # The main ending: the agent pins the boundary; the judge comes back clean.
        (repo / "test_shop.py").write_text(STRONG_TEST)
        head["sha"] = "8d21e7f0c3a95b46e1d2"
        out.append(term("The agent reworks: a test that pins the $50 boundary", agent,
                        "$ git diff --stat && git commit -qam 'pin the $50 boundary'",
                        " test_shop.py | 5 +++++",
                        *[f"+{line}" for line in STRONG_TEST[len(WEAK_TEST):].splitlines()
                          if line],
                        "$ chela request-push CMX-7", "→ pushed 8d21e7f0c3a9 to PR #42"))
        report, state = judged()
        assert state == "clean", "the boundary test must kill the corruption"
        verdict(state)
        out.append({"kind": "comment", "caption": "Re-judged on the new head: the corruption "
                    "now turns the suite red",
                    "body": comment(judge.comment_body(report, PR, "pytest -q"))})
        out.append(term("Now chela merge merges, and logs why", orch,
                        "$ chela merge CMX-7", *run_cli(main, merge).splitlines()))
    return out + alt


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: gate.py <out-dir>", file=sys.stderr)
        return 2
    repo = make_repo()
    home = ROOT / "home"
    os.environ.update(HOME=str(home), CHELA_DIR=str(home / ".chela"),
                      CHELA_OVERRIDE_WAIT_S="600")
    for leak in ("CHELA_ACTOR", "TELEGRAM_BOT_TOKEN", "CHELA_NTFY_TOPIC", "TMUX", "TMUX_PANE"):
        os.environ.pop(leak, None)
    (home / ".chela").mkdir(parents=True)
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    (out / "scenes.json").write_text(json.dumps(scenes(repo), indent=1, ensure_ascii=False))
    print(out / "scenes.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
