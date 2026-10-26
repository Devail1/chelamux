"""CMX-347: `chela keeper run` — model proposals, validated in code, approved by a person."""
from __future__ import annotations

import copy
import json
import subprocess

import pytest

from chela import config, inbox, keeper, keeper_run, main
from chela.sources import linear
from chela.sources.linear import LinearError, LinearSource


def digest():
    return {
        "tracker_key": "CMX", "cursor": {"seq": 9, "boot_id": "b", "ts": 100.0},
        "commits": {"items": [{"sha": "abc1234567", "ts": 1,
                               "subject": "CMX-5: wire the parser"}], "total": 1},
        "prs": {"items": [
            {"number": 41, "title": "CMX-6: ship the export", "state": "MERGED",
             "event": "merged", "ts": 2},
            {"number": 42, "title": "CMX-7: draft the docs", "state": "OPEN",
             "event": "opened", "ts": 3}], "total": 2},
        "sessions": {"items": [{
            "wid": "@3", "recap": "working on CMX-5",
            "prompts": {"items": [{"ts": 1, "text": "Start on   CMX-8 please"}], "total": 1},
            "edits": {"items": ["chela/parser.py"], "total": 1}}], "total": 1},
        "board": {"items": [
            {"id": "CMX-5", "title": "parser", "state": "Todo"},
            {"id": "CMX-6", "title": "export", "state": "In Progress"},
            {"id": "CMX-7", "title": "docs", "state": "In Progress"},
            {"id": "CMX-8", "title": "backlog item", "state": "Backlog"},
            {"id": "CMX-9", "title": "reviewing", "state": "In Review"}], "total": 5},
    }


def ev(kind, ref, quote):
    return {"kind": kind, "ref": ref, "quote": quote}


COMMIT = ev("commit", "abc1234567", "wire the parser")
MERGED = ev("pr", "#41", "ship the export")
VALID = [
    {"issue": "cmx-5", "action": "in_progress", "evidence": [COMMIT]},
    {"issue": "CMX-8", "action": "in_progress", "evidence": [ev("prompt", "@3.p1", "start on cmx-8")]},
    {"issue": "CMX-6", "action": "done", "evidence": [MERGED]},
    {"issue": "CMX-7", "action": "comment", "comment": "draft PR is open",
     "evidence": [ev("pr", "42", "draft the docs")]},
    {"issue": "CMX-7", "action": "flag_stale", "evidence": [ev("edit", "@3.e1", "parser.py")]},
    {"issue": "", "action": "new_backlog_issue", "title": "parser docs", "evidence": [COMMIT]},
]
INVALID = [
    ({"issue": "CMX-5", "action": "in_progress", "evidence": []}, "no evidence"),
    ({"issue": "CMX-5", "action": "in_progress"}, "no evidence"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("commit", "fffffff000", "x")]},
     "evidence commit:fffffff000 is not in the digest"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("pr", "abc1234567", "wire")]},
     "evidence pr:abc1234567 is not in the digest"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("commit", "abc1234567", "rm -rf")]},
     "evidence commit:abc1234567 does not say that"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("commit", "abc1234567", " ")]},
     "evidence commit:abc1234567 does not say that"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [COMMIT, "junk"]},
     "evidence is not an object"),
    ({"issue": "CMX-5", "action": "cancel", "evidence": [COMMIT]}, "action 'cancel' is not allowed"),
    ({"issue": "CMX-9", "action": "in_progress", "evidence": [COMMIT]},
     "CMX-9 is In Review, not Todo or Backlog"),
    ({"issue": "CMX-6", "action": "in_progress", "evidence": [MERGED]},
     "CMX-6 is In Progress, not Todo or Backlog"),
    ({"issue": "CMX-99", "action": "comment", "comment": "x", "evidence": [COMMIT]},
     "CMX-99 is not on the board"),
    ({"issue": "CMX-7", "action": "comment", "comment": " ", "evidence": [COMMIT]},
     "comment without text"),
    ({"issue": "", "action": "new_backlog_issue", "evidence": [COMMIT]}, "new issue without a title"),
    # done: an opened PR, a merged PR naming another issue, a commit naming it — all dropped.
    ({"issue": "CMX-7", "action": "done", "evidence": [ev("pr", "#42", "draft")]},
     "done without a merged PR that names the issue"),
    ({"issue": "CMX-7", "action": "done", "evidence": [MERGED]},
     "done without a merged PR that names the issue"),
    ({"issue": "CMX-5", "action": "done", "evidence": [COMMIT]},
     "done without a merged PR that names the issue"),
    ("not a dict", "not an object"),
]


def test_only_valid_proposals_survive():
    kept, dropped = keeper_run.validate(VALID + [p for p, _ in INVALID], digest())
    assert [(p["issue"], p["action"]) for p in kept] == [
        ("CMX-5", "in_progress"), ("CMX-8", "in_progress"), ("CMX-6", "done"),
        ("CMX-7", "comment"), ("CMX-7", "flag_stale"), ("", "new_backlog_issue")]
    assert [d["reason"] for d in dropped] == [why for _, why in INVALID]


@pytest.mark.parametrize("bad", [None, {"proposals": []}, "x"])
def test_a_non_list_answer_proposes_nothing(bad):
    assert keeper_run.validate(bad, digest()) == ([], [])


def test_lowercase_board_state_still_starts():
    d = digest()
    d["board"]["items"][0]["state"] = "BACKLOG"
    assert keeper_run.validate([VALID[0]], d)[0]


class FakeTracker:
    def __init__(self, fail_on=None):
        self.writes, self.fail_on = [], fail_on

    def _rec(self, *w):
        if self.fail_on and w[0] == self.fail_on:
            raise RuntimeError("tracker down")
        self.writes.append(w)

    def transition(self, issue, edge):
        self._rec("transition", issue, edge)
        return "set"

    def close_tasks(self, ids):
        self._rec("close", *ids)
        return {i: "struck" for i in ids}

    def comment(self, issue, body):
        self._rec("comment", issue, body)
        return "set"

    def create_issue(self, title, description=""):
        self._rec("create", title, description)
        return {"identifier": "CMX-100"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela")
    sent, saved = [], []
    monkeypatch.setattr(inbox, "enqueue", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(keeper, "build", lambda wf, since=None: digest())
    monkeypatch.setattr(keeper, "save_cursor", lambda wf, c: saved.append(c))
    monkeypatch.setattr(keeper, "repo_roots", lambda wf: ("/repo", ["/repo"]))
    return {"sent": sent, "saved": saved}


class WF:
    path = "/repo/WORKFLOW.md"

    def __init__(self, model=None):
        self.model = model

    def get(self, *keys, default=None):
        return self.model if keys == ("keeper", "model") and self.model else default


def rows():
    path = keeper_run.log_path()
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _run(proposals, error=None, **kw):
    seen = {}

    def asker(prompt, *, model, cwd):
        seen.update(prompt=prompt, model=model, cwd=cwd)
        return (None, error) if error else (copy.deepcopy(proposals), None)
    return keeper_run.run(WF(), asker=asker, **kw), seen


def test_run_logs_every_proposal_and_queues_one_inbox_item(state):
    out, seen = _run([VALID[0], VALID[2], INVALID[2][0]])
    assert seen["model"] == "sonnet" and seen["cwd"] == "/repo"
    assert seen["prompt"].startswith(keeper_run.PROMPT)
    facts = json.loads(seen["prompt"][len(keeper_run.PROMPT):])
    assert {"kind": "prompt", "ref": "@3.p1", "text": "Start on   CMX-8 please"} in facts["evidence"]
    assert {"kind": "pr", "ref": "#41", "text": "CMX-6: ship the export",
            "event": "merged"} in facts["evidence"]
    assert facts["recaps"] == [{"session": "@3", "recap": "working on CMX-5"}]
    assert out["dropped"] == 1 and len(out["proposals"]) == 2
    assert [r["outcome"] for r in rows()] == ["dropped", "proposed", "proposed"]
    assert rows()[0]["reason"] == "evidence commit:fffffff000 is not in the digest"
    assert {r["batch"] for r in rows()} == {out["batch"]}
    (kind, summary, payload), = state["sent"]
    assert kind == "keeper_proposals" and payload["batch"] == out["batch"]
    assert payload["proposals"] == out["proposals"] and payload["dropped"] == 1
    assert f"chela keeper approve {out['batch']}" in summary and "2 board change(s)" in summary
    assert state["saved"] == [digest()["cursor"]]


def test_a_failed_model_call_keeps_the_window_and_queues_nothing(state):
    out, _ = _run(None, error="budget exceeded")
    assert out == {"error": "budget exceeded"}
    assert state["saved"] == [] and state["sent"] == [] and rows() == []


def test_nothing_valid_queues_nothing(state):
    out, _ = _run([INVALID[0][0]])
    assert out["batch"] is None and state["sent"] == []
    assert [r["outcome"] for r in rows()] == ["dropped"]
    assert state["saved"]


def test_negative_control_skipping_validation_lets_forged_evidence_through(state, monkeypatch):
    monkeypatch.setattr(keeper_run, "validate", lambda ps, d: (ps, []))
    out, _ = _run([INVALID[4][0]])
    assert out["proposals"] == [INVALID[4][0]]       # what the real validate must stop


@pytest.mark.parametrize("flag, configured, want", [
    ("opus", "haiku", "opus"), (None, "haiku", "haiku"), (None, None, "sonnet")])
def test_model_precedence(flag, configured, want):
    assert keeper_run.model_for(WF(configured), flag) == want


def _batch(state, proposals):
    out, _ = _run(proposals)
    return out["batch"]


def test_approve_all_writes_exactly_the_proposals_attributed(state):
    batch = _batch(state, VALID)
    fake = FakeTracker()
    out = keeper_run.decide(batch, approve=True, by="liav", source=fake)
    assert out["results"] == ["set", "set", "struck", "set", "set", "set"]
    note = f"approved by liav, batch {batch}"
    kinds = [(w[0], w[1]) for w in fake.writes]
    assert kinds == [
        ("transition", "CMX-5"), ("comment", "CMX-5"), ("transition", "CMX-8"),
        ("comment", "CMX-8"), ("close", "CMX-6"), ("comment", "CMX-6"),
        ("comment", "CMX-7"), ("comment", "CMX-7"), ("create", "parser docs"),
        ("comment", "CMX-100")]
    assert fake.writes[0][2] == "in_progress"
    comments = [w[2] for w in fake.writes if w[0] == "comment"]
    assert all(note in c and "— chela keeper (" in c for c in comments)
    assert "commit abc1234567: “wire the parser”" in comments[0]
    assert comments[3].startswith("draft PR is open\n\n")
    assert comments[4].startswith("⚠️ Flagged stale\n\n")
    assert [r["outcome"] for r in rows()][-6:] == ["accepted"] * 6
    assert keeper_run.decide(batch, approve=True, source=fake)["error"].startswith(
        f"no pending batch {batch}")
    assert len(fake.writes) == 10


def test_dismiss_writes_nothing_and_logs_rejected(state):
    batch = _batch(state, VALID[:2])
    fake = FakeTracker()
    assert keeper_run.decide(batch, approve=False, source=fake)["results"] == ["rejected"] * 2
    assert fake.writes == [] and [r["outcome"] for r in rows()][-2:] == ["rejected"] * 2


def test_approve_one_by_one(state):
    batch = _batch(state, VALID[:3])
    fake = FakeTracker()
    assert keeper_run.decide(batch, approve=True, only={2}, source=fake)["results"] == [
        "rejected", "set", "rejected"]
    assert [w[:2] for w in fake.writes] == [("transition", "CMX-8"), ("comment", "CMX-8")]


def test_a_failed_write_is_logged_and_the_rest_still_apply(state):
    batch = _batch(state, [VALID[2], VALID[0]])
    fake = FakeTracker(fail_on="close")
    out = keeper_run.decide(batch, approve=True, source=fake)
    assert out["results"] == ["failed: tracker down", "set"]
    assert [r["outcome"] for r in rows()][-2:] == ["failed", "accepted"]


def test_no_comment_when_the_state_write_did_not_land(state):
    batch = _batch(state, [VALID[0]])
    fake = FakeTracker()
    fake.transition = lambda issue, edge: "already"
    assert keeper_run.decide(batch, approve=True, source=fake)["results"] == ["already"]
    assert fake.writes == []


@pytest.mark.parametrize("bad", ["../x", "ab/cd", "", "ａｂ"])
def test_a_bad_batch_id_is_refused(state, bad):
    assert keeper_run.decide(bad, approve=True, source=FakeTracker())["error"].startswith(
        "not a batch id")


def test_ask_model_pins_the_call(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, json.dumps(
            {"structured_output": {"proposals": [VALID[0]]}}), "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("CHELA_ACTOR", "x")
    assert keeper_run.ask_model("P", model="sonnet", cwd="/repo") == ([VALID[0]], None)
    assert seen["argv"] == [
        "claude", "-p", "P", "--output-format", "json", "--model", "sonnet", "--restricted",
        "--tools", "", "--strict-mcp-config", "--no-session-persistence",
        "--permission-prompts", "none", "--max-budget-usd", "1.00",
        "--json-schema", json.dumps(keeper_run.SCHEMA)]
    assert seen["cwd"] == "/repo" and seen["timeout"] == 600
    assert "CHELA_ACTOR" not in seen["env"]


@pytest.mark.parametrize("stdout, want", [
    ({"structured_output": {"proposals": "nope"}}, "the model returned no proposals list"),
    ({"is_error": True, "subtype": "error_max_budget", "result": "over"},
     "model call failed (error_max_budget): over")])
def test_ask_model_errors(monkeypatch, stdout, want):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, json.dumps(stdout), ""))
    assert keeper_run.ask_model("P", model="m", cwd="/") == (None, want)


class Transport:
    def __init__(self, reply=None, exc=None):
        self.calls, self.reply, self.exc = [], reply, exc

    def __call__(self, query, variables):
        self.calls.append((query, variables))
        if query == linear.COMMENT_MUTATION:
            if self.exc:
                raise self.exc
            return self.reply
        return {"teams": {"nodes": []}}


def _linear(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    (tmp_path / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n---\n")
    from chela.workflow import load_workflow
    return LinearSource(load_workflow(tmp_path / "WORKFLOW.md"), transport=transport)


@pytest.mark.parametrize("reply, exc, issue, body, want", [
    ({"commentCreate": {"success": True}}, None, "cmx-4", "hi", "set"),
    ({"commentCreate": {"success": False}}, None, "CMX-4", "hi", "failed"),
    (None, LinearError("network", "down"), "CMX-4", "hi", "failed"),
    ({"commentCreate": {"success": True}}, None, "ABC-4", "hi", "skipped"),
    ({"commentCreate": {"success": True}}, None, "CMX-4", "  ", "skipped")])
def test_linear_comment(tmp_path, monkeypatch, reply, exc, issue, body, want):
    t = Transport(reply, exc)
    assert _linear(t, tmp_path, monkeypatch).comment(issue, body) == want
    sent = [v for q, v in t.calls if q == linear.COMMENT_MUTATION]
    assert sent == ([] if want == "skipped" else [{"input": {"issueId": "CMX-4", "body": body}}])


def test_cli_run_and_approve(state, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(main.workflow, "load_workflow", lambda p: WF())
    monkeypatch.setattr(keeper_run, "ask_model", lambda prompt, *, model, cwd: (VALID[:2], None))
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "run", "W.md", "--model", "opus"])
    main.main()
    out = capsys.readouterr().out
    batch = out.split("batch ")[1].split()[0]
    assert out.startswith("keeper run: 2 proposal(s), 0 dropped by validation — batch ")
    assert "  1. in_progress CMX-5" in out
    fake = FakeTracker()
    monkeypatch.setattr("chela.sources.get_source", lambda wf: fake)
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "approve", batch, "--only", "2"])
    main.main()
    assert capsys.readouterr().out == "  1. rejected\n  2. set\n"
    assert [w[:2] for w in fake.writes] == [("transition", "CMX-8"), ("comment", "CMX-8")]


@pytest.mark.parametrize("argv, code", [
    (["run", "W.md", "--since", "soon"], 2), (["approve", "abc", "--only", "x"], 2),
    (["dismiss", "deadbeef"], 1)])
def test_cli_errors(state, monkeypatch, argv, code):
    monkeypatch.setattr(main.workflow, "load_workflow", lambda p: WF())
    monkeypatch.setattr("sys.argv", ["chela", "keeper", *argv])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == code


def test_cli_run_model_failure_exits_1(state, monkeypatch):
    monkeypatch.setattr(main.workflow, "load_workflow", lambda p: WF())
    monkeypatch.setattr(keeper_run, "ask_model", lambda prompt, *, model, cwd: (None, "boom"))
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "run", "W.md"])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 1
