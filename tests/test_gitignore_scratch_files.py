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
