"""PR #512 round 3 (THE JUDGE + orchestrator): the push/PR-open hop (#502 B2) lives in FIVE
separate briefs handed to agents — three files on disk, two rendered from in-process
templates — and round 2 pinned exactly three of them with three hand-named test functions.
That is the same failure as pinning one: it just moves the edge. The orchestrator's own
words: "adding a sixth copy of this brief tomorrow is covered without editing the test."

``PUSH_BRIEFS`` below is the ONE list. Add a copy of the brief here and it is guarded
automatically — no new test function required. Each entry is ``(name, get_text, kind)``
where ``get_text(tmp_path)`` returns the brief's actual text: a file read for the three
on-disk copies, a rendered call for the two in-code ones (rendering the REWORK_PROMPT
through ``_renudge_prompt``, not importing the constant directly, matters — see
``test_judge.test_rework_prompt_step_4_tells_the_agent_to_request_push_not_git_push``'s
docstring for why the constant alone can't see a wiring break).

⛔ PR #512 round 4 (THE JUDGE): the three fixed substring checks below (``chela
request-push`` present, ``git push -u origin`` absent, ``gh pr create --base`` absent)
each pin VOCABULARY, not the INVOCATION handed to the agent — dropping ``--pr-title
--pr-body-file`` from a first-dispatch brief leaves all three green, but a first-dispatch
agent following that brief now runs ``chela request-push <id>`` with no title,
``_apply_push_request``'s ``if not pr_url and pr_title:`` gate never fires, and the branch
is pushed with NO PR EVER OPENED. ``kind`` distinguishes the two invocation shapes this
command actually has: ``_FIRST_DISPATCH`` briefs must hand over ``chela request-push``
carrying BOTH ``--pr-title`` and ``--pr-body-file`` (without them the daemon never opens a
PR); the one ``_REWORK`` brief (``REWORK_PROMPT``) must hand it over with NEITHER — the PR
already exists. The per-``chela request-push`` INVOCATION (the single backtick-quoted span
that starts with it, not the whole brief text) is what gets checked, so a brief that merely
*mentions* ``--pr-title`` elsewhere (e.g. in a different, unrelated command) can't satisfy
this by accident.

The three original hand-named tests (in ``test_judge.py`` and ``test_starter.py``) stay:
they pin the EXACT command line, which is more specific than the class guard here.
"""
from __future__ import annotations

import json
import re
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


_FIRST_DISPATCH = "first_dispatch"
_REWORK = "rework"

# Every brief chela hands an agent that tells it how to push/open a PR. Add a copy here and
# it is guarded automatically — that is the acceptance criterion, not "these five pass".
# ``kind`` says which invocation shape the brief must hand over — see the module docstring.
PUSH_BRIEFS = (
    ("WORKFLOW.md (this repo's own dispatched agents)",
     _on_disk("WORKFLOW.md"), _FIRST_DISPATCH),
    ("examples/WORKFLOW.md (adopters copying the example)",
     _on_disk("examples/WORKFLOW.md"), _FIRST_DISPATCH),
    ("skills/chela-setup/SKILL.md (the setup skill's Done Criteria)",
     _on_disk("skills/chela-setup/SKILL.md"), _FIRST_DISPATCH),
    ("chela/starter.py's seeded WORKFLOW.md template",
     _starter_seeded_workflow, _FIRST_DISPATCH),
    ("chela/dispatcher.py's REWORK_PROMPT, rendered",
     _rework_prompt_rendered, _REWORK),
)


@pytest.mark.parametrize("name,get_text,kind", PUSH_BRIEFS, ids=[n for n, _, _ in PUSH_BRIEFS])
def test_every_push_brief_routes_through_chela_request_push(tmp_path, name, get_text, kind):
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

    # ⛔ round 4: pin the INVOCATION, not the vocabulary. Extract the single backtick-quoted
    # span that starts with `chela request-push` — the actual command line the brief hands
    # over — and check what flags THAT SPAN carries, not whether the substrings appear
    # anywhere in the surrounding prose.
    invocation = re.search(r"`chela request-push[^`]*`", text)
    assert invocation, (
        f"{name}: no backtick-quoted `chela request-push ...` invocation found — cannot "
        "pin its flags")
    line = invocation.group(0)
    if kind == _FIRST_DISPATCH:
        assert "--pr-title" in line, (
            f"{name}: a first-dispatch brief's `chela request-push` invocation must carry "
            "--pr-title — without it, _apply_push_request's `if not pr_url and pr_title` "
            "gate never fires and no PR is ever opened")
        assert "--pr-body-file" in line, (
            f"{name}: a first-dispatch brief's `chela request-push` invocation must carry "
            "--pr-body-file")
    else:
        assert kind == _REWORK
        assert "--pr-title" not in line, (
            f"{name}: a rework brief's `chela request-push` invocation must carry NO "
            "--pr-title — the PR already exists, so this must only push the new commit")
