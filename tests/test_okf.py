"""OKF serializer — lock in conformance (every concept file has a non-empty
`type`; reserved files have no frontmatter; the bundle-root index declares
okf_version) and the producer field mappings. The bundle is exercised against
synthetic state so the test needs no live tmux/DB."""
import yaml

from chela import discovery, dispatcher, okf, scheduler
from chela.models import ScheduledTask


def _meta(md: str) -> dict:
    """Parse the YAML frontmatter block of a concept file."""
    assert md.startswith("---"), "concept file must start with frontmatter"
    return yaml.safe_load(md.split("---")[1])


def test_run_doc_field_mapping():
    run = {
        "task_id": "TODO.md:3", "workflow_path": "/home/x/proj/WORKFLOW.md",
        "title": "Add dark mode", "status": "awaiting_review", "window_name": "agent-7",
        "worktree_path": "/home/x/wt/dark", "branch_name": "feat/dark",
        "started_at": "2026-06-29T10:00:00+00:00", "ended_at": "2026-06-29T12:30:00+00:00",
        "attempt": 2, "last_error": None, "pr_url": "https://github.com/x/proj/pull/42",
        "pr_state": "open",
    }
    md = okf._run_doc(run)
    m = _meta(md)
    assert m["type"] == okf.TYPE_RUN
    assert m["resource"] == "https://github.com/x/proj/pull/42"   # resource = pr_url
    assert m["timestamp"] == "2026-06-29T12:30:00+00:00"          # timestamp = ended_at
    assert "[agent-7](../agents/agent-7.md)" in md                # relative cross-link
    assert "`/home/x/wt/dark`" in md                             # worktree under Citations


def test_schedule_doc_field_mapping():
    t = ScheduledTask(
        id=5, agent_name="researcher", schedule_type="interval", schedule_value="15m",
        prompt="Scan arxiv for new papers\nthen summarize", enabled=True,
        last_run=None, next_run="2026-06-30T13:00:00+00:00",
    )
    md = okf._schedule_doc(t)
    m = _meta(md)
    assert m["type"] == okf.TYPE_SCHEDULE
    assert m["schedule_value"] == "15m" and m["enabled"] is True
    assert "[researcher](../agents/researcher.md)" in md
    assert "Scan arxiv for new papers" in md


def test_log_md_is_date_grouped_newest_first():
    runs = [
        {"task_id": "a", "title": "A", "status": "done", "ended_at": "2026-06-28T09:00:00+00:00"},
        {"task_id": "b", "title": "B", "status": "done", "ended_at": "2026-06-29T09:00:00+00:00"},
    ]
    log = okf._log_md(runs)
    assert log.index("## 2026-06-29") < log.index("## 2026-06-28")  # newest first


def test_export_bundle_is_conformant(tmp_path, monkeypatch):
    """A full export against synthetic state must produce an OKF-conformant bundle."""
    run = {
        "task_id": "TODO.md:1", "workflow_path": "/home/x/proj/WORKFLOW.md", "title": "T",
        "status": "done", "window_name": "agent-1", "worktree_path": "", "branch_name": "",
        "started_at": "2026-06-29T10:00:00+00:00", "ended_at": "2026-06-29T11:00:00+00:00",
        "attempt": 1, "last_error": None, "pr_url": "", "pr_state": "",
    }
    task = ScheduledTask(
        id=1, agent_name="agent-1", schedule_type="cron", schedule_value="0 9 * * *",
        prompt="daily", enabled=True, last_run=None, next_run="2026-07-01T09:00:00+00:00",
    )
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [run])
    monkeypatch.setattr(scheduler, "list_tasks", lambda: [task])
    monkeypatch.setattr(discovery, "get_all_windows", lambda: {"agent-1": "@1"})
    monkeypatch.setattr(discovery, "get_window_cwd", lambda name: "/home/x/proj")
    monkeypatch.setattr(okf.transcripts, "_resolve_agent_transcript", lambda name: None)

    out = tmp_path / "knowledge"
    summary = okf.export_bundle(out_dir=out)
    assert summary == {
        "out": str(out), "okf_version": "0.1",
        "runs": 1, "schedules": 1, "agents": 1, "projects": 1, "since": "",
    }

    # Bundle-root index declares okf_version; reserved files carry no frontmatter;
    # every other .md is a concept file with a non-empty `type`.
    for p in sorted(out.rglob("*.md")):
        rel = p.relative_to(out).as_posix()
        text = p.read_text()
        if rel == "index.md":
            assert _meta(text)["okf_version"] == "0.1"
        elif p.name in ("index.md", "log.md"):
            assert not text.startswith("---"), f"{rel} reserved file must have no frontmatter"
        else:
            assert _meta(text).get("type"), f"{rel} missing non-empty type"


def test_within_git_repo_detects_worktree(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert okf._within_git_repo(nested) == tmp_path
    assert okf._within_git_repo(tmp_path.parent) is None
