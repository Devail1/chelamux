"""The repo's own `.gitignore` must actually match the patterns it claims to — a
syntactically valid line that never matches anything is indistinguishable from no line at
all until something asks git.

CMX-286 round 2: the judge mutated `.chela-self-check-*.json` to
`.chela-self-check-DISABLED-*.json` in a throwaway checkout of this PR's head — still a
valid gitignore pattern, still parses — and the full suite stayed green, because nothing
anywhere drove `git check-ignore` against a real `.chela-self-check-<task>.json` name. See
docs/DEFEAT_SHAPES.md #26.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_gitignore_matches_a_real_self_check_scratch_filename():
    # WORKFLOW.md step 6 writes `.chela-self-check-<task-id>.json` at the repo root; an
    # autonomous `git add` must never be able to pick it up (CMX-280 landed one on `dev`
    # unnoticed; CMX-286 repeated it in its own PR).
    probe = ".chela-self-check-cmx286.json"
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", probe],
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"{probe} is not matched by .gitignore — the self-check scratch pattern is broken "
        "(git check-ignore exit code "
        f"{result.returncode}: {result.stderr.decode(errors='replace')!r})"
    )
