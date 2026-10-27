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

import json
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


# ---- ⏸️ CMX-339: a parked run is idle on purpose ----------------------------
#
# 2026-10-25: CMX-318's park shielded a run from the inbox's stuck notice, but not from
# this watchdog — an agent parked and told to wait was re-nudged, then failed "idle at
# empty prompt after re-nudge" about an hour later.

def _row(task_id: str) -> dict:
    with dispatcher._db() as conn:
        return dict(conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone())


def _set(task_id: str, **cols) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "UPDATE runs SET " + ", ".join(f"{k}=?" for k in cols) + " WHERE task_id=?",
            (*cols.values(), task_id),
        )
        conn.commit()


@pytest.fixture
def idle_pane(monkeypatch):
    """A bare empty prompt, affirmatively idle; every re-sent seed recorded."""
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _BARE_IDLE)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "idle")
    monkeypatch.setattr("chela.suite_guard.waits_in", lambda wt: None)
    seeds = []
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **kw: seeds.append(a) or True)
    return seeds


def test_parked_run_idle_past_the_fail_step_is_neither_failed_nor_nudged(ticking, idle_pane):
    """The 10-25 incident: parked, nudged an hour ago, affirmatively idle — the exact state
    that failed CMX-334. Drop the park check → failed → RED."""
    task_id = _seed_running(ticking, nudged=_OLD)
    assert dispatcher.park_run(task_id, reason="wait for the other run", by="orch")["ok"]

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 0
    assert summary["watchdog_renudged"] == 0
    assert summary["watchdog_parked"] == 1
    assert idle_pane == []
    row = _row(task_id)
    assert row["status"] == "running"
    assert row["idle_nudged_at"] is None       # the stale nudge is dropped
    assert row["idle_clock_at"] is not None    # the idle clock restarts here


def test_parked_run_at_the_first_nudge_step_is_not_nudged(ticking, idle_pane):
    """A parked agent is never nudged back toward its task (the 18:23 re-send)."""
    task_id = _seed_running(ticking, nudged=None)
    assert dispatcher.park_run(task_id, reason="hold", by="orch")["ok"]

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    assert summary["watchdog_renudged"] == 0
    assert idle_pane == []
    assert _row(task_id)["idle_nudged_at"] is None


def test_parked_run_on_an_input_dialog_is_not_escaped(ticking, monkeypatch):
    """The whole watchdog stands down for a parked run, the dialog arm included."""
    task_id = _seed_running(ticking, nudged=None)
    assert dispatcher.park_run(task_id, reason="hold", by="orch")["ok"]
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w, **_: _QUESTION)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: "waiting")
    escaped = []
    monkeypatch.setattr(dispatcher, "_dismiss_input_block", escaped.append)

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    assert summary["watchdog_unblocked"] == 0
    assert escaped == []


def test_after_unpark_the_full_idle_budget_applies_again(ticking, idle_pane):
    """Unparked: no instant nudge measured from `started_at` (an hour ago) — the clock runs
    from the last parked tick. Once that clock is a full window old the watchdog nudges, and
    a full window after the nudge it fails, exactly as for an agent never parked."""
    task_id = _seed_running(ticking, nudged=_OLD)
    assert dispatcher.park_run(task_id, reason="hold", by="orch")["ok"]
    dispatcher.tick(ticking / "WORKFLOW.md")
    assert dispatcher.unpark_run(task_id, by="orch")["ok"]

    summary = dispatcher.tick(ticking / "WORKFLOW.md")
    assert summary["watchdog_renudged"] == 0
    assert summary["reconciled_failed"] == 0
    assert idle_pane == []

    _set(task_id, idle_clock_at=_OLD)          # a full idle window has passed since
    summary = dispatcher.tick(ticking / "WORKFLOW.md")
    assert summary["watchdog_renudged"] == 1
    assert summary["reconciled_failed"] == 0
    assert len(idle_pane) == 1

    _set(task_id, idle_nudged_at=_OLD)         # …and a full window since the nudge
    summary = dispatcher.tick(ticking / "WORKFLOW.md")
    assert summary["reconciled_failed"] == 1
    assert _status_of(task_id)[0] == "failed"


def test_unpark_clock_inside_the_window_holds_the_nudge(ticking, idle_pane):
    """The idle clock is a real lower bound, not just "was ever parked": stamped 4 minutes
    ago (inside the 5-minute window) it still holds the nudge."""
    task_id = _seed_running(ticking, nudged=None)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
    _set(task_id, idle_clock_at=recent)

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    assert summary["watchdog_renudged"] == 0
    assert idle_pane == []


def _past(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _future(**kw) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


@pytest.mark.parametrize(
    "until, other_status, max_s, shielded",
    [
        (None, None, None, True),                       # no --until, inside the max
        ("FUTURE", None, None, True),                   # --until not reached yet
        ("PAST", None, None, False),                    # --until reached ⇒ the park ended
        ("run:CMX-OTHER", "running", None, True),       # its run is still going
        ("run:CMX-OTHER", "done", None, False),         # its run ended ⇒ the park ended
        ("run:CMX-OTHER", None, None, False),           # unknown run ⇒ never trusted
        (None, None, "60", False),                      # past the park max
    ],
)
def test_a_park_shields_only_until_it_ends(ticking, idle_pane, monkeypatch,
                                           until, other_status, max_s, shielded):
    """`--until` reached (a time, or its `run:<ID>` ended) or the park max outlived: the park
    no longer holds the watchdog off — the same rule that ends its stuck-notice shield."""
    task_id = _seed_running(ticking, nudged=_OLD)
    if max_s:
        monkeypatch.setenv("CHELA_PARK_MAX_S", max_s)
    if other_status:
        with dispatcher._db() as conn:
            conn.execute(
                "INSERT INTO runs (task_id, workflow_path, title, status, started_at, attempt) "
                "VALUES ('CMX-OTHER', 'elsewhere/WORKFLOW.md', 'other', ?, ?, 1)",
                (other_status, _OLD))
            conn.commit()
    stored = {"FUTURE": _future(hours=1), "PAST": _past(minutes=1)}.get(until, until)
    _set(task_id, parked_at=_past(minutes=10), parked_reason="hold", parked_until=stored,
         parked_by="orch")

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    assert (_status_of(task_id)[0] == "running") is shielded
    assert summary["watchdog_parked"] == (1 if shielded else 0)


@pytest.mark.parametrize("worktree", ["wt", None])
def test_a_suite_slot_wait_parks_the_run(ticking, idle_pane, monkeypatch, tmp_path, worktree):
    """The automatic park: an open suite-slot wait in the run's worktree holds the watchdog
    off; no wait (or no worktree to look in) ⇒ the run fails as usual."""
    task_id = _seed_running(ticking, nudged=_OLD)
    wt = str(tmp_path / "wt") if worktree else None
    _set(task_id, worktree_path=wt)
    asked = []

    def waits_in(path):
        asked.append(path)
        return {"waiting_since": 1.0, "waited": 0.0}
    monkeypatch.setattr("chela.suite_guard.waits_in", waits_in)

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    if worktree:
        assert asked == [wt]
        assert summary["watchdog_parked"] == 1
        assert summary["reconciled_failed"] == 0
        assert _status_of(task_id)[0] == "running"
    else:
        assert asked == []
        assert summary["reconciled_failed"] == 1


def test_a_closed_suite_slot_wait_does_not_park(ticking, idle_pane, monkeypatch, tmp_path):
    """A ledger record with no open wait (`waiting_since` None: it holds the slot now, or
    waited earlier) is not a park."""
    task_id = _seed_running(ticking, nudged=_OLD)
    _set(task_id, worktree_path=str(tmp_path / "wt"))
    monkeypatch.setattr("chela.suite_guard.waits_in",
                        lambda wt: {"waiting_since": None, "waited": 30.0})

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 1


def test_unpark_restarts_the_idle_clock_even_for_a_park_no_tick_saw(ticking, idle_pane):
    """Parked and unparked between two ticks: no parked tick stamped the clock, so
    `unpark_run` must — else the next tick measures from `started_at` (an hour ago) and
    fails the agent at once."""
    task_id = _seed_running(ticking, nudged=_OLD)
    assert dispatcher.park_run(task_id, reason="hold", by="orch")["ok"]
    assert dispatcher.unpark_run(task_id, by="orch")["ok"]
    row = _row(task_id)
    assert row["idle_clock_at"] is not None
    assert row["idle_nudged_at"] is None

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 0
    assert summary["watchdog_renudged"] == 0
    assert idle_pane == []


# ---- 🔒 CMX-359: a watchdog write never holds the lock against a later hand-in -----------
#
# 10-27 14:24: the parked branch above wrote `idle_clock_at` without committing, so the tick
# kept the WAL write lock for the rest of the reconcile loop. A LATER row's completion marker
# runs `mark_awaiting_review` on its own connection, which then failed with "database is
# locked" on every tick and stalled the queue. A one-run test cannot see this: with no later
# row, the commit after the loop releases the lock first. Every watchdog write that ends a
# row's turn is driven here, ahead of a second row that hands in.

def _two_tasks(repo: Path) -> tuple[str, str]:
    """Two open tracker tasks; the first is the one the loop reaches first."""
    from chela.sources.markdown import MarkdownSource
    (repo / "TODO.md").write_text("- [ ] alpha\n- [ ] beta\n")
    for argv in (["add", "TODO.md"], ["commit", "-m", "two"], ["push", "origin", "dev"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True)
    ids = sorted(t.id for t in MarkdownSource(_wf(repo)).list_open_tasks())
    assert len(ids) == 2
    return ids[0], ids[1]


def _insert_running(repo: Path, task_id: str, *, window: str, nudged: str | None,
                    worktree: str | None = None, pr_url: str | None = None,
                    rework_count: int = 0) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "branch_name, started_at, idle_nudged_at, attempt, pr_state, worktree_path, pr_url, "
            "rework_count) VALUES (?,?,?,'running',?,?,?,?,1,'open',?,?,?)",
            (task_id, str(repo / "WORKFLOW.md"), task_id, window, window, _OLD, nudged,
             worktree, pr_url, rework_count),
        )
        conn.commit()


def _template_error(*a, **kw):
    from chela.workflow import TemplateRenderError
    raise TemplateRenderError("unknown variable {{taks_title}}")


# (row A's window, nudge stamp, native status, prompt builder, summary key, A's end status).
# The `rework_*` cases end A's turn in `_rework_failed`, which commits on its own; a later
# step of the same tick escalates that verdict-less rework, hence `needs_human`.
_LOCK_CASES = {
    "rework_window_gone": ("cmx-gone", None, "idle", None, None, "needs_human"),
    "rework_idle_failed": (WID, _OLD, "idle", None, None, "needs_human"),
    "parked": (WID, _OLD, "idle", None, "watchdog_parked", "running"),
    "window_gone": ("cmx-gone", None, "idle", None, "reconciled_failed", "failed"),
    "dialog_failed": (WID, _OLD, "waiting", None, "reconciled_failed", "failed"),
    "dialog_escaped": (WID, None, "waiting", None, "watchdog_unblocked", "running"),
    "idle_failed": (WID, _OLD, "idle", None, "reconciled_failed", "failed"),
    "template_error": (WID, None, "idle", _template_error, "reconciled_failed", "failed"),
    "renudged": (WID, None, "idle", None, "watchdog_renudged", "running"),
}


@pytest.mark.parametrize("case", sorted(_LOCK_CASES))
def test_a_watchdog_write_does_not_lock_out_a_later_rows_hand_in(
        ticking, idle_pane, monkeypatch, tmp_path, caplog, case):
    """Row A ends its turn on a watchdog write; row B, processed after it in the same tick,
    carries a completion marker. B reaches `awaiting_review` in THIS tick and nothing logs
    a lock error. Drop the `conn.commit()` after A's write → B's `mark_awaiting_review`
    waits out the busy timeout and raises "database is locked" → RED."""
    window, nudged, status, prompt, key, a_status = _LOCK_CASES[case]
    a_id, b_id = _two_tasks(ticking)
    _insert_running(ticking, a_id, window=window, nudged=nudged,
                    rework_count=1 if case.startswith("rework_") else 0)
    wt_b = tmp_path / "wt-b"
    wt_b.mkdir()
    (wt_b / ".chela-task-finished-request.json").write_text(json.dumps({"task_id": b_id}))
    _insert_running(ticking, b_id, window="cmx-b", nudged=None, worktree=str(wt_b),
                    pr_url="https://github.com/example/repo/pull/1")
    # The loop must reach A before B, or nothing is left open when B hands in (a silent
    # green). Pinned on the reconcile loop's own query, which has no ORDER BY.
    with dispatcher._db() as conn:
        order = [r["task_id"] for r in conn.execute(
            "SELECT * FROM runs WHERE workflow_path=? AND status IN ('claimed','running')",
            (str(ticking / "WORKFLOW.md"),)).fetchall()]
    assert order == [a_id, b_id]
    if case == "parked":
        assert dispatcher.park_run(a_id, reason="hold", by="orch")["ok"]
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w, **_: status)
    monkeypatch.setattr(dispatcher, "_dismiss_input_block", lambda w: None)
    if prompt is not None:
        monkeypatch.setattr(dispatcher, "_renudge_prompt", prompt)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, cwd: ("open", "MERGEABLE"))
    monkeypatch.setattr(dispatcher, "reap_run_worktree", lambda *a, **kw: None)

    summary = dispatcher.tick(ticking / "WORKFLOW.md")

    if key is not None:
        assert summary[key] == 1
    assert _status_of(a_id)[0] == a_status
    assert summary["task_finished_applied"] == 1
    assert _status_of(b_id)[0] == "awaiting_review"
    assert not (wt_b / ".chela-task-finished-request.json").exists()
    assert not [r for r in caplog.records if "database is locked" in r.getMessage()]
