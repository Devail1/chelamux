"""🔌 CMX-252 — chela's ONE window-spawn choke point (`_new_window`) scrubs Node's leaked
IPC-channel vars from tmux's GLOBAL environment before every agent/judge window is created.

Objective 1 of the CMX-252 ticket, closing the gap the round-1 PR left open: that PR only
scrubbed the two known call sites that shell out to `node --test` (`judge.test_cmd` via
`chela/judge.py`'s `_no_color_env`, and `tests/test_js_suites.py`'s own `node --test`
invocation). Neither touches the LAUNCH path — `_new_window`, the single place chela
creates any agent or judge tmux window — so a leaked `NODE_CHANNEL_FD` (pm2 forks its
managed processes through Node's own `child_process.fork`, IPC channel included, even for
a non-Node target — so `chela-daemon` itself, and any tmux server started under its
ancestry, can carry it) still poisons every OTHER node invocation a spawned window makes:
an agent running `npm`, a build step, any future tooling. `tmux set-environment -gu` acts
on the SERVER's global environment table (not merely the current session), which is what
new windows in ANY session inherit from — the same primitive a human used by hand
(`tmux set-environment -gu NODE_CHANNEL_FD`) to clean up live, but that fix dies with the
tmux server unless something re-applies it. Calling it before every `_new_window` spawn
makes it survive a server recreated later by another node-parented ancestor.
"""
from __future__ import annotations

from types import SimpleNamespace


def _capture_tmux_calls(monkeypatch, dispatcher):
    calls: list[list[str]] = []

    def fake_run(argv, *a, **k):
        calls.append(list(argv))
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@100\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    return calls


def test_new_window_scrubs_node_ipc_vars_before_creating_the_window(monkeypatch):
    """🔴 Mutate `_new_window` to skip `_scrub_node_ipc_env()` (or call it after
    `new-window`) and this goes red: the two `set-environment -gu` calls disappear, or land
    too late to matter for the window they were meant to protect."""
    import chela.dispatcher as dispatcher

    calls = _capture_tmux_calls(monkeypatch, dispatcher)
    wid = dispatcher._new_window("agent-1", "/tmp/somewhere")

    assert wid == "@100"
    scrub_calls = [c for c in calls if c[:3] == ["tmux", "set-environment", "-gu"]]
    scrubbed_vars = {c[3] for c in scrub_calls}
    assert scrubbed_vars == {"NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE"}, (
        "the launch path must scrub exactly the two known leaked Node IPC vars"
    )

    new_window_idx = next(i for i, c in enumerate(calls) if c[:2] == ["tmux", "new-window"])
    assert all(calls.index(c) < new_window_idx for c in scrub_calls), (
        "the scrub must land on tmux's global environment BEFORE `new-window` — a scrub "
        "that ran after would be too late for the window it just inherited from"
    )


def test_new_window_scrub_touches_nothing_else(monkeypatch):
    """⭐ COUNTERWEIGHT / negative control. Pins the scrub to being exactly two targeted
    `set-environment -gu <var>` calls naming exactly the two known Node IPC vars — never a
    broader environment reset that could also clear something a spawned window needs
    (PATH, HOME, CHELA_TMUX_SESSION, ...). A "fix" that widens the scrub into a general
    environment wipe passes the test above (the two vars are still gone) but fails this."""
    import chela.dispatcher as dispatcher

    calls = _capture_tmux_calls(monkeypatch, dispatcher)
    dispatcher._new_window("agent-1", "/tmp/somewhere")

    set_env_calls = [c for c in calls if c[:2] == ["tmux", "set-environment"]]
    assert len(set_env_calls) == 2
    for call in set_env_calls:
        assert call[2] == "-gu", (
            "must be `-gu` (global, unset) — a plain `-u` only clears the CURRENT "
            "session's copy, leaving other sessions on the same poisoned tmux server "
            "(and the next window spawned in THEM) still exposed"
        )
        assert call[3] in dispatcher._NODE_IPC_ENV_VARS
    named = {call[3] for call in set_env_calls}
    assert "PATH" not in named
    assert "HOME" not in named
