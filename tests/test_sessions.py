"""window ↔ session — the resolution that was a ``cwd`` guess, and took the relay down.

CMX-70. The outbound relay resolved a window to its transcript through the pane's cwd;
these lock in that it now resolves by **session id**, and that every way a cwd LIES is a
case with a test on it:

  * a ``--resume`` from a different directory (**the live 2026-07-14 outage**: the
    transcript stays in the project dir the session was BORN in, so the cwd names a
    directory holding zero transcripts, the resolver returns None, and the relay is dead
    in complete silence);
  * an agent that ``cd``s (the pane path moves; the session does not);
  * two windows in one directory (they collide on "newest file wins" and tail EACH
    OTHER'S transcript — a relay that posts one agent's output into another agent's topic
    is worse than silence);
  * a window id RECYCLED by a restarted tmux server, whose old events are still in the log.

The cwd remains, as the last resort it always should have been: a brand-new window that
has fired no hook and was not resumed has nothing else, and it resolves fine.
"""
from __future__ import annotations

import json
import time

import pytest

from chela import event_log, sessions, transcripts

SID = "7f3a91c2-4b8e-4d15-9c62-1e0d5a8b3f47"       # the session of the live outage
OTHER = "cf19ca61-ffbb-4dbf-a8c7-66b74294fa69"


@pytest.fixture
def projects(tmp_path, monkeypatch):
    """A ``~/.claude/projects`` of our own — the owner of every transcript here."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", root)
    return root


def _transcript(projects, cwd: str, session_id: str, *, records: int = 1):
    """Write a session's transcript where Claude Code really writes it: under the project
    dir of the directory the session was BORN in."""
    proj = projects / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_id}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for i in range(records):
            fh.write(json.dumps({"type": "assistant", "timestamp": f"2026-07-14T12:0{i}:00Z",
                                 "message": {"content": []}}) + "\n")
    return path


def _panes(monkeypatch, *panes: sessions.Pane):
    monkeypatch.setattr(sessions, "panes",
                        lambda force=False: {p.wid: p for p in panes})


@pytest.fixture(autouse=True)
def no_tmux(monkeypatch):
    """No live tmux in a unit test: a test that wants panes says so."""
    monkeypatch.setattr(sessions, "panes", lambda force=False: {})


# --- the outage ---------------------------------------------------------------------

def test_a_resumed_session_resolves_to_the_transcript_it_is_REALLY_writing(
        projects, monkeypatch):
    """THE BUG, verbatim (2026-07-14). The tmux server died; the window was rebuilt at
    ``…/analytics/data_prep`` running ``claude --resume`` of a session born in
    ``…/analytics``. The transcript kept growing in the ORIGINAL project dir; the resolver
    searched the new one, which held zero transcripts; outbound was dead for an hour and
    said NOTHING."""
    live = _transcript(projects, "/home/u/projects/analytics", SID)
    (projects / transcripts.encode_cwd("/home/u/projects/analytics/data_prep")).mkdir()
    _panes(monkeypatch, sessions.Pane(
        wid="@2", path="/home/u/projects/analytics/data_prep", command="claude",
        claude_pid=16154, launched_in="/home/u/projects/analytics/data_prep", resumed=SID))

    # what the old resolver did, and why it went silent:
    assert transcripts.transcript_for_cwd("/home/u/projects/analytics/data_prep") is None

    res = sessions.resolve_window("@2")
    assert res.path == live
    assert res.session_id == SID
    assert res.source == "cmdline"


def test_the_event_log_names_the_session_and_that_wins(projects, monkeypatch):
    """A hook fires INSIDE the agent's process, so the session id it carries is ground
    truth — and it follows a ``/clear`` (a new session id) that a command line cannot."""
    _transcript(projects, "/home/u/repo", SID)
    fresh = _transcript(projects, "/home/u/repo", OTHER)
    _panes(monkeypatch, sessions.Pane(
        wid="@3", path="/home/u/repo", command="claude", claude_pid=99,
        launched_in="/home/u/repo", resumed=SID, started=time.time() - 60))
    event_log.append("hook.pre_tool_use", "a tool ran", wid="@3", session_id=OTHER)

    res = sessions.resolve_window("@3")
    assert res.path == fresh                       # the /clear-ed session, not --resume's
    assert res.source == "event_log"


def test_a_cd_ed_agent_still_resolves_to_its_own_session(projects, monkeypatch):
    """The pane path follows the shell. The session does not."""
    live = _transcript(projects, "/home/u", SID)
    _transcript(projects, "/home/u/repo", OTHER)   # a DIFFERENT agent's session lives here
    _panes(monkeypatch, sessions.Pane(
        wid="@0", path="/home/u/repo",             # ← cd-ed: the mutable, lying thing
        command="claude", claude_pid=7, launched_in="/home/u",   # ← the process never moves
        started=time.time() - 60))
    event_log.append("hook.pre_tool_use", "a tool ran", wid="@0", session_id=SID)

    assert sessions.resolve_window("@0").path == live


def test_two_windows_in_one_directory_do_not_tail_each_others_transcript(
        projects, monkeypatch):
    """The cwd guess hands both windows whichever file was written last — i.e. it posts
    one agent's output into the other's topic. A session id cannot do that."""
    a = _transcript(projects, "/home/u/repo", SID)
    b = _transcript(projects, "/home/u/repo", OTHER)
    _panes(monkeypatch,
           sessions.Pane(wid="@1", path="/home/u/repo", command="claude", claude_pid=1,
                         launched_in="/home/u/repo", started=time.time() - 60),
           sessions.Pane(wid="@2", path="/home/u/repo", command="claude", claude_pid=2,
                         launched_in="/home/u/repo", started=time.time() - 60))
    event_log.append("hook.pre_tool_use", "a", wid="@1", session_id=SID)
    event_log.append("hook.pre_tool_use", "b", wid="@2", session_id=OTHER)

    assert sessions.resolve_window("@1").path == a
    assert sessions.resolve_window("@2").path == b


def test_a_recycled_window_id_does_not_inherit_the_dead_agents_session(
        projects, monkeypatch):
    """tmux numbers windows from scratch after a server restart, and the log outlives the
    server: the live log carried 309 events under a ``@113`` that no longer exists. A
    mapping recorded BEFORE the process now in that window is a different agent's, and
    inheriting it would relay a dead session into a live topic."""
    _transcript(projects, "/home/u/old", SID)
    now = time.time()
    event_log.append("hook.pre_tool_use", "the DEAD agent", wid="@2", session_id=SID)
    _panes(monkeypatch, sessions.Pane(
        wid="@2", path="/home/u/new", command="claude", claude_pid=5,
        launched_in="/home/u/new", started=now + 1))   # started AFTER that event

    res = sessions.resolve_window("@2")
    assert res.session_id != SID
    assert res.path is None                        # nothing at all — never the wrong thing
    assert not res.ok


# --- the fallback, and its limits ----------------------------------------------------

def test_a_fresh_window_with_no_hook_and_no_resume_still_resolves_by_cwd(
        projects, monkeypatch):
    """The one case the other two signals cannot cover — and the ONLY one cwd now serves."""
    live = _transcript(projects, "/home/u/repo", SID)
    _panes(monkeypatch, sessions.Pane(
        wid="@4", path="/home/u/repo", command="claude", claude_pid=3,
        launched_in="/home/u/repo", started=time.time()))

    res = sessions.resolve_window("@4")
    assert res.path == live
    assert res.source == "cwd"


def test_a_window_that_resolves_to_nothing_says_why(projects, monkeypatch):
    """The failure is otherwise TOTALLY silent — that is the whole bug."""
    _panes(monkeypatch, sessions.Pane(
        wid="@9", path="/home/u/empty", command="claude", claude_pid=4,
        launched_in="/home/u/empty", started=time.time()))

    res = sessions.resolve_window("@9")
    assert not res.ok and res.source == "none"
    assert "/home/u/empty" in res.detail
    assert "/home/u/empty" in sessions.explain("@9")


def test_an_underscore_in_the_path_encodes_the_way_claude_code_encodes_it(projects):
    """Measured on 2.1.209: a session run from ``…/data_prep`` writes to ``…-meme-scalp``.
    chela encoded the underscore through — so the cwd lookup for that agent searched a
    directory that CANNOT EXIST, and found nothing, silently. It was the second half of
    the same outage."""
    assert transcripts.encode_cwd("/home/u/projects/analytics/data_prep") == (
        "-home-u-projects-analytics-data-prep")
    path = _transcript(projects, "/home/u/projects/analytics/data_prep", SID)
    assert path.parent.name == "-home-u-projects-analytics-data-prep"
    assert transcripts.transcript_for_cwd("/home/u/projects/analytics/data_prep") == path


# --- session → transcript ------------------------------------------------------------

def test_a_session_is_found_without_knowing_its_project_dir(projects):
    """A session id is globally unique: no cwd is needed to find its transcript."""
    live = _transcript(projects, "/some/dir/nobody/passed/in", SID)
    assert sessions.transcript_for_session(SID) == live


def test_a_session_id_cannot_walk_out_of_the_projects_dir(projects):
    """It is pasted into a glob. It is validated, not trusted."""
    assert sessions.transcript_for_session("../../etc/passwd") is None
    assert sessions.transcript_for_session("") is None


def test_a_symlinked_transcript_and_its_target_are_ONE_file(projects):
    """The 07-14 duct tape was a hand-made symlink from the searched dir to the real
    transcript. The resolver must not see two files (the monitor keys its read offset on
    the path, and would replay the whole session on every flip)."""
    real = _transcript(projects, "/home/u/projects/analytics", SID)
    shim_dir = projects / transcripts.encode_cwd("/home/u/projects/analytics/data_prep")
    shim_dir.mkdir()
    (shim_dir / f"{SID}.jsonl").symlink_to(real)

    assert sessions.transcript_for_session(SID) == real     # the real path, not the shim


def test_a_pane_claims_the_session_it_was_resumed_with(monkeypatch):
    """The window→session link :mod:`chela.hooks` needs, and the one a ``--resume`` from a
    foreign directory cannot break."""
    _panes(monkeypatch, sessions.Pane(wid="@2", command="claude", resumed=SID),
           sessions.Pane(wid="@3", command="claude"))
    assert sessions.wid_claiming_session(SID) == "@2"
    assert sessions.wid_claiming_session(OTHER) is None
    assert sessions.wid_claiming_session(None) is None


# --- the process facts, off a fixture /proc ------------------------------------------

def _fake_proc(tmp_path, monkeypatch, pid: int, *, comm: str, cmdline: list[str],
               cwd: str, parent: int | None = None):
    proc = tmp_path / "proc"
    d = proc / str(pid)
    (d / "task" / str(pid)).mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in cmdline) + b"\0")
    (d / "task" / str(pid) / "children").write_text("")
    (d / "cwd").symlink_to(cwd)
    (d / "stat").write_text(f"{pid} ({comm}) S 1 " + " ".join(["0"] * 17) + " 500 0 0\n")
    if parent is not None:
        p = proc / str(parent) / "task" / str(parent)
        p.mkdir(parents=True, exist_ok=True)
        (p / "children").write_text(f"{pid} ")
        (proc / str(parent) / "comm").write_text("bash\n")
    (proc / "stat").write_text("btime 1000\n")
    monkeypatch.setattr(sessions, "PROC", proc)
    return proc


def test_the_pane_map_reads_the_claude_process_not_the_pane(tmp_path, monkeypatch):
    """One tmux call, then /proc — no pgrep, no capture-pane, no `claude agents --json`.
    A hook runs while an agent is BLOCKED on it (CMX-41 rejected the pgrep path at seconds
    per call), so this budget is load-bearing."""
    home = tmp_path / "home"
    home.mkdir()
    _fake_proc(tmp_path, monkeypatch, 16154, comm="claude",
               cmdline=["claude", "--resume", SID], cwd=str(home), parent=15499)
    calls = []

    class Result:
        returncode = 0
        stdout = "@2\tclaude\t/somewhere/else\t15499\n"

    def fake_run(argv, **kw):
        calls.append(argv)
        return Result()

    monkeypatch.setattr(sessions.subprocess, "run", fake_run)

    panes = sessions._load_panes()
    pane = panes["@2"]
    assert pane.claude_pid == 16154
    assert pane.resumed == SID
    assert pane.launched_in == str(home)           # the process cwd — NOT the pane path
    assert pane.path == "/somewhere/else"          # what tmux said, kept but not trusted
    assert pane.started == 1000 + 500 / sessions._CLK_TCK
    assert len(calls) == 1 and calls[0][:2] == ["tmux", "list-windows"]
    flat = [a for call in calls for a in call]
    assert not any("pgrep" in a or "capture-pane" in a for a in flat)


def test_a_pane_with_no_claude_process_degrades_to_the_pane_path(tmp_path, monkeypatch):
    """A shell window, or a kernel with no /proc: the cmdline signal simply disappears."""
    monkeypatch.setattr(sessions, "PROC", tmp_path / "nonexistent")

    class Result:
        returncode = 0
        stdout = "@5\tbash\t/home/u\t123\n"

    monkeypatch.setattr(sessions.subprocess, "run", lambda *a, **k: Result())
    pane = sessions._load_panes()["@5"]
    assert pane.claude_pid is None and pane.resumed is None
    assert pane.origin == "/home/u"                # the pane path, as a last resort
