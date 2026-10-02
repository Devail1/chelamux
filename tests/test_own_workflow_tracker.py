"""chelamux's own queue is Linear (CMX-438): the repo's WORKFLOW.md must say so.

Loads the committed ``WORKFLOW.md`` — not a fixture — so reverting the flip to the
markdown adapter (or pointing it at another team) fails here, not silently in the daemon.
No network: the source is only constructed (``load_api_key`` reads the sandboxed
chela.env), never asked for tasks.
"""
from __future__ import annotations

from pathlib import Path

from chela.sources import get_source
from chela.sources.linear import LinearSource
from chela.workflow import load_workflow

REPO_WORKFLOW = Path(__file__).resolve().parent.parent / "WORKFLOW.md"


def test_repo_workflow_tracker_is_linear_team_cmx():
    wf = load_workflow(REPO_WORKFLOW)
    assert wf.get("tracker", "kind") == "linear"
    assert wf.get("tracker", "team") == "CMX"
    assert wf.get("tracker", "ready_states") == ["Todo"]
    assert wf.get("tracker", "done_state") == "Done"


def test_repo_workflow_resolves_to_linear_source():
    src = get_source(load_workflow(REPO_WORKFLOW))
    assert isinstance(src, LinearSource)
    assert src.team == "CMX"
