"""OKF serializer — lock in conformance (every concept file has a non-empty
`type`; reserved files have no frontmatter; the bundle-root index declares
okf_version) and the producer field mappings. The bundle is exercised against
synthetic state so the test needs no live tmux/DB."""
import pytest
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


def test_agent_doc_description_is_recap_and_surfaces_pr(monkeypatch):
    """An agent concept should read as *what it's doing* (recap) with its project +
    latest PR as frontmatter, so a glance card is insight, not boilerplate."""
    monkeypatch.setattr(discovery, "get_window_cwd", lambda name: "/home/x/nautilus")
    monkeypatch.setattr(okf.transcripts, "_resolve_agent_transcript", lambda name: "/t.jsonl")
    monkeypatch.setattr(okf.transcripts, "latest_recap",
                        lambda p: "# heading\n\nRefactored the risk engine and opened a PR.\nmore detail")
    monkeypatch.setattr(okf.transcripts, "latest_pr",
                        lambda p: type("PR", (), {"url": "https://github.com/x/p/pull/9"})())
    md, cwd = okf._agent_doc("nautilus", "@11", {})
    m = _meta(md)
    assert m["description"] == "Refactored the risk engine and opened a PR."   # recap, not "agent window …"
    assert m["pr_url"] == "https://github.com/x/p/pull/9"
    assert m["project"] == "nautilus"
    assert cwd == "/home/x/nautilus"


def test_agent_doc_description_falls_back_without_recap(monkeypatch):
    monkeypatch.setattr(discovery, "get_window_cwd", lambda name: "/home/x/proj")
    monkeypatch.setattr(okf.transcripts, "_resolve_agent_transcript", lambda name: None)
    md, _ = okf._agent_doc("shell-1", "@2", {})
    m = _meta(md)
    assert m["description"] == "agent window shell-1 @ proj"
    assert "pr_url" not in m           # empty optional keys are dropped (terse bundle)


def test_project_doc_description_rolls_up_counts():
    md = okf._project_doc("nautilus", "/home/x/nautilus", ["a", "b", "a"], [{"task_id": "t"}])
    m = _meta(md)
    assert m["description"] == "2 agents · 1 run"   # deduped agents, run count
    assert m["agent_count"] == 2 and m["run_count"] == 1


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


# ---------------------------------------------------------------------------
# Reader — the consumer half the viewer reads through.
# ---------------------------------------------------------------------------

def _mini_bundle(root):
    """A tiny but representative bundle: a project linked to from an agent, plus
    the reserved index/log files. Mirrors the shape okf.export_bundle emits."""
    (root / "agents").mkdir(parents=True)
    (root / "projects").mkdir(parents=True)
    (root / "agents" / "nautilus.md").write_text(
        "---\ntype: Agent\ntitle: nautilus\ntags: [agent]\n"
        "timestamp: '2026-06-30T09:00:00+00:00'\n---\n\n"
        "**Project:** [nautilus](../projects/nautilus.md)\n", encoding="utf-8")
    (root / "projects" / "nautilus.md").write_text(
        "---\ntype: Project\ntitle: nautilus\ntags: [project]\n"
        "timestamp: '2026-06-30T08:00:00+00:00'\n---\n\n"
        "## Agents\n\n* [nautilus](../agents/nautilus.md)\n", encoding="utf-8")
    (root / "agents" / "index.md").write_text("# Agents\n\n* [nautilus](./nautilus.md)\n", encoding="utf-8")
    (root / "index.md").write_text(
        "---\nokf_version: '0.1'\ntitle: fleet\n---\n\n# fleet\n", encoding="utf-8")
    (root / "log.md").write_text("# Activity log\n\n## 2026-06-30\n- did a thing\n", encoding="utf-8")
    return root


def test_parse_doc_tolerates_missing_and_broken_frontmatter():
    fm, body = okf.parse_doc("no frontmatter here")
    assert fm == {} and body == "no frontmatter here"
    fm, body = okf.parse_doc("---\ntype: Agent\ntitle: x\n---\nhello\n")
    assert fm == {"type": "Agent", "title": "x"} and body.strip() == "hello"
    # a non-mapping / unparseable block is treated as no metadata, never raises
    fm, _ = okf.parse_doc("---\n: : :\n---\nbody")
    assert fm == {}


def test_read_tree_counts_and_groups(tmp_path):
    root = _mini_bundle(tmp_path)
    tree = okf.read_tree(root)
    assert tree["exported"] is True
    assert tree["okf_version"] == "0.1"
    assert tree["total"] == 2                       # reserved index/log excluded
    assert tree["counts"] == {"Agent": 1, "Project": 1}
    assert set(tree["dirs"]) == {"agents", "projects"}
    assert "## 2026-06-30" in tree["log"]


def test_read_tree_missing_bundle_is_not_an_error(tmp_path):
    tree = okf.read_tree(tmp_path / "nope")
    assert tree["exported"] is False and tree["total"] == 0 and tree["dirs"] == {}


def test_read_concept_computes_backlinks_by_inverting_links(tmp_path):
    root = _mini_bundle(tmp_path)
    # The agent links OUT to the project; the project must therefore show the
    # agent as a BACKLINK (the headline feature — invisible to `ls`).
    proj = okf.read_concept(root, "projects/nautilus.md")
    assert proj["type"] == "Project"
    assert [b["path"] for b in proj["backlinks"]] == ["agents/nautilus.md"]
    assert [o["path"] for o in proj["outbound"]] == ["agents/nautilus.md"]
    assert proj["frontmatter"]["tags"] == ["project"]   # unknown/soft keys preserved


def test_read_concept_marks_broken_outbound_links(tmp_path):
    root = _mini_bundle(tmp_path)
    (root / "agents" / "ghost.md").write_text(
        "---\ntype: Agent\ntitle: ghost\n---\n\n[gone](../projects/missing.md)\n", encoding="utf-8")
    c = okf.read_concept(root, "agents/ghost.md")
    assert c["outbound"] == [{"title": "gone", "path": "projects/missing.md", "exists": False}]


def test_read_concept_rejects_path_traversal(tmp_path):
    root = _mini_bundle(tmp_path)
    (tmp_path.parent / "secret.md").write_text("---\ntype: X\n---\nsecret", encoding="utf-8")
    for bad in ["../secret.md", "/etc/passwd", "agents/../../secret.md"]:
        with pytest.raises(ValueError):
            okf.read_concept(root, bad)
    with pytest.raises(ValueError):       # non-markdown is refused outright
        okf.read_concept(root, "agents/nautilus.txt")
    with pytest.raises(FileNotFoundError):
        okf.read_concept(root, "agents/absent.md")


def test_read_search_ranks_meta_over_body_and_filters(tmp_path):
    root = _mini_bundle(tmp_path)
    hits = okf.read_search(root, "nautilus")
    assert {h["path"] for h in hits} == {"agents/nautilus.md", "projects/nautilus.md"}
    # type filter narrows to one concept
    only_agents = okf.read_search(root, "", type_="Agent")
    assert [h["path"] for h in only_agents] == ["agents/nautilus.md"]
    # tag filter likewise
    only_proj = okf.read_search(root, "", tag="project")
    assert [h["path"] for h in only_proj] == ["projects/nautilus.md"]


def test_read_graph_emits_edges_between_real_concepts_only(tmp_path):
    root = _mini_bundle(tmp_path)
    (root / "agents" / "ghost.md").write_text(
        "---\ntype: Agent\ntitle: ghost\n---\n\n[gone](../projects/missing.md)\n", encoding="utf-8")
    g = okf.read_graph(root)
    assert {n["id"] for n in g["nodes"]} == {
        "agents/nautilus.md", "projects/nautilus.md", "agents/ghost.md"}
    # ghost's broken link is dropped; only resolvable concept→concept edges remain
    pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert pairs == {
        ("agents/nautilus.md", "projects/nautilus.md"),
        ("projects/nautilus.md", "agents/nautilus.md"),
    }
