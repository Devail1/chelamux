"""PR #512 round 3 (THE JUDGE + orchestrator): the push/PR-open hop (#502 B2) lives in FIVE
separate briefs handed to agents — three files on disk, two rendered from in-process
templates — and round 2 pinned exactly three of them with three hand-named test functions.
That is the same failure as pinning one: it just moves the edge. The orchestrator's own
words: "adding a sixth copy of this brief tomorrow is covered without editing the test."

``PUSH_BRIEFS`` below is the ONE list. Add a copy of the brief here and it is guarded
automatically — no new test function required. Each entry is ``(name, get_text)`` where
``get_text(tmp_path)`` returns the brief's actual text: a file read for the three on-disk
copies, a rendered call for the two in-code ones (rendering the REWORK_PROMPT through
``_renudge_prompt``, not importing the constant directly, matters — see
``test_judge.test_rework_prompt_step_4_tells_the_agent_to_request_push_not_git_push``'s
docstring for why the constant alone can't see a wiring break).

The three original hand-named tests (in ``test_judge.py`` and ``test_starter.py``) stay:
they pin the EXACT command line, which is more specific than the class guard here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela import dispatcher, starter

from tests.test_judge import _run_row, _wf

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _on_disk(rel_path: str):
    def _get(tmp_path: Path) -> str:
        return (_REPO_ROOT / rel_path).read_text()

    return _get


def _starter_seeded_workflow(tmp_path: Path) -> str:
    """``chela/starter.py``'s embedded template — the copy every freshly-seeded adopter
    repo's ``WORKFLOW.md`` starts life with."""
    return starter._WORKFLOW_TEMPLATE


def _rework_prompt_rendered(tmp_path: Path) -> str:
    """``dispatcher.REWORK_PROMPT``, rendered the way a stuck rework actually gets it — via
    ``_renudge_prompt``, so a wiring break (e.g. the live spawn site's fallback expression
    dropped) is visible here too, not just a change to the constant's own text."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), rework_count=1,
                 review_history=json.dumps([{"round": 1, "at": "t", "body": "fix the thing"}]))
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    prompt = dispatcher._renudge_prompt(wf, row, None)
    assert prompt is not None
    return prompt


# Every brief chela hands an agent that tells it how to push/open a PR. Add a copy here and
# it is guarded automatically — that is the acceptance criterion, not "these five pass".
PUSH_BRIEFS = (
    ("WORKFLOW.md (this repo's own dispatched agents)", _on_disk("WORKFLOW.md")),
    ("examples/WORKFLOW.md (adopters copying the example)", _on_disk("examples/WORKFLOW.md")),
    ("skills/chela-setup/SKILL.md (the setup skill's Done Criteria)",
     _on_disk("skills/chela-setup/SKILL.md")),
    ("chela/starter.py's seeded WORKFLOW.md template", _starter_seeded_workflow),
    ("chela/dispatcher.py's REWORK_PROMPT, rendered", _rework_prompt_rendered),
)


@pytest.mark.parametrize("name,get_text", PUSH_BRIEFS, ids=[n for n, _ in PUSH_BRIEFS])
def test_every_push_brief_routes_through_chela_request_push(tmp_path, name, get_text):
    text = get_text(tmp_path)
    assert "chela request-push" in text, (
        f"{name}: must tell the agent to run `chela request-push`, not a bare `git push`")
    assert "git push -u origin" not in text, (
        f"{name}: must not instruct a bare `git push -u origin ...` — #502 B2's whole point "
        "is that this doesn't survive a sandboxed agent process")
    # Every correct brief SAYS the words "gh pr create" once, in the sentence prohibiting it
    # ("do NOT run `git push` or `gh pr create` yourself") — so the guard against the
    # reverted, bad wording must target the INVOCATION shape (`gh pr create --base ...`),
    # not the bare phrase, or this assertion fails on every brief that is already correct.
    assert "gh pr create --base" not in text, (
        f"{name}: must not instruct a bare `gh pr create --base ...` invocation — that hop "
        "belongs to the daemon now, not the agent")
