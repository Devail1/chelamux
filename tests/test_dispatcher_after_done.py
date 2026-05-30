"""Tests for the after_done hook firing in dispatcher._fire_after_done."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from chela import dispatcher
from chela.workflow import WorkflowDef


def _wf(tmp_path: Path, after_done: str | None) -> WorkflowDef:
    cfg: dict = {}
    if after_done is not None:
        cfg = {"hooks": {"after_done": after_done}}
    return WorkflowDef(path=tmp_path / "WORKFLOW.md", config=cfg, prompt_template="")


def test_fire_after_done_noop_when_unset(tmp_path):
    wf = _wf(tmp_path, after_done=None)
    with patch.object(dispatcher.subprocess, "Popen") as popen:
        dispatcher._fire_after_done(wf)
    popen.assert_not_called()


def test_fire_after_done_runs_detached_in_repo_dir(tmp_path):
    wf = _wf(tmp_path, after_done="echo hi")
    with patch.object(dispatcher.subprocess, "Popen") as popen:
        dispatcher._fire_after_done(wf)
    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args[0] == "echo hi"
    assert kwargs["shell"] is True
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["start_new_session"] is True


def test_fire_after_done_swallows_popen_failure(tmp_path):
    wf = _wf(tmp_path, after_done="echo hi")
    with patch.object(
        dispatcher.subprocess, "Popen", side_effect=OSError("boom")
    ):
        # Must not raise — best-effort, can't block reconcile.
        dispatcher._fire_after_done(wf)
