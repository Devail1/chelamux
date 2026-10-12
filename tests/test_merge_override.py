"""⚖️🔓 CMX-389 / CMX-79 — `chela merge --override`: the ONE way past the judge, and it is not
autonomous. Since CMX-79 the request and the merge are DECOUPLED: `merge --override` records
a pending request bound to the PR's head SHA and returns at once; the operator's approval
(Telegram button, dashboard, `chela merge-approve`) completes the merge by itself, after
re-checking head / CI / mergeable live. Deny, a moved head, or the TTL ⇒ no merge.

GitHub is stubbed (`_read_pr_base`, `_read_pr_checks`, `_read_pr_status`, `_squash_merge`);
the request store is REAL, under the sandboxed `CHELA_DIR` (conftest). No live Telegram:
the relay gets fake post/edit seams.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import config, contract, dispatcher, event_log, inbox, overrides
from chela.dispatcher import CI_FAILING, CI_PASSING, CIStatus
from chela.judge import J_BLOCKED

HEAD = "a" * 40
MOVED = "c" * 40
PR = "https://github.com/o/r/pull/1"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    # The approver must look like a human's plain terminal, whatever shell runs the suite.
    monkeypatch.delenv(config.ACTOR_ENV, raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv(overrides.TTL_ENV, raising=False)
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)
    monkeypatch.setattr(overrides.notify, "enabled", lambda: False)


@pytest.fixture
def told(monkeypatch):
    """Everything the operator/orchestrator was told about an override that did not merge."""
    seen: list[tuple] = []
    monkeypatch.setattr(inbox, "enqueue", lambda kind, summary, payload: seen.append(
        (kind, summary, payload)) or True)
    return seen


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text("# wf\n")
    return tmp_path


def _seed(repo: Path, *, status: str = "changes_requested", judge_state: str = J_BLOCKED) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, started_at, "
            "attempt, pr_url, pr_state, judge_state, judge_sha, branch_name, pr_head_sha) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", str(repo / "WORKFLOW.md"), "t", status, "@9", dispatcher._now(), 1,
             PR, "open", judge_state, "b" * 40, "cmx-1", HEAD),
        )
        conn.commit()


@contextmanager
def _github(ci=CI_PASSING, mergeable="MERGEABLE", head=HEAD, base="dev"):
    """The live-GitHub stubs; yields the `_squash_merge` mock."""
    with patch.object(contract, "_read_pr_base", return_value=base), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(ci, head_sha=head)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", mergeable)), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "m" * 40}) as squash:
        yield squash


def _request(reason="judge flaked on an unrelated test", **gh) -> dict:
    with _github(**gh) as squash:
        result = contract.merge("t1", override=True, reason=reason)
    squash.assert_not_called()
    return result


# --- the request: returns at once, merges nothing ----------------------------------------

def test_merge_override_returns_a_pending_request_and_does_not_merge(repo):
    """🔴 GUARD: `merge --override` opens a request bound to the head and RETURNS — it does
    not merge and does not wait. Corrupt it back into a merge (or a wait) → RED."""
    _seed(repo)
    started = time.monotonic()
    result = _request()
    assert time.monotonic() - started < 2, "the request must not wait for the operator"
    assert result["ok"] is True and result["pending"] is True, result
    rec = overrides.load(result["request_id"])
    assert rec["state"] == overrides.PENDING and rec["head_sha"] == HEAD
    assert [r["id"] for r in overrides.pending()] == [result["request_id"]]
    assert event_log.read(types=["orchestrator.merge_override_requested"])["events"]
    assert not event_log.read(types=["orchestrator.merge"])["events"]
    assert dispatcher.merge_overrides(dispatcher.resolve_run("t1")) == []


def test_asking_twice_for_the_same_head_opens_one_request(repo):
    _seed(repo)
    first, second = _request(), _request()
    assert second["request_id"] == first["request_id"] and second.get("existing")
    assert len(overrides.all_requests()) == 1


def test_asking_again_after_a_new_push_opens_a_fresh_request_for_the_new_head(repo):
    """🔴 GUARD: a pending request is reused only for the SAME head. After a push, asking
    again must bind a NEW request to the new head, never hand back the stale one."""
    _seed(repo)
    first = _request()
    second = _request(head=MOVED)
    assert second["ok"] is True and not second.get("existing"), second
    assert second["request_id"] != first["request_id"]
    assert second["head_sha"] == MOVED
    assert overrides.load(second["request_id"])["head_sha"] == MOVED


@pytest.mark.parametrize("ci,mergeable", [(CI_FAILING, "MERGEABLE"), (CI_PASSING, "CONFLICTING")])
def test_a_request_is_refused_on_red_ci_or_a_conflict(repo, ci, mergeable):
    """The override skips the JUDGE only — red CI / a conflict refuse before any request."""
    _seed(repo)
    result = _request(ci=ci, mergeable=mergeable)
    assert result["ok"] is False
    assert overrides.all_requests() == []


def test_override_requires_a_reason(repo):
    _seed(repo)
    result = _request(reason="  ")
    assert result["ok"] is False and "--reason" in result["error"]
    assert overrides.all_requests() == []


def test_without_override_a_blocked_judge_still_refuses(repo):
    """The override is opt-in: the plain gate is exactly what it was."""
    _seed(repo, status="awaiting_review")
    with _github() as squash:
        result = contract.merge("t1", reason="x")
    assert result["ok"] is False and result.get("judge_state") == J_BLOCKED
    squash.assert_not_called()


# --- the approval: merges by itself, exactly once ----------------------------------------

def test_approve_merges_exactly_once_through_the_merge_path_with_the_audit(repo, told):
    """🔴 GUARD: approve ⇒ merged via `_squash_merge`, bound to the approved head
    (--match-head-commit), the reason in the squash body, the audit on the event log and the
    run's review history. A second approval merges nothing."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "terminal:op")
        again = overrides.decide(rid, True, "dashboard")
    assert out["ok"] is True and out["state"] == overrides.MERGED, out
    squash.assert_called_once()
    kwargs = squash.call_args.kwargs
    assert kwargs["match_head"] == HEAD
    assert "judge flaked on an unrelated test" in kwargs["body"]
    assert again["ok"] is False and again["state"] == overrides.MERGED

    rec = overrides.load(rid)
    assert rec["state"] == overrides.MERGED and rec["merge_commit_sha"] == "m" * 40
    audit, = event_log.read(types=["orchestrator.merge_override"])["events"]
    assert audit["payload"]["approved_by"] == "terminal:op"
    assert audit["payload"]["head_sha"] == HEAD
    assert audit["payload"]["judge_state"] == J_BLOCKED
    assert audit["payload"]["request_id"] == rid
    history = dispatcher.merge_overrides(dispatcher.resolve_run("t1"))
    assert len(history) == 1 and history[0]["head_sha"] == HEAD
    merged, = event_log.read(types=["orchestrator.merge"])["events"]
    assert merged["payload"]["override"]["approved_by"] == "terminal:op"
    assert told == []


def test_the_override_audit_is_on_the_run_row_before_the_squash_merge(repo, told):
    """🔴 GUARD: reconcile must never see the PR merged while the run row lacks the
    override — so the audit is recorded on the row BEFORE `_squash_merge` runs."""
    _seed(repo)
    rid = _request()["request_id"]
    on_row_at_merge: list = []

    def squash(*a, **kw):
        on_row_at_merge.append(dispatcher.merge_overrides(dispatcher.resolve_run("t1")))
        return {"ok": True, "merge_commit_sha": "m" * 40}

    with _github() as mock:
        mock.side_effect = squash
        out = overrides.decide(rid, True, "terminal:op")
    assert out["state"] == overrides.MERGED, out
    assert len(on_row_at_merge) == 1 and len(on_row_at_merge[0]) == 1, \
        "the override must already be on the run row when the squash-merge runs"
    assert on_row_at_merge[0][0]["head_sha"] == HEAD


def test_approve_after_the_head_moved_expires_and_tells_the_operator(repo, told):
    """🔴 GUARD: the request is for ONE commit. A head that moved before the approval ⇒ no
    merge, the request EXPIRED, and the operator/orchestrator is told."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github(head=MOVED) as squash:
        out = overrides.decide(rid, True, "telegram", channel="telegram")
    squash.assert_not_called()
    assert out["ok"] is False and out["state"] == overrides.EXPIRED
    assert overrides.load(rid)["state"] == overrides.EXPIRED
    assert "moved" in overrides.load(rid)["detail"]
    assert [k for k, _s, _p in told] == ["override_expired"]
    assert dispatcher.merge_overrides(dispatcher.resolve_run("t1")) == []


@pytest.mark.parametrize("ci,mergeable,why", [(CI_FAILING, "MERGEABLE", "CI is RED"),
                                              (CI_PASSING, "CONFLICTING", "CONFLICTING")])
def test_approve_with_red_ci_or_a_conflict_fails_with_the_reason(repo, told, ci, mergeable, why):
    """🔴 GUARD: the gate is re-read AT APPROVAL. CI gone red / a conflict since the
    request ⇒ no merge, request FAILED with the reason, operator told."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github(ci=ci, mergeable=mergeable) as squash:
        out = overrides.decide(rid, True, "dashboard", channel="dashboard")
    squash.assert_not_called()
    assert out["ok"] is False and out["state"] == overrides.FAILED
    rec = overrides.load(rid)
    assert rec["state"] == overrides.FAILED and why in rec["detail"]
    assert [k for k, _s, _p in told] == ["override_failed"]


def test_approve_after_the_base_changed_since_the_request_fails(repo, told):
    """🔴 GUARD: the request was approved for the base it NAMED. If the live base is no
    longer that one — even an otherwise-allowed base — nothing merges. The fixture records
    a base that differs from the live one (a record copied from the same stub it is later
    compared against can never exercise this re-check)."""
    _seed(repo)
    rid = _request()["request_id"]
    rec = overrides.set_fields(rid, base="release")
    assert rec["base"] == "release"
    with _github(base="dev") as squash:
        out = overrides.decide(rid, True, "terminal:op")
    squash.assert_not_called()
    assert out["ok"] is False and out["state"] == overrides.FAILED, out
    assert "base changed" in overrides.load(rid)["detail"]
    assert [k for k, _s, _p in told] == ["override_failed"]
    assert dispatcher.merge_overrides(dispatcher.resolve_run("t1")) == []


def test_approve_when_the_live_base_is_main_merges_nothing(repo, told):
    """🔴 GUARD: the base is re-read AT APPROVAL — a PR re-targeted to `main` after the
    request never merges, override or not."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github(base="main") as squash:
        out = overrides.decide(rid, True, "terminal:op")
    squash.assert_not_called()
    assert out["ok"] is False and out["state"] == overrides.FAILED
    assert "main" in overrides.load(rid)["detail"]


def test_approve_with_an_unreadable_head_expires_and_merges_nothing(repo, told):
    """🔴 GUARD: an override is for ONE commit — a head that cannot be read is not it."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github(head=None) as squash:
        out = overrides.decide(rid, True, "terminal:op")
    squash.assert_not_called()
    assert out["ok"] is False and out["state"] == overrides.EXPIRED
    assert overrides.load(rid)["state"] == overrides.EXPIRED


def test_a_failed_squash_marks_the_request_failed_and_tells_the_operator(repo, told):
    """🔴 GUARD: GitHub refusing the merge at the last step ⇒ FAILED with its reason, the
    operator told — never reported as merged."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        squash.return_value = {"ok": False, "error": "head ref was modified"}
        out = overrides.decide(rid, True, "terminal:op")
    squash.assert_called_once()
    assert out["ok"] is False and out["state"] == overrides.FAILED
    rec = overrides.load(rid)
    assert rec["state"] == overrides.FAILED and rec["merge_commit_sha"] is None
    assert [k for k, _s, _p in told] == ["override_failed"]


def test_deny_merges_nothing(repo):
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, False, "terminal:op")
        late = overrides.decide(rid, True, "terminal:op")
    squash.assert_not_called()
    assert out["ok"] is True and out["state"] == overrides.DENIED
    assert late["ok"] is False and late["state"] == overrides.DENIED
    assert event_log.read(types=["orchestrator.merge_override_denied"])["events"]


def test_ttl_expiry_merges_nothing(repo, told, monkeypatch):
    """🔴 GUARD: past its TTL a request is EXPIRED — swept, and an approval that lands after
    the deadline merges nothing."""
    monkeypatch.setenv(overrides.TTL_ENV, "60")
    _seed(repo)
    rid = _request()["request_id"]
    assert overrides.load(rid)["ttl"] == 60
    with _github() as squash:
        overrides.sweep(now=time.time() + 61)
        out = overrides.decide(rid, True, "terminal:op")
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.EXPIRED
    assert out["ok"] is False


def test_an_approval_after_the_deadline_expires_without_a_sweep(repo, monkeypatch):
    monkeypatch.setenv(overrides.TTL_ENV, "0.05")
    _seed(repo)
    rid = _request()["request_id"]
    time.sleep(0.1)
    with _github() as squash:
        out = overrides.decide(rid, True, "terminal:op")
    squash.assert_not_called()
    assert out["state"] == overrides.EXPIRED


def test_a_zero_ttl_refuses_the_request(repo, monkeypatch):
    """🔴 GUARD: no lifetime, no request — `CHELA_OVERRIDE_WAIT_S=0` opens nothing."""
    monkeypatch.setenv(overrides.TTL_ENV, "0")
    _seed(repo)
    with _github() as squash:
        out = contract.merge("t1", override=True, reason="flaky")
    squash.assert_not_called()
    assert out["ok"] is False and not out.get("pending")
    assert overrides.all_requests() == []


def test_the_default_ttl_is_a_day():
    assert overrides.ttl() == 24 * 3600


def test_a_new_push_expires_a_pending_request(repo, told):
    """🔴 GUARD: the sweep expires a request whose PR head (the run row's `pr_head_sha`,
    refreshed by the dispatcher) moved past the recorded SHA — before anyone approves."""
    _seed(repo)
    rid = _request()["request_id"]
    assert overrides.sweep() == []                       # head unchanged → still pending
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET pr_head_sha=? WHERE task_id='t1'", (MOVED,))
        conn.commit()
    expired = overrides.sweep()
    assert [r["id"] for r in expired] == [rid]
    assert overrides.load(rid)["state"] == overrides.EXPIRED
    assert [k for k, _s, _p in told] == ["override_expired"]


def test_the_sweep_keeps_a_request_whose_head_is_unreadable(repo, told):
    """🔴 GUARD: only a head SEEN to differ expires a request — a head the sweep could not
    read (no row value, a failing reader) leaves it pending."""
    _seed(repo)
    rid = _request()["request_id"]
    assert overrides.sweep(head_of=lambda _t: None) == []
    assert overrides.sweep(head_of=lambda _t: 1 / 0) == []
    assert overrides.load(rid)["state"] == overrides.PENDING
    assert told == []


# --- human-only ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", [{config.ACTOR_ENV: config.AUTO_ORCHESTRATOR_ACTOR},
                                 {"CLAUDECODE": "1"}])
def test_an_agent_process_cannot_approve(repo, env):
    """🔴 GUARD: the orchestrator may REQUEST, never approve. A process carrying its actor
    stamp or a Claude session marker is refused, and nothing merges."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "terminal:op", env=env)
    squash.assert_not_called()
    assert out["ok"] is False and out.get("refused")
    assert overrides.load(rid)["state"] == overrides.PENDING


_AGENT_MARKERS = {config.ACTOR_ENV: config.AUTO_ORCHESTRATOR_ACTOR, "CLAUDECODE": "1"}


@pytest.mark.parametrize("channel", ["telegram", "dashboard"])
def test_a_service_channel_approves_despite_inherited_agent_markers(repo, channel):
    """🔴 GUARD (CMX-21): the Telegram bridge and the dashboard are services that may have
    inherited `$CHELA_ACTOR` / `CLAUDECODE` from whatever shell started them. Their CALL is
    the human's press, so the env check must NOT refuse them — else the one-tap button is
    dead. Driven with the markers PRESENT (the default suite env strips them)."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, channel, channel=channel, env=dict(_AGENT_MARKERS))
    assert out["ok"] is True and out["state"] == overrides.MERGED, out
    squash.assert_called_once()


def test_the_telegram_tap_merges_in_a_process_with_agent_markers(repo, monkeypatch):
    """🔴 GUARD: the real tap path, in a bridge process whose os.environ carries both
    markers — it still merges (the tap routes through the telegram channel)."""
    from chela.telegram.overridewatch import handle_override_tap
    _seed(repo)
    rid = _request()["request_id"]
    for k, v in _AGENT_MARKERS.items():
        monkeypatch.setenv(k, v)
    with _github() as squash:
        toast = handle_override_tap(f"ov:a:{rid}")
    squash.assert_called_once()
    assert toast.startswith("✅ Merged")


def test_the_dashboard_post_merges_in_a_process_with_agent_markers(repo, monkeypatch):
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    _seed(repo)
    rid = _request()["request_id"]
    for k, v in _AGENT_MARKERS.items():
        monkeypatch.setenv(k, v)
    with _github() as squash:
        ok = app.test_client().post(f"/override/{rid}", data={"decision": "approve"})
    assert ok.status_code == 200 and b"merged" in ok.data
    squash.assert_called_once()


@pytest.mark.parametrize("channel", ["terminal", "cli", ""])
def test_a_non_service_channel_with_agent_markers_is_refused(repo, channel):
    """The exemption is exactly the two service channels — any other spelling is refused."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "x", channel=channel, env=dict(_AGENT_MARKERS))
    squash.assert_not_called()
    assert out.get("refused") and overrides.load(rid)["state"] == overrides.PENDING


def test_merge_approve_cli_refuses_inside_an_agent_session(repo, monkeypatch, capsys):
    from chela import main
    _seed(repo)
    rid = _request()["request_id"]
    monkeypatch.setenv(config.ACTOR_ENV, config.AUTO_ORCHESTRATOR_ACTOR)
    with _github() as squash, pytest.raises(SystemExit) as exc:
        main.cmd_merge_approve(SimpleNamespace(request=rid, deny=False))
    assert exc.value.code == 1
    squash.assert_not_called()
    assert "never approve" in capsys.readouterr().out


def test_merge_approve_cli_completes_the_merge(repo, capsys):
    from chela import main
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        main.cmd_merge_approve(SimpleNamespace(request=rid, deny=False))
    squash.assert_called_once()
    assert "merged" in capsys.readouterr().out


def test_merge_override_cli_exits_zero_with_the_request_id(repo, capsys):
    from chela import main
    _seed(repo)
    with _github() as squash:
        main.cmd_merge(SimpleNamespace(run="t1", reason="flaky judge", override=True))
    squash.assert_not_called()
    out = capsys.readouterr().out
    rid, = [r["id"] for r in overrides.pending()]
    assert rid in out and "PENDING" in out


def test_override_request_cli_never_merges(repo, capsys):
    from chela import main
    _seed(repo)
    with _github() as squash:
        main.cmd_override(SimpleNamespace(override_cmd="request", run="t1", reason="flaky"))
    squash.assert_not_called()
    assert len(overrides.pending()) == 1


# --- Telegram: one message, two buttons, edited to the outcome ----------------------------

class _FakeBot:
    def __init__(self):
        self.posts: list[dict] = []
        self.edits: list[dict] = []

    def post(self, text, message_thread_id=None, reply_markup=None):
        self.posts.append({"text": text, "thread": message_thread_id, "markup": reply_markup})
        return 100 + len(self.posts)

    def edit(self, message_id, text, reply_markup=None):
        self.edits.append({"id": message_id, "text": text, "markup": reply_markup})
        return True


def _relay(bot, thread="42"):
    from chela.telegram.overridewatch import OverrideRelay
    return OverrideRelay(post=bot.post, edit=bot.edit, thread=lambda: thread)


def test_a_request_posts_one_telegram_message_with_two_buttons_and_the_pr_url(repo):
    """🔴 GUARD: ONE message on the orchestrator's topic, an absolute PR link, the judge
    state on the head, the reason, and exactly two callback buttons for this request."""
    from chela.telegram.interactive import decode_override_callback
    _seed(repo)
    rid = _request()["request_id"]
    bot = _FakeBot()
    relay = _relay(bot)
    relay.tick()
    relay.tick()
    post, = bot.posts
    assert post["thread"] == "42"
    assert PR in post["text"] and PR.startswith("https://")
    assert J_BLOCKED in post["text"] and "judge flaked" in post["text"]
    # the seeded judge ran on "b"*40, not HEAD — the posted card must say so
    assert "not this head" in post["text"] and "on this head" not in post["text"]
    buttons = [b for row in post["markup"]["inline_keyboard"] for b in row]
    assert [decode_override_callback(b["callback_data"]) for b in buttons] == [
        (True, rid), (False, rid)]
    assert bot.edits == []


def test_the_card_says_the_judge_never_saw_this_head_when_it_judged_another():
    """🔴 GUARD: judge_sha != head_sha ⇒ the card must NOT claim the verdict is on this
    head. Both branches print the judge state, so assert the head-specific wording."""
    from chela.telegram.overridewatch import format_override_message
    base = {"id": "override-" + "0" * 24, "task_id": "t1", "pr_url": PR, "reason": "r",
            "judge_state": J_BLOCKED, "head_sha": HEAD}
    other = format_override_message({**base, "judge_sha": "b" * 40})
    assert "on this head" not in other
    assert "not this head" in other and "b" * 12 in other
    same = format_override_message({**base, "judge_sha": HEAD})
    assert f"Judge: {J_BLOCKED} on this head" in same and "not this head" not in same


def test_an_approve_tap_merges_and_the_message_is_edited_to_the_outcome(repo):
    from chela.telegram.overridewatch import handle_override_tap
    _seed(repo)
    _request()
    bot = _FakeBot()
    relay = _relay(bot)
    relay.tick()
    approve = bot.posts[0]["markup"]["inline_keyboard"][0][0]["callback_data"]
    with _github() as squash:
        toast = handle_override_tap(approve)
        handle_override_tap(approve)                     # a double tap merges nothing more
    squash.assert_called_once()
    assert toast.startswith("✅ Merged")
    relay.tick()
    edit, = bot.edits
    assert edit["id"] == 101 and edit["markup"] is None
    assert "merged mmmmmmmmmmmm" in edit["text"]


@pytest.mark.parametrize("decision,shown", [(False, "Denied"), (None, "Expired")])
def test_the_message_is_edited_to_denied_or_expired(repo, decision, shown):
    from chela.telegram.overridewatch import handle_override_tap
    _seed(repo)
    rid = _request()["request_id"]
    bot = _FakeBot()
    relay = _relay(bot)
    relay.tick()
    if decision is False:
        handle_override_tap(f"ov:d:{rid}")
    else:
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET pr_head_sha=? WHERE task_id='t1'", (MOVED,))
            conn.commit()
    relay.tick()
    edit, = bot.edits
    assert shown in edit["text"] and edit["markup"] is None


def test_a_bot_tap_is_refused(repo):
    from chela.telegram.overridewatch import handle_override_tap
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        toast = handle_override_tap(f"ov:a:{rid}", is_bot=True)
    squash.assert_not_called()
    assert "bot" in toast
    assert overrides.load(rid)["state"] == overrides.PENDING


class _Query:
    def __init__(self, data, is_bot=False):
        self.data = data
        self.from_user = SimpleNamespace(is_bot=is_bot, id=1, username="op")
        self.answers: list = []

    async def answer(self, text=None, **_kw):
        self.answers.append(text)


def _override_handler(decide, refresh=None):
    pytest.importorskip("telegram")
    from telegram.ext import CallbackQueryHandler

    from chela.telegram.inbound import TopicRouter, build_application

    app = build_application("123:fake-token",
                            TopicRouter("777", "@3", "4", sender=lambda *_a: True),
                            decide_override=decide, refresh_overrides=refresh)
    handlers = [h for group in app.handlers.values() for h in group]
    hit = [h for h in handlers if isinstance(h, CallbackQueryHandler)
           and h.pattern is not None and h.pattern.match("ov:a:override-0")]
    return hit[0].callback


def test_the_callback_handler_resolves_from_the_bound_chat_only():
    """🔴 GUARD (the callback path): a tap from the bound chat resolves the request through
    `decide`; the same tap from any other chat decides NOTHING."""
    import asyncio
    calls: list = []
    refreshed: list = []

    def decide(rid, approve, by, channel="terminal"):
        calls.append((rid, approve, channel))
        return {"ok": True, "state": overrides.DENIED}

    on_override = _override_handler(decide, refresh=lambda: refreshed.append(1))
    rid = "override-" + "0" * 24
    stranger = SimpleNamespace(callback_query=_Query(f"ov:d:{rid}"),
                               effective_chat=SimpleNamespace(id=999))
    asyncio.run(on_override(stranger, None))
    assert calls == [] and stranger.callback_query.answers == [None]

    bound = SimpleNamespace(callback_query=_Query(f"ov:d:{rid}"),
                            effective_chat=SimpleNamespace(id=777))
    asyncio.run(on_override(bound, None))
    assert calls == [(rid, False, "telegram")]
    assert bound.callback_query.answers == ["❌ Denied — nothing merged"]
    assert refreshed == [1]


# --- the dashboard confirm page -----------------------------------------------------------

def test_the_dashboard_confirm_page_approves_and_merges(repo):
    """The dashboard confirm (`/override/<id>`) resolves through the same path — and refuses
    a cross-site POST."""
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    _seed(repo)
    rid = _request()["request_id"]
    client = app.test_client()
    page = client.get(f"/override/{rid}")
    assert page.status_code == 200 and b"Approve override" in page.data
    with _github() as squash:
        cross = client.post(f"/override/{rid}", data={"decision": "approve"},
                            headers={"Origin": "https://evil.example",
                                     "Sec-Fetch-Site": "cross-site"})
        assert cross.status_code == 403
        squash.assert_not_called()
        ok = client.post(f"/override/{rid}", data={"decision": "approve"})
    assert ok.status_code == 200 and b"merged" in ok.data
    squash.assert_called_once()
    assert overrides.load(rid)["decided_by"] == "dashboard"
    gone = client.get(f"/override/{rid}")
    assert gone.status_code == 404


# --- rework 2: each guard asserts its invariant on the real path ---------------------------

def test_merge_approve_cli_deny_denies_and_merges_nothing(repo, capsys):
    """🔴 GUARD: `chela merge-approve <id> --deny` DENIES — the flag reaches `decide` as
    approve=False. Hard-wire it to approve and this merges → RED."""
    from chela import main
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        main.cmd_merge_approve(SimpleNamespace(request=rid, deny=True))
    squash.assert_not_called()
    rec = overrides.load(rid)
    assert rec["state"] == overrides.DENIED and rec["merge_commit_sha"] is None
    out = capsys.readouterr().out
    assert "denied" in out and "approved and merged" not in out
    assert event_log.read(types=["orchestrator.merge_override_denied"])["events"]
    assert not event_log.read(types=["orchestrator.merge_override"])["events"]
    assert dispatcher.merge_overrides(dispatcher.resolve_run("t1")) == []


def test_a_bot_tap_on_the_real_callback_handler_is_refused():
    """🔴 GUARD (wiring): the handler must pass the tapping account's `is_bot` through to the
    tap — a bot's tap from the BOUND chat decides nothing. Drop the wiring → RED."""
    import asyncio
    calls: list = []

    def decide(rid, approve, by, channel="terminal"):
        calls.append((rid, approve, channel))
        return {"ok": True, "state": overrides.MERGED, "merge_commit_sha": "m" * 40}

    on_override = _override_handler(decide)
    rid = "override-" + "0" * 24
    bot_tap = SimpleNamespace(callback_query=_Query(f"ov:a:{rid}", is_bot=True),
                              effective_chat=SimpleNamespace(id=777))
    asyncio.run(on_override(bot_tap, None))
    assert calls == []
    answer, = bot_tap.callback_query.answers
    assert "bot" in answer and "Merged" not in answer
    human_tap = SimpleNamespace(callback_query=_Query(f"ov:a:{rid}", is_bot=False),
                                effective_chat=SimpleNamespace(id=777))
    asyncio.run(on_override(human_tap, None))
    assert calls == [(rid, True, "telegram")]


def _decided(repo, how: str) -> str:
    """A request moved out of `pending` BEFORE the relay ever saw it."""
    _seed(repo)
    rid = _request()["request_id"]
    if how == "denied":
        overrides.decide(rid, False, "terminal:op")
    elif how == "merged":
        with _github():
            overrides.decide(rid, True, "terminal:op")
    elif how == "failed":
        with _github(ci=CI_FAILING):
            overrides.decide(rid, True, "terminal:op")
    elif how == "expired":
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET pr_head_sha=? WHERE task_id='t1'", (MOVED,))
            conn.commit()
        overrides.sweep()
    elif how == "merging":
        overrides.set_fields(rid, state=overrides.MERGING)
    assert overrides.load(rid)["state"] == how
    return rid


@pytest.mark.parametrize("how", ["denied", "merged", "failed", "expired", "merging"])
def test_the_relay_posts_no_buttons_for_a_request_decided_before_it_saw_it(repo, how):
    """🔴 GUARD: only a request still PENDING gets a new Telegram message (with buttons).
    One decided before the bridge saw it gets NOTHING — no post, no edit, no buttons."""
    _decided(repo, how)
    bot = _FakeBot()
    relay = _relay(bot)
    relay.tick()
    relay.tick()
    assert bot.posts == [] and bot.edits == []


def test_the_relay_edits_an_outcome_once_not_every_tick(repo):
    """🔴 GUARD: a message already showing its outcome is not re-edited on every tick."""
    _seed(repo)
    rid = _request()["request_id"]
    bot = _FakeBot()
    relay = _relay(bot)
    relay.tick()
    overrides.decide(rid, False, "terminal:op")
    for _ in range(3):
        relay.tick()
    assert len(bot.posts) == 1 and len(bot.edits) == 1
    assert overrides.load(rid)["telegram"]["rendered"] == overrides.DENIED


def test_pending_excludes_a_request_past_its_deadline_even_unswept(repo):
    """🔴 GUARD: `pending()` itself filters on the deadline — a request past its TTL that no
    sweep has touched yet is neither offered for approval nor reused for a new ask."""
    _seed(repo)
    rid = _request()["request_id"]
    assert [r["id"] for r in overrides.pending()] == [rid]
    overrides.set_fields(rid, deadline=time.time() - 1)
    assert overrides.load(rid)["state"] == overrides.PENDING      # not swept
    assert overrides.pending() == []
    with patch.object(overrides, "sweep", lambda *a, **k: []):
        again = _request()
    assert again["request_id"] != rid and not again.get("existing")


def test_merge_approve_cli_lists_only_live_requests(repo, capsys):
    from chela import main
    _seed(repo)
    rid = _request()["request_id"]
    with patch.object(overrides, "sweep", lambda *a, **k: []):
        main.cmd_merge_approve(SimpleNamespace(request=None, deny=False))
        assert rid in capsys.readouterr().out
        overrides.set_fields(rid, deadline=time.time() - 1)
        main.cmd_merge_approve(SimpleNamespace(request=None, deny=False))
    out = capsys.readouterr().out
    assert rid not in out and "No override is waiting" in out


@pytest.mark.parametrize("ci,mergeable,head", [(CI_FAILING, "MERGEABLE", HEAD),
                                               (CI_PASSING, "MERGEABLE", MOVED)])
def test_a_failed_or_expired_tap_never_reads_as_merged_or_denied(repo, ci, mergeable, head):
    from chela.telegram.overridewatch import handle_override_tap
    _seed(repo)
    rid = _request()["request_id"]
    with _github(ci=ci, mergeable=mergeable, head=head) as squash:
        toast = handle_override_tap(f"ov:a:{rid}")
    squash.assert_not_called()
    assert toast.startswith("Not merged") and "Denied" not in toast
    assert overrides.load(rid)["state"] in (overrides.FAILED, overrides.EXPIRED)


def test_a_failed_dashboard_approval_never_reads_as_merged_or_denied(repo):
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    _seed(repo)
    rid = _request()["request_id"]
    with _github(ci=CI_FAILING) as squash:
        resp = app.test_client().post(f"/override/{rid}", data={"decision": "approve"})
    squash.assert_not_called()
    assert resp.status_code == 410
    assert b"Approved and merged" not in resp.data and b"Denied" not in resp.data
    assert b"Not merged" in resp.data


def test_the_dashboard_deny_denies_and_merges_nothing(repo):
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        resp = app.test_client().post(f"/override/{rid}", data={"decision": "deny"})
    squash.assert_not_called()
    assert resp.status_code == 200 and b"Denied" in resp.data
    assert overrides.load(rid)["state"] == overrides.DENIED


@pytest.mark.parametrize("raw,expected", [("garbage", 24 * 3600.0), ("-5", 0.0), ("90", 90.0)])
def test_the_ttl_env_parses_safely(monkeypatch, raw, expected):
    monkeypatch.setenv(overrides.TTL_ENV, raw)
    assert overrides.ttl() == expected


def test_a_request_without_a_head_is_never_recorded():
    assert overrides.create({"task_id": "t1"}) is None
    assert overrides.create({"task_id": "t1", "head_sha": ""}) is None
    assert overrides.all_requests() == []


@pytest.mark.parametrize("bad", ["../../etc/passwd", "override-xyz", "override-" + "0" * 23, ""])
def test_a_malformed_request_id_resolves_nothing(repo, bad):
    _seed(repo)
    _request()
    assert overrides.load(bad) is None
    with _github() as squash:
        out = overrides.decide(bad, True, "terminal:op")
    squash.assert_not_called()
    assert out["ok"] is False and out["state"] is None
    assert overrides.set_fields(bad, state=overrides.MERGED) is None
    # a malformed id never names a file — not in the store, not anywhere it points to
    assert {p.name for p in overrides.store_dir().glob("*.lock")} == {
        f"{r['id']}.lock" for r in overrides.all_requests()}
    assert not [p for p in repo.parent.rglob("*.lock") if "passwd" in p.name]
    assert len(overrides.pending()) == 1


# --- orchestrator-added guards (judge round 4 survivors) ----------------------------------

def test_the_squash_merge_binds_github_to_the_approved_head(monkeypatch):
    """🔴 GUARD: GitHub must refuse the merge if a push landed after the re-check — the
    squash-merge passes `--match-head-commit <approved head>`."""
    seen: list[list[str]] = []

    class _Done:
        returncode, stdout, stderr = 1, "", "stop here"

    monkeypatch.setattr(contract.subprocess, "run",
                        lambda argv, **kw: (seen.append(list(argv)), _Done())[1])
    contract._squash_merge({"task_id": "t1"}, "/nonexistent", PR, body="b", match_head=HEAD)
    argv = seen[0]
    assert argv[:3] == ["gh", "pr", "merge"]
    i = argv.index("--match-head-commit")
    assert argv[i + 1] == HEAD


def test_a_request_lock_is_exclusive_across_open_files(tmp_path, monkeypatch):
    """🔴 GUARD: "approve twice ⇒ one merge" across processes rests on the request lock
    being EXCLUSIVE: while it is held, no other open of the lock file can take even a
    shared lock."""
    import fcntl

    monkeypatch.setattr(overrides, "store_dir", lambda: tmp_path)
    rid = overrides.new_request_id()
    with overrides._locked(rid):
        with open(tmp_path / f"{rid}.lock") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)


def test_the_bridges_override_loop_ticks_the_relay():
    """🔴 GUARD (wiring): the Telegram bridge's own loop must actually tick the override
    relay — that tick is what posts the Approve/Deny card and edits it to the outcome."""
    import threading

    from chela import main as chela_main

    stop = threading.Event()
    ticks: list[int] = []

    class _Relay:
        def tick(self):
            ticks.append(1)
            stop.set()

    t = threading.Thread(target=chela_main._override_loop, args=(_Relay(), 0, stop), daemon=True)
    t.start()
    t.join(5)
    assert ticks == [1], "the loop must call relay.tick()"


def test_an_override_request_notification_names_the_override_never_a_release(repo, monkeypatch):
    """🔴 GUARD: the operator's push for an override REQUEST — question, hint and title —
    says override, and the hint is the same approve line a release card uses."""
    sent = []
    monkeypatch.setattr(contract.notify, "enabled", lambda: True)
    monkeypatch.setattr(contract.notify, "send", lambda text, title=None, **k: sent.append(
        (text, title)))
    _seed(repo)
    result = _request(reason="flaky")
    rid = result["request_id"]
    assert result["question"] == (f"Override-merge cmx-1 into dev past the judge ('blocked' on "
                                  f"{HEAD[:12]})? Reason: flaky")
    assert result["hint"] == contract.approve_hint(rid) == (
        f"Approve with the Telegram button, on the dashboard (/override/{rid}), "
        f"or in a plain terminal: chela merge-approve {rid}  (deny: add --deny)")
    assert sent == [(f"{result['question']}\n\n{result['hint']}",
                     "chela: approve override of cmx-1?")]
    assert "release" not in sent[0][0].lower() and "release" not in sent[0][1]
