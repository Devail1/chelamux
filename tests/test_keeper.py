"""CMX-346: `chela keeper digest` — a deterministic digest of one workflow's repo."""
from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from chela import agent_manager, config, discovery, event_log, keeper, main, sessions, transcripts
from chela.sources import Task
from chela.workflow import load_workflow

T0 = 1_790_000_000            # 2026-09-21 14:13:20 UTC
NOW = T0 + 3600
LIVE_SID = "11111111-2222-3333-4444-555555555555"
STALE_SID = "99999999-8888-7777-6666-555555555555"
REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(path))
    return path


def _git(repo, *args, ts=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    if ts is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{ts} +0000"
    REAL_RUN(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _commit(repo, subject, ts):
    _git(repo, "commit", "--allow-empty", "-q", "-m", subject, ts=ts)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: TAP\ntracker:\n  kind: markdown\n  path: TODO.md\n"
        f"workspace:\n  root: {tmp_path / 'wt'}\n---\nbody\n")
    _commit(repo, "old work TAP-1", T0 - 600)
    _commit(repo, "TAP-7: commit-only id", T0 + 600)
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side branch commit", T0 + 900)
    _git(repo, "checkout", "-q", "dev")
    (tmp_path / "wt" / "TAP-3").mkdir(parents=True)
    return repo


@pytest.fixture
def wf(repo):
    return load_workflow(repo / "WORKFLOW.md")


def _clock(monkeypatch, ts):
    monkeypatch.setattr(event_log, "time", SimpleNamespace(time=lambda: ts))


def _emit(monkeypatch, ts, type_, payload, wid="@3", sid=LIVE_SID):
    _clock(monkeypatch, ts)
    return event_log.append(type_, "", payload, wid=wid, session_id=sid)


def _prompt(monkeypatch, ts, text, **kw):
    return _emit(monkeypatch, ts, keeper.PROMPT_EVENT, {"prompt": text}, **kw)


def _tool(monkeypatch, ts, tool, path, type_=keeper.TOOL_EVENT, **kw):
    return _emit(monkeypatch, ts, type_, {"tool_name": tool, "tool_input": {"file_path": path}},
                 **kw)


PRS = [
    {"number": 12, "title": "TAP-8: pr-only id", "state": "MERGED",
     "createdAt": "2026-09-21T10:00:00Z", "mergedAt": "2026-09-21T14:30:00Z"},
    {"number": 13, "title": "new thing", "state": "OPEN",
     "createdAt": "2026-09-21T14:40:00Z", "mergedAt": None},
    {"number": 9, "title": "stale TAP-2", "state": "OPEN",
     "createdAt": "2026-09-20T10:00:00Z", "mergedAt": None},
]


@pytest.fixture
def gh(monkeypatch):
    """`gh` answers from ``state``; git runs for real. Every call is recorded."""
    state = SimpleNamespace(calls=[], rc=0, out=json.dumps(PRS), raise_=None)

    def fake(argv, **kw):
        state.calls.append((list(argv), kw))
        if argv[0] != "gh":
            return REAL_RUN(argv, **kw)
        if state.raise_:
            raise state.raise_
        return subprocess.CompletedProcess(argv, state.rc, state.out, "gh boom")
    monkeypatch.setattr(subprocess, "run", fake)
    return state


class FakeSource:
    def __init__(self, tasks, read_failed=False, raise_=None):
        self.tasks, self.read_failed, self.raise_ = tasks, read_failed, raise_

    def list_open_tasks(self):
        if self.raise_:
            raise self.raise_
        return self.tasks


def _task(ident, title, tracker_state=None, state="open"):
    return Task(id=ident, title=title, file="", line_number=0, raw="",
                state=state, tracker_state=tracker_state)


@pytest.fixture
def tracker(monkeypatch):
    holder = SimpleNamespace(source=FakeSource([_task("TAP-4", "board item", "Todo"),
                                                _task("TAP-5", "markdown item")]))
    monkeypatch.setattr("chela.sources.get_source", lambda wf: holder.source)
    return holder


@pytest.fixture
def fleet(monkeypatch, tmp_path, repo):
    """Three panes: one in a worktree (claude, a stale --resume id), one elsewhere, one shell."""
    panes = {
        "@3": sessions.Pane(wid="@3", claude_pid=111, launched_in=str(tmp_path / "wt" / "TAP-3"),
                            resumed=STALE_SID, started=T0 - 100),
        "@4": sessions.Pane(wid="@4", claude_pid=222, launched_in=str(tmp_path / "other")),
        "@5": sessions.Pane(wid="@5", path=str(repo)),
    }
    monkeypatch.setattr(sessions, "panes", lambda force=False: panes)
    monkeypatch.setattr(sessions, "registry_session", lambda pid: None)
    monkeypatch.setattr(discovery, "get_windows_by_id",
                        lambda: {"@3": "tap-3-fix-it", "@4": "far", "@5": "zsh"})
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: {})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: {"@3": 111}.get(wid))
    monkeypatch.setattr(agent_manager, "session_entry",
                        lambda pid, m: {"status": "busy"} if pid == 111 else None)
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid: f"/t/{wid}")
    monkeypatch.setattr(transcripts, "summary_for_path", lambda p: {
        "recap": "working on the TAP-6 thing", "recap_ts": "2026-09-21T14:50:00Z"})
    return panes


def _seed(monkeypatch, tmp_path):
    wt = tmp_path / "wt" / "TAP-3"
    _prompt(monkeypatch, T0 - 50, "too old")
    _prompt(monkeypatch, T0 + 60, "please fix TAP-9 " + "x" * 300)
    _prompt(monkeypatch, T0 + 70, "<task-notification> <task-id>b1</task-id> TAP-99")
    _tool(monkeypatch, T0 + 80, "Edit", str(wt / "chela" / "a.py"))
    _tool(monkeypatch, T0 + 90, "Read", str(wt / "chela" / "read.py"))
    _tool(monkeypatch, T0 + 95, "Edit", str(wt / "chela" / "pre.py"),
          type_="hook.pre_tool_use")
    _tool(monkeypatch, T0 + 100, "Write", "/elsewhere/b.md", wid=None)   # by session id only
    _tool(monkeypatch, T0 + 110, "Edit", str(wt / "chela" / "a.py"))
    _prompt(monkeypatch, T0 + 120, "far window TAP-50", wid="@4", sid="other")


GOLDEN = """\
# Keeper digest — TAP (since 2026-09-21 14:13 UTC, from default)

## Sessions (1)
### @3 tap-3-fix-it · busy · session 11111111-2222-3333-4444-555555555555
- recap (14:50): working on the TAP-6 thing
- prompts (1):
  - 14:14 please fix TAP-9 {x}…
- edited (2): /elsewhere/b.md, TAP-3/chela/a.py

## Commits (2)
- {c1} TAP-7: commit-only id
- {c2} side branch commit

## PRs (2)
- #12 merged (MERGED): TAP-8: pr-only id
- #13 opened (OPEN): new thing

## Issues mentioned (5)
TAP-3, TAP-6, TAP-7, TAP-8, TAP-9

## Board (2)
- TAP-4 [Todo] board item
- TAP-5 [open] markdown item
"""


def _sha(repo, subject):
    out = REAL_RUN(["git", "-C", str(repo), "log", "--all", "--format=%H %s"],
                   capture_output=True, text=True).stdout
    return next(line.split()[0][:10] for line in out.splitlines() if line.endswith(subject))


def test_golden_digest(monkeypatch, tmp_path, repo, wf, gh, tracker, fleet):
    _seed(monkeypatch, tmp_path)
    d = keeper.build(wf, now=NOW)
    want = GOLDEN.format(x="x" * (keeper.PROMPT_CHARS - len("please fix TAP-9 ")),
                         c1=_sha(repo, "TAP-7: commit-only id"),
                         c2=_sha(repo, "side branch commit"))
    assert keeper.render(d) == want


def test_session_id_is_the_live_one_never_the_command_line(monkeypatch, tmp_path, wf, gh,
                                                            tracker, fleet):
    """Negative control (CMX-337): with no live evidence the id is unknown, not --resume's."""
    d = keeper.build(wf, now=NOW)
    (window,) = d["sessions"]["items"]
    assert window["session_id"] is None
    assert STALE_SID not in keeper.render(d)
    assert "session unknown" in keeper.render(d)


def test_windows_outside_the_repo_or_without_claude_are_not_sessions(wf, gh, tracker, fleet):
    d = keeper.build(wf, now=NOW)
    assert [w["wid"] for w in d["sessions"]["items"]] == ["@3"]


def test_window_in_the_checkout_itself_counts(monkeypatch, repo, wf, gh, tracker, fleet):
    fleet["@5"] = sessions.Pane(wid="@5", claude_pid=333, path=str(repo / "chela"))
    d = keeper.build(wf, now=NOW)
    assert [w["wid"] for w in d["sessions"]["items"]] == ["@3", "@5"]
    assert d["sessions"]["items"][1]["status"] == "unknown"


@pytest.mark.parametrize("text, want", [
    ("TAP-1 and tap-22, TAP-1 again", ["TAP-1", "TAP-22"]),
    ("branch liavacc/tap-3-fix", ["TAP-3"]),
    ("(TAP-4)", ["TAP-4"]),
    ("TAP-5\n", ["TAP-5"]),
    ("XTAP-6", []),
    ("TAP-7a", []),
    ("TAP-", []),
    ("TAPS-8", []),
    ("CMX-9", []),
    ("1TAP-10", []),
])
def test_issue_ids(text, want):
    assert keeper.issue_ids([text], "TAP") == want


def test_issue_ids_numeric_order_and_none_text():
    assert keeper.issue_ids([None, "TAP-10 TAP-9", ""], "TAP") == ["TAP-9", "TAP-10"]


@pytest.mark.parametrize("source", ["prompt", "commit", "pr", "recap", "name"])
def test_issue_ids_come_from_each_source(monkeypatch, repo, wf, gh, tracker, fleet, source):
    monkeypatch.setattr(transcripts, "summary_for_path", lambda p: {"recap": None,
                                                                    "recap_ts": None})
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@3": "plain"})
    gh.out = "[]"
    if source == "prompt":
        _prompt(monkeypatch, T0 + 60, "see TAP-41")
    elif source == "commit":
        _commit(repo, "fix TAP-41", T0 + 1000)
    elif source == "pr":
        gh.out = json.dumps([{"number": 1, "title": "TAP-41", "state": "OPEN",
                              "createdAt": "2026-09-21T14:30:00Z", "mergedAt": None}])
    elif source == "recap":
        monkeypatch.setattr(transcripts, "summary_for_path",
                            lambda p: {"recap": "on TAP-41", "recap_ts": None})
    else:
        monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@3": "tap-41-x"})
    assert "TAP-41" in keeper.build(wf, now=NOW)["issues"]["items"]


def test_tracker_key_linear_team_else_project_key(tmp_path):
    from chela.workflow import parse_workflow
    lin = parse_workflow(tmp_path / "W.md",
                         "---\nproject_key: TAP\ntracker:\n  kind: linear\n  team: eng\n---\n")
    md = parse_workflow(tmp_path / "W.md", "---\nproject_key: TAP\ntracker:\n  kind: markdown\n"
                                           "  team: eng\n---\n")
    nteam = parse_workflow(tmp_path / "W.md", "---\nproject_key: TAP\ntracker:\n  kind: linear\n"
                                              "---\n")
    assert (keeper.tracker_key(lin), keeper.tracker_key(md), keeper.tracker_key(nteam)) == (
        "ENG", "TAP", "TAP")


# --- the cursor --------------------------------------------------------------------

def test_cursor_advances(monkeypatch, tmp_path, wf, gh, tracker, fleet):
    _prompt(monkeypatch, T0 + 60, "first TAP-21")
    monkeypatch.setattr(keeper.time, "time", lambda: NOW)
    first = json.loads(keeper.run(wf, as_json=True))
    assert first["since_source"] == "default"
    cursor = json.loads(keeper.cursor_path(wf).read_text())
    assert keeper.cursor_path(wf) == config.CHELA_DIR / "keeper" / "tap.json"
    assert cursor == {"seq": first["cursor"]["seq"], "boot_id": first["cursor"]["boot_id"],
                      "ts": NOW}
    assert cursor["seq"] == event_log.tip()["seq"]

    _prompt(monkeypatch, NOW + 60, "second TAP-22")
    monkeypatch.setattr(keeper.time, "time", lambda: NOW + 120)
    second = json.loads(keeper.run(wf, as_json=True))
    assert second["since_source"] == "cursor"
    assert second["since"] == NOW
    texts = [p["text"] for p in second["sessions"]["items"][0]["prompts"]["items"]]
    assert texts == ["second TAP-22"]
    assert json.loads(keeper.cursor_path(wf).read_text())["ts"] == NOW + 120


def test_cursor_seq_not_time_selects_events(monkeypatch, wf, gh, tracker, fleet):
    """An event whose ts is before the cursor's but whose seq is after it is still new."""
    keeper.save_cursor(wf, {"seq": 0, "boot_id": event_log.tip().get("boot_id"), "ts": NOW})
    _prompt(monkeypatch, T0, "late-stamped")
    d = keeper.build(wf, now=NOW + 10)
    assert [p["text"] for p in d["sessions"]["items"][0]["prompts"]["items"]] == ["late-stamped"]


@pytest.mark.parametrize("since, has_cursor, want_source, want_since", [
    (600, True, "--since", NOW - 600),
    (600, False, "--since", NOW - 600),
    (None, True, "cursor", T0 + 1234),
    (None, False, "default", NOW - 3600),
])
def test_window_precedence(wf, gh, tracker, fleet, since, has_cursor, want_source, want_since):
    if has_cursor:
        keeper.save_cursor(wf, {"seq": 0, "boot_id": "", "ts": T0 + 1234})
    d = keeper.build(wf, since=since, now=NOW)
    assert (d["since_source"], d["since"]) == (want_source, want_since)


def test_since_override_reads_by_time_not_cursor(monkeypatch, wf, gh, tracker, fleet):
    _prompt(monkeypatch, NOW - 500, "inside")
    _prompt(monkeypatch, NOW - 900, "outside")
    keeper.save_cursor(wf, {"seq": event_log.tip()["seq"], "boot_id": "", "ts": NOW})
    d = keeper.build(wf, since=600, now=NOW)
    assert [p["text"] for p in d["sessions"]["items"][0]["prompts"]["items"]] == ["inside"]


@pytest.mark.parametrize("raw", ["not json", "[]", '{"seq": "1", "ts": 1}', '{"seq": 1}'])
def test_untrustworthy_cursor_falls_back_to_default(wf, raw):
    keeper.cursor_path(wf).parent.mkdir(parents=True, exist_ok=True)
    keeper.cursor_path(wf).write_text(raw)
    assert keeper.load_cursor(wf) is None


def test_cursor_from_another_boot_reports_the_gap(monkeypatch, wf, gh, tracker, fleet):
    _prompt(monkeypatch, T0 + 60, "x")
    keeper.save_cursor(wf, {"seq": 1, "boot_id": "an-older-boot", "ts": T0})
    d = keeper.build(wf, now=NOW)
    assert "boot_id changed" in d["event_gap"]
    assert "⚠ event log gap: boot_id changed" in keeper.render(d)


def test_cursor_write_failure_is_reported(monkeypatch, wf, gh, tracker, fleet):
    def boom(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(keeper.os, "replace", boom)
    out = keeper.run(wf)
    assert "⚠ cursor not saved (read-only)" in out
    assert json.loads(keeper.run(wf, as_json=True))["cursor_error"] == "read-only"
    assert not keeper.cursor_path(wf).exists()


# --- truncation --------------------------------------------------------------------

def test_truncation_keeps_counts(monkeypatch, tmp_path, repo, wf, gh, tracker, fleet):
    for i in range(12):
        _prompt(monkeypatch, T0 + 10 + i, f"p{i}")
    for i in range(20):
        _tool(monkeypatch, T0 + 100 + i, "Edit", f"/x/f{i}.py")
    for i in range(18):
        _commit(repo, f"c{i}", T0 + 1000 + i)
    gh.out = json.dumps([{"number": i, "title": f"pr{i}", "state": "OPEN",
                          "createdAt": "2026-09-21T14:30:00Z", "mergedAt": None}
                         for i in range(14)])
    tracker.source = FakeSource([_task(f"TAP-{i}", f"t{i}") for i in range(40)])
    for i in range(10):
        fleet[f"@{10 + i}"] = sessions.Pane(wid=f"@{10 + i}", claude_pid=500 + i,
                                            path=str(repo))
    d = keeper.build(wf, now=NOW)
    text = keeper.render(d)
    w = d["sessions"]["items"][0]
    assert (len(w["prompts"]["items"]), w["prompts"]["total"]) == (3, 12)
    assert [p["text"] for p in w["prompts"]["items"]] == ["p9", "p10", "p11"]
    assert (len(w["edits"]["items"]), w["edits"]["total"]) == (8, 20)
    assert (len(d["commits"]["items"]), d["commits"]["total"]) == (15, 20)
    assert (len(d["prs"]["items"]), d["prs"]["total"]) == (10, 14)
    assert (len(d["board"]["items"]), d["board"]["total"]) == (30, 40)
    assert (len(d["sessions"]["items"]), d["sessions"]["total"]) == (8, 11)
    for line in ("## Sessions (8 of 11)", "- prompts (3 of 12):", "- edited (8 of 20):",
                 "## Commits (15 of 20)", "## PRs (10 of 14)", "## Board (30 of 40)"):
        assert line in text
    assert len(text) < 12_000                 # ~3k tokens
    # the TAP-7 commit fell off the commit cap, but its id is still mentioned
    assert all("TAP-7" not in c["subject"] for c in d["commits"]["items"])
    assert "TAP-7" in d["issues"]["items"]


def test_issue_section_is_bounded(monkeypatch, wf, gh, tracker, fleet):
    _prompt(monkeypatch, T0 + 60, " ".join(f"TAP-{i}" for i in range(100, 140)))
    d = keeper.build(wf, now=NOW)
    # 40 from the one long prompt (cut to 200 chars on screen) + TAP-3/6/7/8 from the fixture
    assert (len(d["issues"]["items"]), d["issues"]["total"]) == (30, 44)
    assert "## Issues mentioned (30 of 44)" in keeper.render(d)


# --- reads that fail ----------------------------------------------------------------

@pytest.mark.parametrize("setup, why", [
    (lambda g: setattr(g, "rc", 1), "gh pr list exited 1: gh boom"),
    (lambda g: setattr(g, "out", "{not json"), "unparseable JSON"),
    (lambda g: setattr(g, "out", "{}"), "returned no list"),
    (lambda g: setattr(g, "raise_", FileNotFoundError("no gh")), "gh pr list failed: no gh"),
    (lambda g: setattr(g, "raise_", subprocess.TimeoutExpired("gh", 30)), "gh pr list failed"),
])
def test_unreadable_prs(wf, gh, tracker, fleet, setup, why):
    setup(gh)
    d = keeper.build(wf, now=NOW)
    assert d["prs"]["total"] is None and why in d["prs"]["error"]
    assert "## PRs: unreadable (" in keeper.render(d)


def test_unreadable_git(tmp_path, monkeypatch, gh, tracker, fleet):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "WORKFLOW.md").write_text("---\nproject_key: TAP\ntracker:\n  kind: markdown\n---\n")
    d = keeper.build(load_workflow(plain / "WORKFLOW.md"), now=NOW)
    assert d["commits"]["total"] is None and "git log exited" in d["commits"]["error"]
    assert "## Commits: unreadable (git log exited" in keeper.render(d)


def test_git_spawn_failure_is_unreadable(monkeypatch, wf, gh, tracker, fleet):
    def fake(argv, **kw):
        if argv[:3] == ["git", "-C", argv[2]] and "log" in argv:
            raise subprocess.TimeoutExpired("git", 15)
        return REAL_RUN(argv, **kw)
    monkeypatch.setattr(subprocess, "run", fake)
    d = keeper.build(wf, now=NOW)
    assert d["commits"]["total"] is None and "git log failed" in d["commits"]["error"]


@pytest.mark.parametrize("source, why", [
    (FakeSource([_task("TAP-1", "t")], read_failed=True), "tracker read failed"),
    (FakeSource([], raise_=RuntimeError("linear down")), "tracker read failed: linear down"),
])
def test_unreadable_board(wf, gh, tracker, fleet, source, why):
    tracker.source = source
    d = keeper.build(wf, now=NOW)
    assert d["board"] == {"items": [], "total": None, "error": why}
    assert f"## Board: unreadable ({why})" in keeper.render(d)


def test_empty_board_is_not_unreadable(wf, gh, tracker, fleet):
    tracker.source = FakeSource([])
    d = keeper.build(wf, now=NOW)
    assert d["board"] == {"items": [], "total": 0}
    assert "## Board (0)" in keeper.render(d)


# --- the external calls -----------------------------------------------------------

def test_git_and_gh_argv(repo, wf, gh, tracker, fleet):
    keeper.build(wf, since=600, now=NOW)
    git_log = [(a, k) for a, k in gh.calls if "log" in a]
    assert git_log == [(["git", "-C", str(repo), "log", "--all", "--since=2026-09-21T15:03:20Z",
                         "--format=%H%x1f%ct%x1f%s"],
                        {"capture_output": True, "text": True, "timeout": 15})]
    gh_calls = [(a, k) for a, k in gh.calls if a[0] == "gh"]
    assert gh_calls == [(["gh", "pr", "list", "--state", "all", "--limit", "100",
                          "--search", "updated:>=2026-09-21T15:03:20Z",
                          "--json", "number,title,state,createdAt,mergedAt"],
                         {"cwd": str(repo), "capture_output": True, "text": True,
                          "timeout": 30})]
    top = [(a, k) for a, k in gh.calls if "rev-parse" in a]
    assert top == [(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                    {"capture_output": True, "text": True, "timeout": 15})]


def test_pr_event_classification(wf, gh, tracker, fleet):
    """Merged in-window wins over opened; opened before the window and merged never: out."""
    d = keeper.build(wf, now=NOW)
    assert [(p["number"], p["event"]) for p in d["prs"]["items"]] == [(12, "merged"),
                                                                       (13, "opened")]


# --- parse_since + CLI --------------------------------------------------------------

@pytest.mark.parametrize("text, want", [("600", 600), ("90m", 5400), ("2h", 7200),
                                        ("1d", 86400), ("45s", 45), (" 3H ", 10800)])
def test_parse_since(text, want):
    assert keeper.parse_since(text) == want


@pytest.mark.parametrize("text", ["0", "0m", "-5m", "5w", "m", "", "1.5h", "5 m x"])
def test_parse_since_rejects(text):
    with pytest.raises(ValueError):
        keeper.parse_since(text)


def test_cli_prints_digest(monkeypatch, capsys, wf, gh, tracker, fleet):
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "digest", str(wf.path),
                                     "--since", "90m", "--json"])
    main.main()
    out = json.loads(capsys.readouterr().out)
    assert out["since_source"] == "--since"
    assert out["until"] - out["since"] == 5400


@pytest.mark.parametrize("argv", [["missing.md"], ["{wf}", "--since", "soon"]])
def test_cli_exit_2(monkeypatch, capsys, wf, argv):
    argv = [a.replace("{wf}", str(wf.path)) for a in argv]
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "digest", *argv])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("keeper digest: ")


# --- review fixes -------------------------------------------------------------------

def test_since_look_leaves_the_cursor_alone(wf, gh, tracker, fleet):
    keeper.save_cursor(wf, {"seq": 0, "boot_id": "", "ts": T0})
    out = json.loads(keeper.run(wf, since=300, as_json=True))
    assert out["cursor"] is None
    assert json.loads(keeper.cursor_path(wf).read_text()) == {"seq": 0, "boot_id": "", "ts": T0}


def test_since_look_with_no_cursor_writes_none(wf, gh, tracker, fleet):
    keeper.run(wf, since=300)
    assert not keeper.cursor_path(wf).exists()


@pytest.mark.parametrize("broken", ["git", "gh", "none"])
def test_failed_read_keeps_the_cursor_time(monkeypatch, wf, gh, tracker, fleet, broken):
    if broken == "gh":
        gh.rc = 1
    elif broken == "git":
        monkeypatch.setattr(keeper, "commits", lambda repo, ts: keeper._unreadable("x"))
    d = keeper.build(wf, now=NOW)
    assert d["cursor"]["ts"] == (NOW if broken == "none" else NOW - 3600)
    assert d["cursor"]["seq"] == event_log.tip()["seq"]


def test_gh_limit_hit_is_said(wf, gh, tracker, fleet):
    rows = [{"number": i, "title": f"pr{i}", "state": "OPEN",
             "createdAt": "2026-09-20T10:00:00Z", "mergedAt": None} for i in range(100)]
    rows[-1]["createdAt"] = "2026-09-21T14:30:00Z"
    gh.out = json.dumps(rows)
    d = keeper.build(wf, now=NOW)
    assert d["prs"]["limit_hit"] is True and d["prs"]["total"] == 1
    assert "## PRs (1) — gh's 100-row limit was hit; older ones may be missing" in \
        keeper.render(d)
    gh.out = json.dumps(rows[1:])
    assert keeper.build(wf, now=NOW)["prs"]["limit_hit"] is False


def test_ring_that_starts_after_the_window_is_a_gap(monkeypatch, wf, gh, tracker, fleet):
    monkeypatch.setattr(event_log, "ring", lambda: [{"seq": 2, "ts": NOW - 90}])
    monkeypatch.setattr(event_log, "read", lambda **kw: {
        "events": event_log.ring(), "gap": None, "first_seq": 2, "next_seq": 2,
        "boot_id": "b"})
    d = keeper.build(wf, now=NOW)
    assert d["event_gap"].startswith("the event log no longer reaches back")
    # A ring that starts at seq 1 is the whole log: no gap however late it starts.
    monkeypatch.setattr(event_log, "read", lambda **kw: {
        "events": [], "gap": None, "first_seq": 1, "next_seq": 1, "boot_id": "b"})
    assert keeper.build(wf, now=NOW)["event_gap"] is None


def test_a_previous_occupant_of_the_wid_is_not_this_session(monkeypatch, wf, gh, tracker,
                                                            fleet):
    _prompt(monkeypatch, T0 - 100 - 1, "before this claude started", sid="ancient")
    _prompt(monkeypatch, T0 - 100, "at start")
    d = keeper.build(wf, since=7200, now=NOW)
    texts = [p["text"] for p in d["sessions"]["items"][0]["prompts"]["items"]]
    assert texts == ["at start"]


def test_board_lists_mentioned_issues_first(monkeypatch, wf, gh, tracker, fleet):
    tracker.source = FakeSource([_task(f"TAP-{i}", f"t{i}") for i in range(100, 140)]
                                + [_task("TAP-9", "named")])
    _prompt(monkeypatch, T0 + 60, "TAP-9")
    d = keeper.build(wf, now=NOW)
    assert d["board"]["items"][0]["id"] == "TAP-9"
    assert [t["id"] for t in d["board"]["items"][1:4]] == ["TAP-100", "TAP-101", "TAP-102"]
    assert (len(d["board"]["items"]), d["board"]["total"]) == (30, 41)
