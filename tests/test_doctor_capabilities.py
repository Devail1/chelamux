"""`chela doctor` must verify the daemon's JOB, not just its env file.

Doctor printed ALL-GREEN while the dispatcher was dead: it checked that the running
environment agreed with the env file, and it did — both were missing
``CHELA_DISPATCH_WORKFLOWS``, so both were wrong. Comparing two copies of a fact is not
checking the fact. These tests pin the capability assertions: a dispatcher that is off is
a WARN (never a silent pass), a workflow that is missing or unparseable is an ERROR, a
tracker that is not there is an ERROR — and doctor is honest about the one thing it
cannot see, that it runs in a different process from the daemon.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chela import capabilities, config, doctor

WF = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
---
Do the thing.
"""


@pytest.fixture
def chela_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir()
    return tmp_path


def _repo(tmp_path, workflow_text=WF, tracker=True):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(workflow_text)
    if tracker:
        (repo / "TODO.md").write_text("- [ ] a task\n")
    return repo / "WORKFLOW.md"


def _findings(monkeypatch, workflows):
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", workflows)
    out: list[doctor.Finding] = []
    doctor._check_daemon(out)
    return out


def _by_level(findings, level):
    return [f for f in findings if f.level == level]


def test_dispatcher_off_is_a_WARN_not_a_silent_pass(chela_dir, monkeypatch):
    findings = _findings(monkeypatch, [])
    warns = _by_level(findings, doctor.WARN)
    titles = " | ".join(f.title for f in warns)
    assert "Work dispatcher: OFF" in titles
    assert "Run reconciliation: OFF" in titles          # say BOTH — they share a tick
    assert not _by_level(findings, doctor.ERROR)        # off is valid, just not invisible
    # The nine-hour bug: nothing in the report may read as green while dispatch is dead.
    assert any(f.level == doctor.WARN for f in findings)


def test_doctor_says_when_it_is_only_inferring(chela_dir, monkeypatch):
    """No daemon has published → doctor is reading its OWN config, which is exactly the
    evidence that failed last time. It must say so rather than imply observation."""
    findings = _findings(monkeypatch, [])
    assert any("INFERRED" in f.detail for f in findings)


def test_doctor_reads_the_RUNNING_daemon_over_its_own_config(chela_dir, monkeypatch, tmp_path):
    # A daemon came up with dispatch OFF; the env file has since been repaired, so this
    # process's config says ON. The daemon is still doing nothing — and only a restart
    # changes that. Config-says-fine is precisely the lie doctor exists to refuse.
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [])
    capabilities.publish(capabilities.effective())

    wf = _repo(tmp_path)
    findings = _findings(monkeypatch, [wf])
    assert any("RUNNING daemon" in f.title for f in _by_level(findings, doctor.ERROR))
    assert any("Work dispatcher: OFF" in f.title for f in _by_level(findings, doctor.WARN))


def test_a_healthy_daemon_reports_its_workflow_and_tracker(chela_dir, monkeypatch, tmp_path):
    wf = _repo(tmp_path)
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [wf])
    capabilities.publish(capabilities.effective())

    findings = _findings(monkeypatch, [wf])
    assert not _by_level(findings, doctor.ERROR)
    oks = " | ".join(f"{f.title} {f.detail}" for f in _by_level(findings, doctor.OK))
    assert "daemon running" in oks
    assert "WORKFLOW.md parses (project CMX)" in oks
    assert "TODO.md" in oks


def test_a_missing_workflow_is_an_ERROR(chela_dir, monkeypatch, tmp_path):
    findings = _findings(monkeypatch, [tmp_path / "gone" / "WORKFLOW.md"])
    assert any("does not exist" in f.title for f in _by_level(findings, doctor.ERROR))


def test_an_unparseable_workflow_is_an_ERROR(chela_dir, monkeypatch, tmp_path):
    wf = _repo(tmp_path, workflow_text="---\nproject_key: not-a-key\n---\nbody\n")
    findings = _findings(monkeypatch, [wf])
    assert any("does not parse" in f.title for f in _by_level(findings, doctor.ERROR))


def test_a_missing_tracker_is_an_ERROR(chela_dir, monkeypatch, tmp_path):
    wf = _repo(tmp_path, tracker=False)
    findings = _findings(monkeypatch, [wf])
    errors = _by_level(findings, doctor.ERROR)
    assert any("tracker" in f.title and "does not exist" in f.title for f in errors)


def test_the_shipped_env_template_carries_the_var_that_went_missing(chela_dir):
    """Item 3: a fresh install must not be able to lose it again. The template ships the
    variable — empty, but PRESENT — with the dispatch-AND-reconcile warning attached."""
    template = Path(__file__).resolve().parents[1] / "examples" / "chela.env"
    text = template.read_text()
    assert "\nCHELA_DISPATCH_WORKFLOWS=" in text        # declared, not merely commented
    block = text.split("CHELA_DISPATCH_WORKFLOWS")[0].rsplit("\n\n", 1)[-1].lower()
    assert "reconcil" in block and "dispatch" in block  # both, or the comment is a trap
