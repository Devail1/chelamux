"""⚖️🧹 CMX-97 slice C1 — ``chela judge sweep --github``: step summary, outputs, ONE sticky PR
comment. ``gh`` is a fake on ``PATH`` that records every argv and stdin it gets."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from chela import sweep, sweep_github

SURVIVOR = {"key": "calc.py:8", "mutation": "if-false: `if x:` → `if False and (x):`"}
LIST = ["api", "--paginate", "repos/o/r/issues/7/comments", "--jq",
        '.[] | select(.body | contains("<!-- chela-judge-sweep -->")) | .id']
FAKE_GH = f"""#!{sys.executable}
import json, os, sys
listing = "--paginate" in sys.argv
with open(os.environ["GH_LOG"], "a") as f:
    f.write(json.dumps([sys.argv[1:], "" if listing else sys.stdin.read()]) + "\\n")
print(os.environ.get("GH_LIST", "") if listing else "{{}}")
if os.environ.get("GH_FAIL") == ("list" if listing else "write"):
    sys.exit("HTTP 403: Resource not accessible by integration")
"""


def _result(**kw) -> dict:
    return {"ok": True, "mutations": 2, "killed": 2, "seconds": 1.0, "base": "main",
            "test_cmd": "pytest", "survivors": [], "notes": [], "recovered": "",
            "cannot_verify": ""} | kw


@pytest.fixture
def gh(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()          # the fake gh first on PATH, an Actions env for PR #7
    (tmp_path / "bin" / "gh").write_text(FAKE_GH)
    (tmp_path / "bin" / "gh").chmod(0o755)
    (tmp_path / "event.json").write_text('{"pull_request": {"number": 7}}')
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    for k, v in {"GH_LOG": tmp_path / "gh.log", "GITHUB_REPOSITORY": "o/r",
                 "GITHUB_EVENT_PATH": tmp_path / "event.json",
                 "GITHUB_STEP_SUMMARY": tmp_path / "summary.md",
                 "GITHUB_OUTPUT": tmp_path / "output"}.items():
        monkeypatch.setenv(k, str(v))
    return lambda: [json.loads(x) for x in (tmp_path / "gh.log").read_text().splitlines()]


def test_a_marked_comment_is_patched_in_place_never_a_second_one(gh, monkeypatch, tmp_path):
    monkeypatch.setenv("GH_LIST", "11\n22")
    result = _result(survivors=[SURVIVOR])
    assert sweep_github.publish(result) == []
    body = sweep_github.render_comment(result)
    assert gh() == [[LIST, ""], [["api", "--method", "PATCH", "repos/o/r/issues/comments/22",
                                  "--input", "-"], json.dumps({"body": body})]]
    assert (tmp_path / "summary.md").read_text() == body
    assert (tmp_path / "output").read_text() == "verdict=survivors\nsurvivors=1\n"


@pytest.mark.parametrize("result, verdict", [
    (_result(survivors=[SURVIVOR]), "survivors"), (_result(unsupported=["a.go"]), "unknown"),
    (_result(cannot_verify="red"), "cannot_verify"), ({"ok": False, "error": "x"}, "error"),
    (_result(), "clean"),
])
def test_no_marked_comment_posts_one_only_when_not_clean(gh, tmp_path, result, verdict):
    assert sweep_github.publish(result) == []
    post = [["api", "--method", "POST", "repos/o/r/issues/7/comments", "--input", "-"],
            json.dumps({"body": sweep_github.render_comment(result)})]
    assert gh() == [[LIST, ""]] + ([] if verdict == "clean" else [post])
    assert (tmp_path / "output").read_text().startswith(f"verdict={verdict}\n")


def test_an_unreadable_comment_list_is_an_error_never_a_post(gh, monkeypatch):
    monkeypatch.setenv("GH_FAIL", "list")
    assert sweep_github.publish(_result(survivors=[SURVIVOR])) == [
        "the PR comment could not be posted: `gh api --paginate repos/o/r/issues/7/comments` "
        "exited 1: HTTP 403: Resource not accessible by integration"]
    assert [argv for argv, _ in gh()] == [LIST]


@pytest.mark.parametrize("exc", [FileNotFoundError("no gh"), subprocess.TimeoutExpired("gh", 60)])
def test_gh_that_cannot_run_is_an_error_and_the_call_is_pinned(exc):
    calls = []
    runner = lambda argv, **kw: calls.append((argv, kw)) or (_ for _ in ()).throw(exc)  # noqa: E731
    assert sweep_github.upsert_comment("o/r", 7, "B", runner=runner) == f"`gh api` could not run: {exc}"
    assert calls == [(["gh", *LIST], dict(input=None, capture_output=True, text=True, timeout=60))]


@pytest.mark.parametrize("event", ['{"pull_request": {"number": 0}}', '{"push": {}}', "{nope",
                                   '{"pull_request": {"number": "7"}}', '{"pull_request": null}'])
def test_no_pr_number_means_no_comment_and_a_warning(gh, tmp_path, event):
    (tmp_path / "event.json").write_text(event)
    assert sweep_github.publish(_result()) == ["not a pull_request run — no PR comment posted"]
    assert not (tmp_path / "gh.log").exists() and sweep_github.pr_number(None) is None


def test_an_unwritable_summary_is_a_warning_and_no_comment_skips_gh(gh, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))        # a directory: IsADirectoryError
    warnings = sweep_github.publish(_result(survivors=[SURVIVOR]), comment=False)
    assert len(warnings) == 1 and warnings[0].startswith("the step summary could not be written: ")
    assert not (tmp_path / "gh.log").exists() and (tmp_path / "output").read_text() == "verdict=survivors\nsurvivors=1\n"


def test_the_comment_body():
    body = sweep_github.render_comment(_result(survivors=[SURVIVOR] * 2, killed=1,
                                               unsupported=["a.go"], not_applied=[{}]))
    assert body.startswith("<!-- chela-judge-sweep -->\n### ⚖️🧹 chela judge sweep — ❌ 2 "
                           "survivor(s)\n\n2 mutation(s) of the lines this PR changed vs `main`: "
                           "1 killed, 2 survived, 1 skipped, 0 timeout, 0 unrun · suite `pytest` · 1.0s\n")
    assert body.count("- `calc.py:8` `` if-false: `if x:` → `if False and (x):` ``\n") == 2
    assert "\n- `a.go`\n" in body and "`` x` ``" == sweep_github._code("x`")
    assert "`chela judge sweep` could not run: no suite at all" in sweep_github.render_comment(
        {"ok": False, "error": "no suite\nat all"})
    assert "\n\n**Cannot verify:** red base\n" in sweep_github.render_comment(
        _result(cannot_verify="red\nbase"))
    assert "Cannot verify" not in sweep_github.render_comment(_result())


def _cli(monkeypatch, capsys, *argv: str):
    from chela import main
    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: _result(survivors=[SURVIVOR]))
    monkeypatch.setattr(sys, "argv", ["chela", "judge", "sweep", "--cwd", ".", *argv])
    with pytest.raises(SystemExit) as e:
        main.main()
    return e.value.code, capsys.readouterr()


@pytest.mark.parametrize("as_json", [False, True])
def test_a_failing_publish_is_a_warning_and_the_exit_code_is_the_sweeps(gh, monkeypatch, capsys,
                                                                       as_json):
    monkeypatch.setenv("GH_FAIL", "write")
    code, cap = _cli(monkeypatch, capsys, "--github", *(["--json"] if as_json else []))
    assert code == 1 and [argv[2] for argv, _ in gh()] == ["repos/o/r/issues/7/comments", "POST"]
    assert ("::warning::chela judge sweep: the PR comment could not be posted: `gh api --method "
            "POST` exited 1: HTTP 403") in (cap.err if as_json else cap.out)


def test_a_raising_publish_is_a_warning_and_no_github_never_publishes(gh, monkeypatch, capsys):
    monkeypatch.setattr(sweep_github, "publish", lambda r, comment: 1 / 0)
    code, cap = _cli(monkeypatch, capsys, "--github", "--no-comment")
    assert code == 1 and "::warning::chela judge sweep: publishing failed: ZeroDivisionError" in cap.out
    seen = []
    monkeypatch.setattr(sweep_github, "publish", lambda r, comment: seen.append(comment) or [])
    assert _cli(monkeypatch, capsys, "--github", "--no-comment")[0] == 1 and seen == [False]
    assert _cli(monkeypatch, capsys, "--github")[0] == 1 and seen == [False, True]
    assert _cli(monkeypatch, capsys)[0] == 1 and seen == [False, True]
