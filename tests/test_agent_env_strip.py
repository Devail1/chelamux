"""📵🔒 CMX-115 / 🧾 CMX-45 — a dispatched agent/judge pane keeps an ALLOWLISTED env.

The daemon carries `CHELA_NOTIFY_URL` (`~/.chela/chela.env`) so PRODUCTION can push real
phone notifications. `_launch_agent` spawns every agent AND judge window inside the same
tmux session, which inherits that same environment — so without a strip, any agent that
runs inbox/notify code (cmx-113 did), a manual repro, or `chela` command reaching
`notify.send` pushes a REAL ntfy to the human's phone with whatever test fixture happens
to be sitting in the worktree. `_spawn_judge` is exposed the same way because it calls
`_launch_agent` too — there is no second launch path to fix.

CMX-45 turned the one-name strip-list into an allowlist: every inherited name (the daemon's
env + the tmux server's tables) that is not on `envutil.AGENT_ENV_ALLOW` is unset, so a
daemon-side setting nobody thought to list never reaches an agent either. The guard below
runs the EXACT lines `_launch_agent` types into the pane through a real `bash`.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from chela.workflow import WorkflowDef

SENTINEL = "CHELA_CMX45_SENTINEL"
TMUX_ONLY_SENTINEL = "CMX45_TMUX_GLOBAL_ONLY"
SESSION_ONLY_SENTINEL = "CMX45_TMUX_SESSION_ONLY"
# `_capture_send_keys` patches the shared `subprocess` module; the replay needs the real one.
_REAL_RUN = subprocess.run


def _wf(tmp_path):
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "agent": {}},
        prompt_template="go {{workspace_path}}",
    )


def _conn(dispatcher):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _capture_send_keys(monkeypatch, dispatcher, tmux_global: str = "",
                       tmux_session: str = ""):
    """Fake `subprocess.run` for `_launch_agent`'s tmux calls; records every send-keys line.
    ``tmux_global`` is what `tmux show-environment -g` reports and ``tmux_session`` what
    `tmux show-environment -t <TMUX_SESSION>` reports (``KEY=value`` lines) — two separate
    tables tmux merges into a new window, so each is fed on its own."""
    sent: list[str] = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@100\n", returncode=0)
        if argv[:3] == ["tmux", "show-environment", "-g"]:
            return SimpleNamespace(stdout=tmux_global, returncode=0)
        if argv[:4] == ["tmux", "show-environment", "-t", dispatcher.TMUX_SESSION]:
            return SimpleNamespace(stdout=tmux_session, returncode=0)
        if argv[:2] == ["tmux", "send-keys"] and len(argv) > 4:
            sent.append(argv[4])
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    return sent


def _launch(monkeypatch, dispatcher, tmp_path, tmux_global: str = "",
            tmux_session: str = "", **kwargs):
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher, tmux_global, tmux_session)
    wf = _wf(tmp_path)
    conn = _conn(dispatcher)
    dispatcher._launch_agent(
        wf, "t1", "cmx-1", tmp_path / "wt", "go", conn,
        hook_vars={}, fresh_worktree=False, **kwargs,
    )
    return sent


def _unset_names(sent: list[str]) -> list[str]:
    return next(line for line in sent if line.startswith("unset")).split()[1:]


def _run_in_pane(sent: list[str], pane_env: dict[str, str]) -> dict[str, str]:
    """Replay the lines `_launch_agent` typed into the pane through a real non-interactive
    bash born with ``pane_env``, the agent command swapped for `env -0` so we read what the
    agent process would have inherited."""
    script = "\n".join("env -0" if line.startswith("claude") else line for line in sent)
    out = _REAL_RUN(["bash", "--norc", "--noprofile", "-c", script],
                         env=pane_env, capture_output=True, check=True)
    pairs = (item.partition("=") for item in out.stdout.decode().split("\0") if item)
    return {k: v for k, _, v in pairs}


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs a real bash")
def test_a_daemon_var_is_absent_in_the_agent_pane_but_chela_wid_and_path_survive(
        monkeypatch, tmp_path):
    """🔴 THE CMX-45 GUARD. A sentinel set in the DAEMON env — a name no strip-list knows —
    must be gone in the pane the agent starts from, while `CHELA_WID` (exported after the
    strip) and `PATH` are still there. Revert `_strip_agent_env_cmd` to the old
    `unset CHELA_NOTIFY_URL` strip-list and the sentinel survives ⇒ RED."""
    import chela.dispatcher as dispatcher

    monkeypatch.setenv(SENTINEL, "1")
    monkeypatch.setenv("CHELA_NOTIFY_URL", "https://ntfy.invalid/topic")
    sent = _launch(monkeypatch, dispatcher, tmp_path,
                   tmux_global=f"{TMUX_ONLY_SENTINEL}=1\n-CMX45_REMOVED\n",
                   tmux_session=f"{SESSION_ONLY_SENTINEL}=1\n")

    pane_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CHELA_TMUX_SESSION": "chela",
        SENTINEL: "1",
        TMUX_ONLY_SENTINEL: "1",
        SESSION_ONLY_SENTINEL: "1",
        "CHELA_NOTIFY_URL": "https://ntfy.invalid/topic",
    }
    agent_env = _run_in_pane(sent, pane_env)

    assert SENTINEL not in agent_env, "a daemon-env var reached the agent pane"
    assert TMUX_ONLY_SENTINEL not in agent_env, "a tmux-global var reached the agent pane"
    # The SESSION table is a second source tmux merges into a new window (`set-environment
    # -t chela`, or update-environment on attach) — a name only it holds must go too.
    assert SESSION_ONLY_SENTINEL not in agent_env, "a tmux-session var reached the agent pane"
    assert "CHELA_NOTIFY_URL" not in agent_env
    assert agent_env.get("CHELA_WID") == "@100"
    assert agent_env.get("PATH") == os.environ["PATH"]
    assert agent_env.get("CHELA_TMUX_SESSION") == "chela"


def test_launch_agent_strips_notify_url_before_the_agent_command(monkeypatch, tmp_path):
    """🔴 The `unset` line must land in the window BEFORE `claude …` is typed — a strip that
    ran after the agent started would already be too late for the process it launched."""
    import chela.dispatcher as dispatcher

    monkeypatch.setenv("CHELA_NOTIFY_URL", "https://ntfy.invalid/topic")
    sent = _launch(monkeypatch, dispatcher, tmp_path)

    unset_idx = next(i for i, line in enumerate(sent) if line.startswith("unset"))
    claude_idx = next(i for i, line in enumerate(sent) if line.startswith("claude"))
    assert unset_idx < claude_idx
    assert "CHELA_NOTIFY_URL" in sent[unset_idx].split()


def test_the_allowlist_keeps_what_the_agent_needs(monkeypatch, tmp_path):
    """🔴 Not a blanket wipe: PATH/HOME, chela's own session + DB location, Claude Code's and
    gh's credentials and an operator-forwarded name all survive; a pm2 bookkeeping name, an
    unforwarded secret and a Claude session marker all go."""
    import chela.dispatcher as dispatcher

    keep = ("PATH", "HOME", "CHELA_TMUX_SESSION", "CHELA_DIR", "ANTHROPIC_API_KEY",
            "GH_TOKEN", "LC_ALL", "XDG_RUNTIME_DIR", "CMX45_OPERATOR_FORWARD")
    drop = ("CHELA_DASHBOARD_PORT", "pm_id", "LINEAR_API_KEY", "CLAUDE_CODE_SESSION_ID",
            "UV_PUBLISH_TOKEN", "CHELA_ACTOR")
    for name in (*keep, *drop):
        monkeypatch.setenv(name, os.environ.get(name, "x"))
    monkeypatch.setenv("CHELA_CHILD_ENV_FORWARD", "CMX45_OPERATOR_FORWARD")

    stripped = set(_unset_names(_launch(monkeypatch, dispatcher, tmp_path)))
    assert stripped.isdisjoint(keep), sorted(stripped & set(keep))
    assert stripped >= set(drop), sorted(set(drop) - stripped)


def test_notify_url_is_dropped_even_when_an_operator_forwards_it(monkeypatch, tmp_path):
    """🔴 CMX-115 outranks the forward list: the daemon's real notifier never reaches a pane."""
    import chela.dispatcher as dispatcher

    monkeypatch.setenv("CHELA_NOTIFY_URL", "https://ntfy.invalid/topic")
    monkeypatch.setenv("CHELA_CHILD_ENV_FORWARD", "CHELA_NOTIFY_URL")
    assert "CHELA_NOTIFY_URL" in _unset_names(_launch(monkeypatch, dispatcher, tmp_path))


def test_the_unset_line_names_only_shell_variables(monkeypatch, tmp_path):
    """A pm2 key like `chela-daemon` is not a shell name — `unset` would error on it. It is
    left to the tmux-table scrub (below), and never typed into the pane."""
    import chela.dispatcher as dispatcher

    sent = _launch(monkeypatch, dispatcher, tmp_path,
                   tmux_global="chela-daemon=x\nstatus=online\n")
    names = _unset_names(sent)
    assert "chela-daemon" not in names
    assert "status" in names


def test_non_shell_names_are_scrubbed_from_the_tmux_global_table():
    """🔴 bash passes a `chela-daemon`-shaped env entry to every child and cannot unset it,
    so the only place to drop it is the tmux server's global table before the window opens."""
    from chela import envutil

    out = "chela-daemon=x\nPATH=/bin\nstatus=online\n"
    assert envutil.tmux_scrub_names(out, forward=frozenset()) == ["chela-daemon"]


def test_notify_is_disabled_once_the_launch_env_strips_the_url(monkeypatch, tmp_path):
    """🔴 Ties the strip to the actual gate `notify.send` checks: an agent/judge process
    booting under the stripped env must see `notify.enabled() is False`."""
    import chela.dispatcher as dispatcher
    import chela.notify as notify

    monkeypatch.setenv("CHELA_NOTIFY_URL", "https://ntfy.invalid/topic")
    stripped_vars = _unset_names(_launch(monkeypatch, dispatcher, tmp_path))

    daemon_env = {"CHELA_NOTIFY_URL": "https://ntfy.invalid/topic"}
    agent_env = dict(daemon_env)
    for var in stripped_vars:
        agent_env.pop(var, None)

    monkeypatch.setattr(notify, "NOTIFY_URL", agent_env.get("CHELA_NOTIFY_URL", ""))
    assert notify.enabled() is False


def test_spawn_judge_strips_notify_url_too(monkeypatch, tmp_path):
    """🔴 `_spawn_judge` calls `_launch_agent` — same wiring, same exposure (the judge runs
    the FULL pytest suite, and any test reaching `notify.send` unmocked would push real)."""
    import chela.dispatcher as dispatcher

    monkeypatch.setenv("CHELA_NOTIFY_URL", "https://ntfy.invalid/topic")
    monkeypatch.setenv(SENTINEL, "1")
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)

    wf = _wf(tmp_path)
    conn = _conn(dispatcher)
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status, branch_name, "
        "task_number, pr_url) VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?)",
        ("abc123", str(wf.path), "do a thing", "cmx-1", 1, "https://x/pull/1"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM runs WHERE task_id=?", ("abc123",)).fetchone()

    monkeypatch.setattr(dispatcher, "detached_worktree",
                         lambda *_a, **_k: (tmp_path / "judge-abc123", True))
    assert dispatcher._spawn_judge(wf, row, "deadbeef", conn) is True

    unset_idx = next(i for i, line in enumerate(sent) if line.startswith("unset"))
    claude_idx = next(i for i, line in enumerate(sent) if line.startswith("claude"))
    assert unset_idx < claude_idx
    assert {"CHELA_NOTIFY_URL", SENTINEL} <= set(sent[unset_idx].split())


def test_the_suite_itself_cannot_push_a_real_notification(monkeypatch):
    """🔴 THE FENCE. CMX-115 (above) strips `CHELA_NOTIFY_URL` from every tmux-spawned
    agent and judge — but NOT from a maintainer running `pytest` or `chela judge run` in
    their own shell, which is how this suite is normally run by hand. That gap paged the
    operator 109 times on 2026-09-01, from
    `test_auto_apply_sweep_never_calls_apply_with_a_bespoke_repo_arg` — which drives the
    real `auto_apply_sweep()` and, alone among its three siblings, never stubbed `notify`.

    conftest's `_no_live_notifications` shuts both doors. This pins both halves: delete
    the `NOTIFY_URL` blanking and the first assert goes red on any machine with a notifier
    configured; delete the `_post` guard and the second does, everywhere.
    """
    import pytest

    import chela.notify as notify

    # Door 1: every `if notify.enabled()` call site is gated off, even though the
    # operator's real environment has CHELA_NOTIFY_URL set.
    assert notify.enabled() is False, (
        "conftest did not blank NOTIFY_URL — every `if notify.enabled():` call site in the "
        "product is live, and the suite can page the operator"
    )

    # Door 2: and a test that sets its own URL still cannot reach the network. `send()`
    # wraps its transport in `except Exception` and returns False, so a guard deriving
    # from Exception would be SWALLOWED and read as an ordinary send failure — the escape
    # must propagate.
    monkeypatch.setattr(notify, "NOTIFY_URL", "https://ntfy.sh/not-a-real-topic")
    assert notify.enabled() is True  # the fixture's blanking is genuinely overridden here

    with pytest.raises(BaseException) as excinfo:
        notify.send("this must never leave the machine", title="chela: test escape")

    assert type(excinfo.value).__name__ == "LiveNotificationEscape", (
        f"send() did not raise the fence's escape (got {type(excinfo.value).__name__}) — "
        "if it returned False instead, the guard derives from Exception and send() ate it"
    )
