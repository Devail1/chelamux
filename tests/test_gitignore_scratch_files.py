"""The repo's own `.gitignore` must actually match the patterns it claims to — a
syntactically valid line that never matches anything is indistinguishable from no line at
all until something asks git.

CMX-286 round 2: the judge mutated `.chela-self-check-*.json` to
`.chela-self-check-DISABLED-*.json` in a throwaway checkout of this PR's head — still a
valid gitignore pattern, still parses — and the full suite stayed green, because nothing
anywhere drove `git check-ignore` against a real `.chela-self-check-<task>.json` name. See
docs/DEFEAT_SHAPES.md #26.

CMX-289: that fix (and the guard above) still only spoke to the one literal spelling CMX-286's
blocking finding named. WORKFLOW.md step 6 never mandates a filename for the self-check
experiments JSON, so agents keep inventing their own — `.chela-self-check-*.json` matched only
2 of the 4 distinct spellings actually committed across CMX-280/284/286/288/282->dev (see
d77c2c4, docs/defeat_shapes/28). This guard now probes every real spelling on record, so a
future gitignore edit that narrows back to one of them fails here instead of shipping quiet.

CMX-337 round 5: `*selfcheck*.json` (the no-separator spelling, added for
`.chela_selfcheck_cmx337_round3.json`) landed in .gitignore with no probe of its own — every
existing filename in the list below is already matched by one of the OTHER patterns
(`.chela-self-check-*.json`, `*self[-_]check*.json`, `*scratch*experiment*.json`), so the new
line was load-bearing for nothing this suite could see. The judge mutated it to
`*selfcheckDISABLED*.json` — still a syntactically valid glob, matching no real filename — and
the suite stayed green. See docs/defeat_shapes/337 (round 5). A new pattern added to this list must come
with a filename here that ONLY that pattern matches, or it is dead weight from the moment it is
committed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every distinct scratch filename an agent has actually committed for this workflow step, in
# order of occurrence. Keep this the full list, not a representative sample — a pattern that
# matches a subset reads as fixed until the next spelling repeats one already seen here.
REAL_SELF_CHECK_SCRATCH_FILENAMES = [
    ".chela-self-check-cmx280.json",
    ".chela-self-check-cmx284.json",
    ".chela-self-check-cmx286.json",
    "scratch_cmx288_experiments.json",
    "self_check_experiments.json",
    ".chela_selfcheck_cmx337_round3.json",
]


@pytest.mark.parametrize("probe", REAL_SELF_CHECK_SCRATCH_FILENAMES)
def test_gitignore_matches_a_real_self_check_scratch_filename(probe):
    # An autonomous `git add` must never be able to pick one of these up (CMX-280 and
    # self_check_experiments.json both landed on `dev` unnoticed; CMX-286 repeated it in its
    # own PR).
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", probe],
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"{probe} is not matched by .gitignore — the self-check scratch pattern is broken "
        "(git check-ignore exit code "
        f"{result.returncode}: {result.stderr.decode(errors='replace')!r})"
    )


# issue #502 rework round 1: `.chela-task-finished-request.json` — the completion-request
# marker `dispatcher._task_finished_request_path()` drops in a dispatched agent's own
# worktree — matched NEITHER of the two rules that existed for `.chela-*.json` files before
# this list: `.chela-self-check-*.json` needs the literal "self-check", and
# `.chela-judge-experiments.json` (itself never probed by a test until now — the same class
# of untested literal CMX-286/337 already burned this file for) is an exact, different name.
# So the marker showed up untracked in a REWORK agent's worktree — exactly the worktree that
# then commits from it. Both real filenames below are single-source, daemon/CLI-owned names
# (see the `.gitignore` comment), so — unlike the self-check family above — there is no
# "every spelling on record" list to keep: one glob, two real names to prove it against.
REAL_CHELA_MARKER_FILENAMES = [
    ".chela-judge-experiments.json",
    ".chela-task-finished-request.json",
    # issue #502 B2: `dispatcher._push_request_path()` — the push/PR-open request a
    # dispatched agent's `chela request-push` drops, same family, same reason.
    ".chela-push-request.json",
]


@pytest.mark.parametrize("probe", REAL_CHELA_MARKER_FILENAMES)
def test_gitignore_matches_a_real_chela_marker_filename(probe):
    # A rework agent's `git add <paths>` must never be able to pick one of these up — a
    # daemon-owned marker committed into a PR is either dead weight (the judge worktree) or,
    # worse, a stale completion request replayed on the next clone/checkout.
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", probe],
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"{probe} is not matched by .gitignore — the .chela-*.json marker pattern is broken "
        "(git check-ignore exit code "
        f"{result.returncode}: {result.stderr.decode(errors='replace')!r})"
    )
