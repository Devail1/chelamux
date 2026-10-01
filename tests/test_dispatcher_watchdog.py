"""The reconcile watchdog must not false-fail a working agent, and must unblock a
dispatched agent stranded on an input dialog (2026-07-23 dispatcher hardening).

Two bugs made the 07-23 dogfood batch a babysitting exercise:

  (1) FALSE FAILURE. The Claude Code TUI paints its working spinner + a live
      "(<elapsed> · ↓ <n> tokens)" status line ABOVE the input box, and the box
      below stays an empty `❯` while it generates — so `_pane_idle_empty_prompt`
      returns True for an agent that is plainly still working. With only a
      "not busy" cross-check (which an unreadable status passes), the watchdog
      marked cmx-158/159/160 `failed` mid-work, after they had opened their PRs.

  (2) HUNG ON A DIALOG. A dispatched agent that calls `AskUserQuestion` (or hits a
      gated permission prompt) goes native-status "waiting" and shows a picker,
      not a bare `❯` — so the idle check never fired and it hung to MAX_ATTEMPTS
      with no human to answer it.

These pin the fixes at `tick()`'s watchdog: an activity veto + an affirmative-idle
requirement for (1), and an Escape-then-fail recovery for (2).
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from chela import dispatcher
from chela.workflow import WorkflowDef

WORKFLOW = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: 1
---
seed
"""

WID = "cmx-1"

# The real false-failure pane: the working spinner + token counter sit ABOVE an
# EMPTY `❯` input box, so it trips `_pane_idle_empty_prompt` even though the agent
# is generating. This is exactly what a mid-work agent's pane looks like.
_WORKING_WITH_EMPTY_PROMPT = (
    "⏺ Editing dispatcher.py…\n"
    "✽ Puzzling… (6m 9s · ↓ 17.2k tokens)\n"
    "╭───────────────────────────────────╮\n"
    "│ ❯                                 │\n"
    "╰───────────────────────────────────╯\n"
)
# A genuinely stalled agent: a bare empty prompt, no spinner, no counter.
_BARE_IDLE = (
    "╭───────────────────────────────────╮\n"
    "│ ❯                                 │\n"
    "╰───────────────────────────────────╯\n"
)
# An AskUserQuestion picker — NOT a bare `❯`, so the idle check can't see it.
_QUESTION = (
    "╭─ Which approach? ─────────────────╮\n"
    "│ ❯ 1. Option A                     │\n"
    "│   2. Option B                     │\n"
    "╰───────────────────────────────────╯\n"
)

_OLD = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


@pytest.fixture
def ticking(tmp_path, monkeypatch):
    """A repo whose WORKFLOW.md drives a real tick(), tmux/gh/spawn stubbed."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True, capture_output=True)
    (repo / "TODO.md").write_text("- [ ] alpha\n")
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / ".chela" / "worktrees"))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "dev"], check=True, capture_output=True)

    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: {WID})
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: False)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **kw: True)
    return repo


def _wf(repo: Path) -> WorkflowDef:
    from chela.workflow import load_workflow
    return load_workflow(repo / "WORKFLOW.md")


def _seed_running(repo: Path, *, nudged: str | None) -> str:
    """A `running` first-dispatch row for the tracker's one open task, aged past
    the watchdog window; `nudged` set (and old) puts it at the terminal-fail step."""
    from chela.sources.markdown import MarkdownSource
    task_id = next(t.id for t in MarkdownSource(_wf(repo)).list_open_tasks())
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "branch_name, started_at, idle_nudged_at, attempt, pr_state) "
            "VALUES (?,?,?,'running',?,?,?,?,1,'open')",
            (task_id, str(repo / "WORKFLOW.md"), "alpha", WID, "cmx-1", _OLD, nudged),
        )
        conn.commit()
    return task_id


def _status_of(task_id: str) -> tuple[str, str | None]:
    with dispatcher._db() as conn:
        r = conn.execute(
            "SELECT status, last_error FROM runs WHERE task_id=?", (task_id,)
        ).fetchone()
    return r["status"], r["last_error"]


# ---- (1) false-failure guards ----------------------------------------------

def test_working_agent_is_never_failed_even_at_the_fail_step(ticking, monkeypatch):
    """The load-bearing guard: a pane showing the working spinner (above an empty
    prompt) must NOT be failed, even with an affirmative 'idle' status and both
    graces elapsed. Remove the activity veto → this run is failed → RED."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _WORKING_WITH_EMPTY_PROMPT)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "idle")

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 0
    assert _status_of(task_id)[0] == "running"  # left alone — it's working


def test_unreadable_status_does_not_drive_a_terminal_fail(ticking, monkeypatch):
    """A bare-idle pane with an UNREADABLE status (None) must re-nudge, not fail —
    None is not evidence of idleness. Fail on `status != 'busy'` instead → RED."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _BARE_IDLE)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: None)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 0
    assert summary["watchdog_renudged"] == 1  # re-nudged instead of killed
    assert _status_of(task_id)[0] == "running"


def test_genuinely_idle_agent_still_fails(ticking, monkeypatch):
    """The real stall path is intact: a bare-idle pane, affirmatively 'idle', past
    both graces → failed into the re-dispatch path."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _BARE_IDLE)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "idle")

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 1
    status, err = _status_of(task_id)
    assert status == "failed"
    assert "idle at empty prompt" in err


# ---- (2) input-dialog (AskUserQuestion) recovery ---------------------------

def test_first_renudge_with_a_broken_prompt_template_fails_only_this_run(ticking, monkeypatch):
    """issue #504 / SPEC 5.5: a `{{var}}` the workflow never provides must fail only the
    ONE stuck run being re-nudged — never crash the whole watchdog pass and leave every
    other run in this tick unchecked. Drop the try/except around `_renudge_prompt` at the
    call site → the raise propagates out of `tick()` uncaught → RED."""
    repo = ticking
    (repo / "WORKFLOW.md").write_text(
        (repo / "WORKFLOW.md").read_text().replace("\nseed\n", "\nseed {{taks_title}}\n")
    )
    task_id = _seed_running(repo, nudged=None)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _BARE_IDLE)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "idle")

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 1
    status, err = _status_of(task_id)
    assert status == "failed"
    assert "taks_title" in err


def test_waiting_agent_is_escaped_not_failed_on_first_encounter(ticking, monkeypatch):
    """A dispatched agent blocked on a dialog (status 'waiting') is Escaped so it
    falls back to its own default — not failed, not ignored. Drop the waiting
    branch and it falls through to the idle check, which can't see a dialog pane →
    no Escape → RED."""
    repo = ticking
    task_id = _seed_running(repo, nudged=None)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _QUESTION)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "waiting")
    dismiss = Mock()
    monkeypatch.setattr(dispatcher, "_dismiss_input_block", dismiss)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    dismiss.assert_called_once_with(WID)
    assert summary["watchdog_unblocked"] == 1
    assert summary["reconciled_failed"] == 0
    assert _status_of(task_id)[0] == "running"


def test_waiting_agent_that_does_not_recover_is_failed(ticking, monkeypatch):
    """Still 'waiting' a full grace after the Escape → it isn't recovering, so it
    fails into re-dispatch rather than hanging forever."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _QUESTION)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "waiting")
    monkeypatch.setattr(dispatcher, "_dismiss_input_block", Mock())

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 1
    status, err = _status_of(task_id)
    assert status == "failed"
    assert "blocked on an input dialog" in err


# ---- unit guards for the two helpers ---------------------------------------

def test_pane_shows_activity_detects_spinner_and_counter():
    assert dispatcher._pane_shows_activity(_WORKING_WITH_EMPTY_PROMPT)
    assert dispatcher._pane_shows_activity("✻ Sautéed for 1m 22s")
    assert dispatcher._pane_shows_activity("  ✶ Working… (esc to interrupt)")
    # A bare idle prompt or a static dialog shows no work signal.
    assert not dispatcher._pane_shows_activity(_BARE_IDLE)
    assert not dispatcher._pane_shows_activity(_QUESTION)


def test_pane_shows_login_expired_detects_the_banner():
    banner = "✽ Sonnet 5\n\nLogin expired · Please run /login\n\n❯ \n"
    assert dispatcher._pane_shows_login_expired(banner)
    # Ordinary idle/working panes carry no such text.
    assert not dispatcher._pane_shows_login_expired(_BARE_IDLE)
    assert not dispatcher._pane_shows_login_expired(_WORKING_WITH_EMPTY_PROMPT)
    assert not dispatcher._pane_shows_login_expired(_QUESTION)


def test_dismiss_input_block_sends_escape(monkeypatch):
    run = Mock()
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    dispatcher._dismiss_input_block(WID)
    args = run.call_args.args[0]
    assert args[:3] == ["tmux", "send-keys", "-t"]
    assert args[-1] == "Escape"


def test_dismiss_input_block_never_raises(monkeypatch):
    monkeypatch.setattr(
        dispatcher.subprocess, "run", Mock(side_effect=OSError("no tmux"))
    )
    dispatcher._dismiss_input_block(WID)  # must not raise


# ---- 👻 CMX-410: the ghost suggestion is an EMPTY prompt ---------------------
#
# Claude Code draws a grey suggestion into an EMPTY prompt through the input's
# placeholder — faint (SGR 2) by construction, and shown only while the input is empty
# (anthropics/claude-code #23859). A plain `capture-pane -p` strips the SGR, so the ghost
# read as a typed draft and cmx-408 sat stranded ~8h, never re-nudged. These fixtures are
# `capture-pane -pe` bytes; the input line is the orchestrator's real capture of @266.

_ESC = "\x1b"
_GHOST_LINE = f'{_ESC}[39m❯ {_ESC}[2mTry "fix lint errors"{_ESC}[0m'
_RULE = "─" * 40
_GHOST_IDLE_ANSI = f"{_RULE}\n{_GHOST_LINE}\n{_RULE}\n  ? for shortcuts\n"
# What a PLAIN capture of the same pane returns — the SGR gone, the ghost now "text".
_GHOST_IDLE_PLAIN = f'{_RULE}\n❯ Try "fix lint errors"\n{_RULE}\n  ? for shortcuts\n'
_TYPED_ANSI = f"{_RULE}\n{_ESC}[39m❯ hello\n{_RULE}\n"
# Claude's recap: dim-looking (italic + grey 246), but ABOVE the input and not SGR 2.
_RECAP = f"{_ESC}[3m{_ESC}[38;5;246m※ recap: added the ghost check; next run the suite{_ESC}[0m"
_RECAP_THEN_EMPTY_ANSI = f"{_RECAP}\n{_RULE}\n{_ESC}[39m❯ \n{_RULE}\n"
_RECAP_THEN_TYPED_ANSI = f"{_RECAP}\n{_RULE}\n{_ESC}[39m❯ hello\n{_RULE}\n"
# Mid-response: the working spinner above a prompt that still shows a ghost.
_WORKING_WITH_GHOST_ANSI = (
    f"⏺ Editing dispatcher.py…\n{_ESC}[38;5;174m✽ Puzzling… (6m 9s · ↓ 17.2k tokens){_ESC}[0m\n"
    f"{_RULE}\n{_GHOST_LINE}\n{_RULE}\n"
)


def _stuck(pane_ansi: str) -> bool:
    screen = dispatcher._drop_ghost_suggestion(pane_ansi)
    return dispatcher._pane_idle_empty_prompt(screen) and not dispatcher._pane_shows_activity(screen)


def test_ghost_suggestion_reads_as_an_empty_prompt():
    assert _stuck(_GHOST_IDLE_ANSI)
    # The same pane without escapes is what the pre-fix watchdog saw: "typed" ⇒ not stuck.
    assert not dispatcher._pane_idle_empty_prompt(_GHOST_IDLE_PLAIN)


def test_typed_draft_without_sgr2_is_not_empty():
    assert not _stuck(_TYPED_ANSI)
    # A colour that merely CONTAINS a 2 (`38;5;2`, truecolour `38;2;…`) is not faint.
    assert not _stuck(f"{_ESC}[39m❯ {_ESC}[38;5;2mhello{_ESC}[0m\n")
    assert not _stuck(f"{_ESC}[39m❯ {_ESC}[38;2;10;20;30mhello{_ESC}[0m\n")
    # Faint switched OFF (SGR 22) before the text: typed, not ghost.
    assert not _stuck(f"{_ESC}[39m❯ {_ESC}[2m{_ESC}[22mhello\n")


def test_dim_recap_elsewhere_does_not_confuse_the_input_line():
    assert _stuck(_RECAP_THEN_EMPTY_ANSI)
    assert not _stuck(_RECAP_THEN_TYPED_ANSI)


def test_agent_mid_response_behind_a_ghost_is_not_stuck():
    assert not _stuck(_WORKING_WITH_GHOST_ANSI)


def _ansi_only_capture(ansi_pane: str, plain_pane: str):
    """A `_capture_pane` stub returning what tmux would: escapes only under `-e`."""
    return lambda w, ansi=False: ansi_pane if ansi else plain_pane


def test_watchdog_renudges_an_agent_stranded_behind_a_ghost(ticking, monkeypatch):
    """The cmx-408 strand: an idle agent whose empty prompt shows a ghost suggestion must
    be re-nudged. Revert the watchdog to a plain capture → the ghost reads as typed text →
    not stuck → never nudged → RED."""
    repo = ticking
    task_id = _seed_running(repo, nudged=None)
    monkeypatch.setattr(dispatcher, "_capture_pane", _ansi_only_capture(_GHOST_IDLE_ANSI, _GHOST_IDLE_PLAIN))
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "idle")
    seed = Mock(return_value=True)
    monkeypatch.setattr(dispatcher, "_send_seed", seed)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["watchdog_renudged"] == 1
    seed.assert_called_once()
    assert _status_of(task_id)[0] == "running"


def test_watchdog_never_nudges_an_agent_mid_response_behind_a_ghost(ticking, monkeypatch):
    """The case that must be ACCEPTED: activity on screen vetoes the nudge even though the
    ghost makes the input line read empty."""
    repo = ticking
    task_id = _seed_running(repo, nudged=None)
    plain = dispatcher._CSI_RE.sub("", _WORKING_WITH_GHOST_ANSI)
    monkeypatch.setattr(dispatcher, "_capture_pane", _ansi_only_capture(_WORKING_WITH_GHOST_ANSI, plain))
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "idle")
    seed = Mock(return_value=True)
    monkeypatch.setattr(dispatcher, "_send_seed", seed)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["watchdog_renudged"] == 0
    seed.assert_not_called()
    assert _status_of(task_id)[0] == "running"


def test_watchdog_does_not_nudge_a_typed_draft(ticking, monkeypatch):
    repo = ticking
    _seed_running(repo, nudged=None)
    monkeypatch.setattr(dispatcher, "_capture_pane", _ansi_only_capture(_TYPED_ANSI, _TYPED_ANSI.replace(f"{_ESC}[39m", "")))
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "idle")
    seed = Mock(return_value=True)
    monkeypatch.setattr(dispatcher, "_send_seed", seed)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["watchdog_renudged"] == 0
    seed.assert_not_called()


def test_capture_pane_ansi_adds_dash_e(monkeypatch):
    """`ansi=True` must reach tmux as `-e`, or the SGR the ghost check reads never arrives."""
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="x", stderr=""))
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    dispatcher._capture_pane("cmx-1", ansi=True)
    assert "-e" in run.call_args.args[0]
    dispatcher._capture_pane("cmx-1")
    assert "-e" not in run.call_args.args[0]
