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
    buttons = [b for row in post["markup"]["inline_keyboard"] for b in row]
    assert [decode_override_callback(b["callback_data"]) for b in buttons] == [
        (True, rid), (False, rid)]
    assert bot.edits == []


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
