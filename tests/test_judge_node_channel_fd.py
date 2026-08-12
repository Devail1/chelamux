"""⚖️ CMX-252 — a judge spawned into a tmux window inherits ``NODE_CHANNEL_FD``, and every
JS suite it runs fails for a reason that has nothing to do with the PR under judgment.

⛔ THE BUG THIS FILE EXISTS FOR (measured 2026-08-12). ``NODE_CHANNEL_FD`` is Node's own
IPC-channel marker: a fd number a parent ``child_process.fork`` leaves behind for the child
to reconnect on. pm2 forks its managed processes through Node's ``fork`` (IPC channel
included) even when the target is not a Node program at all, so the fd number ends up in the
environment of the tmux session pm2 ultimately starts — and every window `_new_window`
creates in it, including a judge's. When that inherited fd number happens to resolve to
something OPEN but wrong in a freshly spawned ``node`` process — stdin reproduces it exactly
— Node aborts (SIGABRT) before running a single test:

    NODE_CHANNEL_FD=0 node --test some.test.mjs   # aborts, core dump, before "TAP version 13"

``judge.test_cmd`` (``CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q``) shells out to exactly this
via ``tests/test_js_suites.py``'s ``node --test`` per-file discovery, so one leaked env var
takes down EVERY JS suite in the same run — the baseline goes red for a reason that is the
judge's own box, not the PR, and every PR the judge looks at comes back ``cannot_verify``.

The fix is two independent env scrubs, because the two code paths that spawn ``node`` don't
share a caller: :func:`chela.judge._no_color_env` (the top-level ``test_cmd`` the judge
runs) and ``tests/test_js_suites.py``'s own ``node --test`` invocation (which runs the same
way under a bare ``uv run pytest -q``, whether or not a judge is involved at all).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chela import judge

ROOT = Path(__file__).resolve().parent.parent

# A fd that is guaranteed OPEN in a freshly spawned child (stdin) but is not a working IPC
# pipe/socket — the exact condition that reproduces the SIGABRT live, independent of what
# fd number a real pm2/tmux ancestry happens to leak.
_POISON_FD = "0"


def test_no_color_env_strips_node_channel_fd(monkeypatch):
    monkeypatch.setenv("NODE_CHANNEL_FD", _POISON_FD)
    assert "NODE_CHANNEL_FD" not in judge._no_color_env()


def test_no_color_env_strips_node_channel_serialization_mode(monkeypatch):
    """The sibling var Node sets alongside the fd. Inert on its own once the fd is gone —
    so a BEHAVIOURAL guard (does `node --test` survive?) can never tell a scrub that pops
    only the fd apart from one that pops both. Only a direct check of the returned env dict
    can, which is what this is: set ONLY the serialization-mode var (not the fd) so a
    regression that dropped this half of the scrub is caught even when the fd happens to be
    absent from the caller's own environment."""
    monkeypatch.delenv("NODE_CHANNEL_FD", raising=False)
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    assert "NODE_CHANNEL_SERIALIZATION_MODE" not in judge._no_color_env()


def test_no_color_env_is_a_no_op_when_node_channel_fd_is_absent(monkeypatch):
    """⭐ COUNTERWEIGHT. A box that never had the leak keeps behaving exactly as before —
    this is a targeted pop, not a broader env rebuild that could drop something real."""
    monkeypatch.delenv("NODE_CHANNEL_FD", raising=False)
    env = judge._no_color_env()
    assert "NODE_CHANNEL_FD" not in env
    assert env["NO_COLOR"] == "1"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_run_suite_survives_a_leaked_node_channel_fd(monkeypatch):
    """The real thing, offline: the exact `node --test` invocation `judge.test_cmd` drives,
    with the fd that reproduces the abort actually set in this process's own environment —
    proving `run_suite` (which env's through `_no_color_env`) does not hand it down."""
    monkeypatch.setenv("NODE_CHANNEL_FD", _POISON_FD)
    result = judge.run_suite("node --test tests/cost.test.mjs", ROOT, timeout=60)
    assert result.ok, "the suite subprocess itself could not even be started/collected"
    assert result.exit_code == 0, (
        f"`node --test` did not survive a leaked NODE_CHANNEL_FD (exit {result.exit_code}): "
        f"{result.tail[-500:]}"
    )


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_test_js_suite_itself_survives_a_leaked_node_channel_fd(monkeypatch):
    """Calls the REAL `test_js_suite` test function — not a parallel reproduction — with
    NODE_CHANNEL_FD poisoned. A regression that drops `_clean_env()` from that call site's
    `subprocess.run` (e.g. reverted back to inheriting the full environment) makes THIS
    fail, because that call site is exactly what runs here, unlike a copy of its logic."""
    import test_js_suites

    monkeypatch.setenv("NODE_CHANNEL_FD", _POISON_FD)
    test_js_suites.test_js_suite(ROOT / "tests" / "cost.test.mjs")


def test_clean_env_strips_node_channel_serialization_mode_too(monkeypatch):
    """Same reasoning as `_no_color_env`'s counterpart above, for the OTHER `node --test`
    call site (`tests/test_js_suites.py`'s own subprocess.run). Inert on its own once the
    fd is gone, so only a direct env-dict check — not a behavioural one — can tell a
    one-var scrub apart from the two-var scrub this call site is supposed to do."""
    import test_js_suites

    monkeypatch.delenv("NODE_CHANNEL_FD", raising=False)
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    assert "NODE_CHANNEL_SERIALIZATION_MODE" not in test_js_suites._clean_env()


def test_clean_env_is_a_targeted_pop_not_an_environment_rebuild(monkeypatch):
    """⛔ CMX-260 lift, closing PR #321's round 6 finding 1 — the one finding the
    orchestrator called out by name when re-scoping this ticket, because it was the exact
    negative control asked for in round-1 review and still missing five rounds later: a
    "scrub" reimplemented as `{k: v for k, v in os.environ.items() if k not in (...)}` with
    PATH/HOME in that exclusion tuple drops them from every `node --test` child's
    environment — and every OTHER test of `_clean_env` passes, because they only assert the
    two IPC vars are ABSENT, which an empty dict satisfies best of all.

    Guards the positive half (the two IPC vars are gone) and the negative half (PATH/HOME
    still reach the child) in ONE test, per the orchestrator's own generalised instruction:
    "where a finding has a positive and a negative half, guard BOTH in one test."
    """
    import test_js_suites

    monkeypatch.setenv("NODE_CHANNEL_FD", _POISON_FD)
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/example")

    env = test_js_suites._clean_env()

    assert "NODE_CHANNEL_FD" not in env
    assert "NODE_CHANNEL_SERIALIZATION_MODE" not in env
    assert env.get("PATH") == "/usr/bin:/bin", "PATH must still reach the node child"
    assert env.get("HOME") == "/home/example", "HOME must still reach the node child"
