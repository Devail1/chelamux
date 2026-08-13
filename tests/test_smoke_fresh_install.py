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
import socket
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


# A blind `DASH_PORT=$(( 20000 + (RANDOM % 20000) ))` guess (the pre-CMX-275 line, and the
# mutation the judge re-applied to prove this file didn't guard it) can ONLY ever produce a
# value in this band. Read straight from /proc rather than hardcoding "32768-60999" so this
# stays correct if a box's ephemeral range is ever configured differently.
GUESS_RANGE = range(20000, 40000)


def _ephemeral_port_range() -> tuple[int, int] | None:
    try:
        lo, hi = (int(x) for x in Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split())
    except (FileNotFoundError, ValueError):
        return None
    return lo, hi


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


def test_dash_port_comes_from_a_kernel_probe_not_an_arithmetic_guess():
    """🔴 WIRING guard (docs/DEFEAT_SHAPES.md #9). The judge re-applied the pre-CMX-275 line
    — `DASH_PORT=$(( 20000 + (RANDOM % 20000) ))` in place of the real `pick_free_port()`
    bind-to-0 probe — to a throwaway checkout of this branch and the full suite, including
    every other test in this file, STILL PASSED: nothing anywhere asserted where the port
    number actually came from, only that *some* dashboard eventually answered 200.

    A blind arithmetic guess can only ever land in GUESS_RANGE (20000-39999). The real
    kernel-assigned port is drawn from this box's ephemeral range
    (/proc/sys/net/ipv4/ip_local_port_range, default 32768-60999 on Linux) — wide enough
    that most draws land outside GUESS_RANGE entirely. So: call the script's `--print-port`
    fast path (the exact pick_free_port() function the real DASH_PORT= line calls, not a
    reimplementation that could quietly drift from it) repeatedly, and require at least one
    draw to land outside GUESS_RANGE. Under the mutation that is impossible — every draw is
    confined to GUESS_RANGE by construction, no matter how many samples are taken. Under the
    real code it is a near-certainty within a handful of samples.
    """
    ephemeral = _ephemeral_port_range()
    if ephemeral is not None:
        lo, hi = ephemeral
        outside_width = (hi - lo + 1) - max(0, min(hi, GUESS_RANGE.stop - 1) - max(lo, GUESS_RANGE.start) + 1)
        if outside_width <= 0:
            pytest.skip(
                f"this box's ephemeral port range ({lo}-{hi}) is entirely inside "
                f"{GUESS_RANGE.start}-{GUESS_RANGE.stop - 1} — a real kernel-assigned port "
                f"can never land outside GUESS_RANGE here, so this guard can't discriminate "
                f"a real probe from the arithmetic guess it replaces"
            )

    samples = []
    for _ in range(15):
        out = subprocess.run(
            ["bash", str(SCRIPT), "--print-port"],
            capture_output=True, text=True, timeout=10,
        )
        assert out.returncode == 0, out.stdout + out.stderr
        port = int(out.stdout.strip())
        # "genuinely bindable at that moment" — pick_free_port() released this exact port
        # right before printing it, so binding it here (immediately, before anything else on
        # the box can grab it) must succeed. A stale/fabricated value would not reliably do
        # this.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        samples.append(port)

    outside_guess_range = [p for p in samples if p not in GUESS_RANGE]
    assert outside_guess_range, (
        f"all {len(samples)} samples from `--print-port` landed inside "
        f"{GUESS_RANGE.start}-{GUESS_RANGE.stop - 1} — exactly the range a blind "
        f"`20000 + (RANDOM % 20000)` guess is confined to: {samples}. Either DASH_PORT "
        f"stopped calling a real bind-to-0 probe, or (see _ephemeral_port_range()) this "
        f"box's ephemeral range doesn't extend past that band."
    )


def test_dash_port_wiring_calls_the_real_probe_function():
    """Static half of the CMX-275 WIRING guard (docs/DEFEAT_SHAPES.md #9) — pairs with
    test_dash_port_comes_from_a_kernel_probe_not_an_arithmetic_guess just above, which drives
    pick_free_port() through the script's `--print-port` mode: a SEPARATE call site from the
    one `chela dashboard` actually uses (step 3's `DASH_PORT=$(pick_free_port)`). A mutation
    that reverts only THAT assignment back to the arithmetic guess — leaving pick_free_port()
    itself, and `--print-port`'s own call to it, untouched — would defeat the behavioral test
    without this one (docs/DEFEAT_SHAPES.md shape 7: two callers, one guarded).

    This is a bare source-text match, the shape shape 1 in that same catalog warns is weak —
    but not for the same reason it's weak there: shape 1 is defeated by wrapping a statement
    in dead code (`if (false && ...)`) so it never runs while the substring survives. That
    doesn't apply to a bare variable assignment under this script's own `set -euo pipefail`:
    there is no way to dead-code `DASH_PORT=$(pick_free_port)` without either leaving it
    calling the real probe or making `$DASH_PORT` unbound, which crashes the rest of the
    script outright — a failure every other test in this file already catches. An exact
    literal match on this one line is therefore the strong form for this specific mutation
    target, not the weak one.
    """
    source = SCRIPT.read_text()
    assert re.search(r"^DASH_PORT=\$\(pick_free_port\)\s*$", source, re.MULTILINE), (
        "the production DASH_PORT= assignment (step 3, right before `chela dashboard` "
        "starts) no longer calls pick_free_port() — either it was reverted to something "
        "else (e.g. the pre-CMX-275 `$(( 20000 + (RANDOM % 20000) ))` guess) or renamed. "
        "test_dash_port_comes_from_a_kernel_probe_not_an_arithmetic_guess above only "
        "exercises the separate `--print-port` call site, so it would not catch this on "
        "its own:\n" + source
    )
