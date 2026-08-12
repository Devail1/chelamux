"""scripts/smoke-fresh-install.sh — CMX-263: an adopter fresh-install smoke test.

Every install anyone has run `chela update`/`chela doctor` on predates months of changes
— "it worked when I set it up" is not evidence about current `dev`/`main`. Nobody had ever
proven, end to end, that a brand-new clone can `uv sync` and run both commands without
crashing. These tests run the real script against a real (local, offline) clone of this
checkout — no mocked git, no mocked `uv` — the same contract `tests/test_update.py` holds
for `chela.update` itself.

The isolation test pins a real bug hit while building this: the script's first draft only
redirected `CHELA_DIR`, so on a box that already runs a live chela install (this project's
own dev machine, notably) the "fresh" run still inherited the calling shell's
`CHELA_DISPATCH_WORKFLOWS` and printed that developer's actual dispatched-repo paths —
exactly the "reads live state" bug class `tests/test_isolation.py` exists to catch, one
level up (a subprocess's inherited environment rather than an unredirected `CHELA_DIR`).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "smoke-fresh-install.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("git") is None,
    reason="uv and git are both required to run a real fresh-install clone + sync",
)

# Every step the script is supposed to run, keyed by the exact "==> ..." line it prints
# when (and only when) it actually executes. A step that gets commented out / turned into
# a no-op (e.g. `: run_step "chela doctor" doctor`) still lets the rest of the script pass
# — nothing else notices a skipped step — so this is the guard against exactly that: it is
# read straight off run_step()'s own echo, not inferred from the overall exit code.
#
# Each pattern is matched against a WHOLE line of stdout (re.fullmatch), never as a
# substring of the concatenated output — "==> chela update" is itself a prefix of the
# PRECEDING step's line, "==> chela update --check", so a substring check lets a no-op'd
# `run_step "chela update" update` (e.g. `: run_step ...`) pass unnoticed, fully satisfied
# by the --check step's own echo. Only the dashboard step has a variable suffix (the
# isolated port number), hence the regex; every other pattern is an exact literal line.
EXPECTED_STEPS = [
    re.escape("==> uv sync --all-extras"),
    re.escape("==> chela status (verifies the CHELA_TMUX_SESSION pin took effect)"),
    re.escape("==> chela plugin --dir (documented offline-render path)"),
    re.escape("==> chela dashboard (background, isolated port ") + r"\d+\)",
    re.escape("==> chela doctor"),
    re.escape("==> chela update --check"),
    re.escape("==> chela update"),
    re.escape("==> chela dispatch --dry-run (fixture tracker)"),
]

# The exact shape of the pin set in the script: `smoke-fresh-install-$$-nonexistent`. `chela
# status` prints it back verbatim via config.current_session(), so this is checked against
# the RESOLVED value chela actually used — not the literal export line in the script, which
# a corruption could leave untouched while still breaking what it resolves to (e.g. exporting
# an empty string, which `current_session()` treats as unset and falls through to $TMUX_PANE
# or the "chela" default — exactly the mirror-session leak this pin exists to prevent).
PINNED_SESSION_RE = re.compile(r"tmux session '(smoke-fresh-install-\d+-nonexistent)'")

# Mirrors the `not_covered` array literal in scripts/smoke-fresh-install.sh. This is the
# scope-boundary disclosure the reviewer required the PR to make: what a green run does
# NOT prove (credential-gated paths that must not be faked to claim coverage). The script's
# `for item in "${not_covered[@]}"` loop still runs — printing nothing — under `set -u`
# even if the array is collapsed to `not_covered=()`, so only asserting on the printed
# items (not just the header line, which prints unconditionally either way) catches that:
# a green run would otherwise claim to cover strictly more than it does.
NOT_COVERED_ITEMS = [
    "interactive plugin install (/plugin marketplace add, /plugin install chela@chela — "
    "Claude Code REPL slash commands, no headless equivalent)",
    "a live dispatched agent launch (needs real Claude Code credentials)",
    "a judge run",
    "a real merge",
]


def _run(*, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ROOT)],
        capture_output=True, text=True, timeout=600, env=env,
    )


def test_passes_on_a_real_fresh_clone_of_this_checkout():
    """The load-bearing one: a genuinely fresh `git clone` + `uv sync --all-extras` +
    `chela doctor` + `chela update --check` + `chela update` must all run to completion
    without an uncaught exception — an install nobody has verified end to end since months
    of changes landed."""
    out = _run(env=dict(os.environ))

    assert "Traceback (most recent call last):" not in out.stdout, out.stdout
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS: fresh-install smoke test" in out.stdout

    # Whole-line match (re.fullmatch), not substring: see the EXPECTED_STEPS comment above
    # for why a substring check is unsafe here (the "chela update" / "chela update --check"
    # prefix collision).
    stdout_lines = out.stdout.splitlines()
    for pattern in EXPECTED_STEPS:
        assert any(re.fullmatch(pattern, line) for line in stdout_lines), (
            f"step matching {pattern!r} never ran as its own output line (its run_step() "
            f"echo is missing, or only appears as a prefix of a different step's line) — "
            f"a no-op'd or skipped step doesn't fail the overall exit code, so this is "
            f"the only thing that would catch it:\n{out.stdout}"
        )

    match = PINNED_SESSION_RE.search(out.stdout)
    assert match, (
        "chela never reported the guaranteed-nonexistent pinned tmux session name — "
        "either the CHELA_TMUX_SESSION export was neutered (e.g. set to '', which "
        "config.current_session() treats as unset) or the status step didn't run:\n"
        + out.stdout
    )

    # The scope-boundary disclosure: a green run must still say what it does NOT prove.
    # The header line alone is not enough to assert on — it prints unconditionally even if
    # the `not_covered` array is collapsed to `=()` — so this checks every item actually
    # got printed, which is the only thing a `not_covered=()` corruption stops from being
    # true.
    assert (
        "==> NOT COVERED by this smoke test (see SCOPE BOUNDARY at the top of this file):"
        in out.stdout
    ), out.stdout
    for item in NOT_COVERED_ITEMS:
        assert f"  - {item}" in out.stdout, (
            f"the NOT COVERED scope-boundary disclosure is missing {item!r} — a future "
            f"edit could silently empty the `not_covered` array (the header line alone "
            f"still prints under `set -u` with zero loop iterations) and this green run "
            f"would then claim to cover strictly more than it does:\n{out.stdout}"
        )


def test_a_real_traceback_from_dispatch_dry_run_fails_the_run():
    """🔴 Pins the traceback scan itself. `chela.workflow.load_workflow` raises an
    uncaught `ValueError` on a WORKFLOW.md missing `project_key` — a genuine Python
    traceback, not a simulated one. SMOKE_BREAK_DISPATCH_WORKFLOW=1 makes the script write
    exactly that fixture instead of a valid one (see scripts/smoke-fresh-install.sh). If the
    traceback scan in run_step() is neutered (e.g. `if false && grep -q ...`), this crash is
    indistinguishable from a clean run and the script wrongly reports PASS."""
    env = dict(os.environ)
    env["SMOKE_BREAK_DISPATCH_WORKFLOW"] = "1"

    out = _run(env=env)

    assert "Traceback (most recent call last):" in out.stdout, out.stdout
    # run_step()'s FAIL line goes to stderr (`>&2`) — the traceback it's reacting to is on
    # stdout (echoed from the captured `2>&1` subprocess output), so both streams matter.
    assert "FAIL: chela dispatch --dry-run (fixture tracker) crashed" in out.stderr, out.stderr
    assert out.returncode == 1, out.stdout + out.stderr
    assert "PASS: fresh-install smoke test" not in out.stdout


def test_a_clean_nonzero_exit_above_one_fails_the_run():
    """🔴 Pins the OTHER half of run_step()'s "ran vs. crashed" contract — the `rc -gt 1`
    branch, right next to the traceback scan. `chela dispatch --pause --ttl not-a-duration`
    is real production code (chela.hold.parse_ttl raises ValueError, cmd_dispatch_hold
    catches it and does `raise SystemExit(2)`): it runs to completion, prints a clean
    `error: --ttl ...` message, and exits 2 — no traceback anywhere. SMOKE_BREAK_HOLD_TTL=1
    makes the script run exactly that as an extra step. If the `[ "$rc" -gt 1 ]` branch is
    neutered (e.g. `if false && [ "$rc" -gt 1 ]`), this clean-but-bad exit is indistinguishable
    from success and the script wrongly reports PASS."""
    env = dict(os.environ)
    env["SMOKE_BREAK_HOLD_TTL"] = "1"

    out = _run(env=env)

    assert "==> chela dispatch --pause (bad --ttl, exercises rc>1)" in out.stdout, out.stdout
    assert "Traceback (most recent call last):" not in out.stdout, out.stdout
    assert "error: --ttl not a duration" in out.stdout, out.stdout
    assert "FAIL: chela dispatch --pause (bad --ttl, exercises rc>1) exited 2" in out.stderr, (
        out.stdout + out.stderr
    )
    assert out.returncode == 1, out.stdout + out.stderr
    assert "PASS: fresh-install smoke test" not in out.stdout


def test_a_dashboard_that_never_answers_200_fails_the_run():
    """🔴 Pins the dashboard readiness loop's own success comparison. SMOKE_BREAK_DASHBOARD=1
    makes the script occupy the dashboard's isolated port with a bound-but-unlistened socket
    *before* `chela dashboard` starts, so its own `app.run()` bind raises a genuine
    `OSError: Address already in use` (a real crash, not a simulated one) and the dashboard
    process dies without ever answering anything — every curl probe in the readiness loop
    gets an instant connection-refused ("000"), never "200". If the loop's `= "200"`
    comparison is neutered into something that is true for whatever curl happens to print
    (e.g. `!= "<sentinel>"`, true for "000"), the very first failed probe is wrongly treated
    as success and the script reports PASS with a dead dashboard."""
    env = dict(os.environ)
    env["SMOKE_BREAK_DASHBOARD"] = "1"

    out = _run(env=env)

    assert "==> chela dashboard (background, isolated port" in out.stdout, out.stdout
    assert "chela dashboard: started, /api/agents -> 200" not in out.stdout, out.stdout
    assert (
        "FAIL: chela dashboard did not answer /api/agents with 200 within 30s" in out.stderr
    ), out.stdout + out.stderr
    assert out.returncode == 1, out.stdout + out.stderr
    assert "PASS: fresh-install smoke test" not in out.stdout


def test_strips_inherited_chela_env_so_a_live_install_never_leaks_in():
    """A calling shell that already runs chela for real (CHELA_DISPATCH_WORKFLOWS pointing
    at real repos, as this project's own dev machine does) must not have any of that leak
    into what is supposed to simulate a brand-new adopter's empty environment. Corrupt this
    by deleting the script's `unset` loop and the sentinel path below reappears verbatim in
    `chela doctor`'s "dispatch workflow ... does not exist" finding."""
    sentinel = "/nonexistent-sentinel-repo-cmx263/WORKFLOW.md"
    env = dict(os.environ)
    env["CHELA_DISPATCH_WORKFLOWS"] = sentinel
    env["CHELA_TMUX_SESSION"] = "definitely-not-a-real-session-cmx263"

    out = _run(env=env)

    assert sentinel not in out.stdout, (
        "a CHELA_* var inherited from the calling shell leaked into the fresh-install run:\n"
        + out.stdout
    )
    assert "definitely-not-a-real-session-cmx263" not in out.stdout
