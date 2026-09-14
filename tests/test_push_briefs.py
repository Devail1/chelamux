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

⛔ PR #512 round 5 (THE JUDGE, see ``docs/defeat_shapes/368c-*.md``): round 4's own fix
carried the same disease it cured. (a) The two absence checks it kept — ``"git push -u
origin" not in text`` / ``"gh pr create --base" not in text`` — still pinned VOCABULARY: a
brief that instructs ``git push origin {{branch_name}}`` (no ``-u``) satisfied both, in a
backtick-quoted invocation the agent would actually run. Fixed by matching the INVOCATION
SHAPE instead — any backtick-quoted ``git push``/``gh pr create`` span that carries an
argument — rather than one specific flag spelling; a bare, argument-less ``` `git push` ```
mention (the prohibition sentence itself says this) still doesn't match, only a span with
something after the verb does. (b) The invocation-flags check never asserted the
invocation's own REQUIRED POSITIONAL (``{{task_id}}``) was present, so dropping it left
every flag assertion green while ``chela request-push --pr-title ... --pr-body-file ...``
argparse-refuses for want of ``task_id`` — fixed by asserting the token immediately after
``request-push`` exists and isn't itself a flag.

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


# ⛔ round 5: match the INVOCATION SHAPE (a backtick-quoted span carrying an argument), not
# one specific flag spelling — "git push origin {{branch_name}}" (no `-u`) is just as
# forbidden as "git push -u origin ...", and a bare, argument-less mention like the
# prohibition sentence's own `` `git push` `` must NOT trip this.
_FORBIDDEN_GIT_PUSH_INVOCATION = re.compile(r"`git push +\S[^`]*`")
_FORBIDDEN_GH_PR_CREATE_INVOCATION = re.compile(r"`gh pr create +\S[^`]*`")

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

    # ⛔ round 5, finding 1: match the SHAPE of a runnable invocation (any backtick-quoted
    # `git push`/`gh pr create` span carrying an argument), not one flag spelling like
    # `-u origin` or `--base` — a brief that hands over `git push origin {{branch_name}}`
    # (no `-u`) is just as much a violation, and every fixed-substring check would miss it.
    # A bare, argument-less mention (e.g. the prohibition sentence's own `` `git push` ``)
    # does not match, only a span with something after the verb does.
    forbidden_push = _FORBIDDEN_GIT_PUSH_INVOCATION.search(text)
    assert forbidden_push is None, (
        f"{name}: must not hand the agent a runnable `git push ...` invocation (found "
        f"{forbidden_push.group(0)!r}) — #502 B2's whole point is that this doesn't survive "
        "a sandboxed agent process, regardless of which flags it's spelled with")
    forbidden_create = _FORBIDDEN_GH_PR_CREATE_INVOCATION.search(text)
    assert forbidden_create is None, (
        f"{name}: must not hand the agent a runnable `gh pr create ...` invocation (found "
        f"{forbidden_create.group(0)!r}) — that hop belongs to the daemon now, not the agent")

    # ⛔ round 4: pin the INVOCATION, not the vocabulary. Extract the single backtick-quoted
    # span that starts with `chela request-push` — the actual command line the brief hands
    # over — and check what flags THAT SPAN carries, not whether the substrings appear
    # anywhere in the surrounding prose.
    invocation = re.search(r"`chela request-push[^`]*`", text)
    assert invocation, (
        f"{name}: no backtick-quoted `chela request-push ...` invocation found — cannot "
        "pin its flags")
    line = invocation.group(0)

    # ⛔ round 5, finding 2: the invocation's own REQUIRED positional (`task_id`) was never
    # asserted — a brief could drop it while every flag check below stayed green. Without
    # it, `chela request-push --pr-title ... --pr-body-file ...` argparse-refuses with "the
    # following arguments are required: task_id", no marker is ever written, and the daemon
    # never pushes or opens a PR. The token right after `request-push` must exist and must
    # not itself be a flag.
    tokens = line[1:-1].split()  # strip the enclosing backticks
    assert tokens[:2] == ["chela", "request-push"]
    positional = tokens[2] if len(tokens) > 2 else ""
    assert positional and not positional.startswith("--"), (
        f"{name}: the `chela request-push` invocation must carry the task_id as its first "
        f"positional argument, found {line!r} — without it argparse refuses with 'the "
        "following arguments are required: task_id', no marker is ever written, and the "
        "daemon never pushes or opens a PR")

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
