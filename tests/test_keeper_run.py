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
        "commits": {"items": [{"sha": "abc1234567", "ts": 1, "subject": "CMX-5: wire the parser"},
                              {"sha": "def5678901", "ts": 2, "subject": "CMX-6: export the rows"}],
                    "total": 2},
        "prs": {"items": [
            {"number": 41, "title": "CMX-6: ship the export", "state": "MERGED",
             "event": "merged", "ts": 2},
            {"number": 42, "title": "CMX-7: draft the docs", "state": "OPEN",
             "event": "opened", "ts": 3}], "total": 2},
        "sessions": {"items": [{
            "wid": "@3", "recap": "working on CMX-5",
            "prompts": {"items": [{"ts": 1, "text": "Start on   CMX-8 please"},
                                  {"ts": 2, "text": "tidy the build scripts, then the parser"}],
                        "total": 2},
            "edits": {"items": ["chela/parser.py"], "total": 1}}], "total": 1},
        "board": {"items": [
            {"id": "CMX-5", "title": "Parser", "state": "Todo"},
            {"id": "CMX-6", "title": "export", "state": "In Progress"},
            {"id": "CMX-7", "title": "documentation", "state": "In Progress"},
            {"id": "CMX-8", "title": "backlog item", "state": "Backlog"},
            {"id": "CMX-9", "title": "reviewing", "state": "In Review"},
            {"id": "CMX-10", "title": "telemetry", "state": "In Progress"},
            {"id": "CMX-11", "title": "parser rewrite", "state": "In Progress"}], "total": 7},
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
    {"issue": "CMX-10", "action": "flag_stale", "evidence": [ev("edit", "@3.e1", "chela/parser.py")]},
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
    # long enough to pass the length floor, but the commit never said it
    ({"issue": "CMX-5", "action": "in_progress",
      "evidence": [ev("commit", "abc1234567", "drop the production database")]},
     "evidence commit:abc1234567 does not say that"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("commit", "abc1234567", " ")]},
     "evidence commit:abc1234567 does not say that"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [COMMIT, "junk"]},
     "evidence is not an object"),
    # a quote too short to mean anything, though it IS in the text
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("commit", "abc1234567", "the")]},
     "evidence commit:abc1234567 does not say that"),
    ({"issue": "CMX-5", "action": "in_progress",
      "evidence": [ev("commit", "abc1234567", "wire the parse")]}, None),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev(["pr"], "41", "ship the export")]},
     "evidence without a kind and a ref"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("pr", ["41"], "ship the export")]},
     "evidence without a kind and a ref"),
    ({"issue": "CMX-5", "action": "flag_stale", "evidence": [COMMIT]},
     "CMX-5 is Todo, not In Progress: only started work can be stale"),
    ({"issue": "CMX-8", "action": "flag_stale", "evidence": [COMMIT]},
     "CMX-8 is Backlog, not In Progress: only started work can be stale"),
    ({"issue": "CMX-9", "action": "flag_stale", "evidence": [COMMIT]},
     "CMX-9 is In Review, not In Progress: only started work can be stale"),
    # In Progress, but the window names it: by id alone (no title word of CMX-7 is in
    # PR #42), or by a title word.
    ({"issue": "CMX-7", "action": "flag_stale", "evidence": [COMMIT]},
     "CMX-7 is not stale: pr:#42 names it"),
    ({"issue": "CMX-6", "action": "flag_stale", "evidence": [COMMIT]},
     "CMX-6 is not stale: commit:def5678901 names it"),
    ({"issue": "CMX-11", "action": "flag_stale", "evidence": [COMMIT]},
     "CMX-11 is not stale: commit:abc1234567 names it"),
    # every cited item must be about THE issue: its id in the item, or a title word quoted
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("pr", "#42", "draft the docs")]},
     "evidence_unrelated: pr:#42 is not about CMX-5"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [COMMIT, ev("pr", "42", "draft the docs")]},
     "evidence_unrelated: pr:42 is not about CMX-5"),
    ({"issue": "CMX-7", "action": "comment", "comment": "x", "evidence": [COMMIT]},
     "evidence_unrelated: commit:abc1234567 is not about CMX-7"),
    ({"issue": "CMX-6", "action": "done", "evidence": [MERGED, COMMIT]},
     "evidence_unrelated: commit:abc1234567 is not about CMX-6"),
    ({"issue": "CMX-8", "action": "in_progress", "evidence": [ev("edit", "@3.e1", "chela/parser.py")]},
     "evidence_unrelated: edit:@3.e1 is not about CMX-8"),
    ({"issue": "CMX-5", "action": "in_progress", "evidence": [ev("edit", "@3.e1", "chela/parser.py")]},
     None),                                    # no id, but it quotes the title word "parser"
    ({"issue": "CMX-7", "action": "comment", "comment": "x", "evidence": [ev("pr", 42, "draft the docs")]},
     None),                                    # an int ref is the same PR
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
    ({"issue": "CMX-7", "action": "done", "evidence": [ev("pr", "#42", "draft the docs")]},
     "done without a merged PR that names the issue"),
    ({"issue": "CMX-7", "action": "done", "evidence": [MERGED]},
     "done without a merged PR that names the issue"),
    ({"issue": "CMX-5", "action": "done", "evidence": [COMMIT]},
     "done without a merged PR that names the issue"),
    # a merged PR naming CMX-6 IS in the digest, but this proposal does not cite it
    ({"issue": "CMX-6", "action": "done", "evidence": [ev("commit", "def5678901", "export the rows")]},
     "done without a merged PR that names the issue"),
    ("not a dict", "not an object"),
]


def test_only_valid_proposals_survive():
    kept, dropped = keeper_run.validate(VALID + [p for p, _ in INVALID], digest())
    assert [(p["issue"], p["action"]) for p in kept] == [
        ("CMX-5", "in_progress"), ("CMX-8", "in_progress"), ("CMX-6", "done"),
        ("CMX-7", "comment"), ("CMX-10", "flag_stale"), ("", "new_backlog_issue"),
        ("CMX-5", "in_progress"), ("CMX-5", "in_progress"), ("CMX-7", "comment")]
    assert [d["reason"] for d in dropped] == [why for _, why in INVALID if why]


STALE = {"issue": "CMX-10", "action": "flag_stale", "evidence": [COMMIT]}


@pytest.mark.parametrize("kind,ref,text", [
    ("commit", "abc1234567", "CMX-10: wire the parser"),
    ("pr", "#41", "CMX-10: ship it"),
    ("prompt", "@3.p1", "pick up cmx-10 again"),
    ("edit", "@3.e1", "notes/cmx-10.md"),
])
@pytest.mark.parametrize("by", ["id", "title word"])
def test_flag_stale_is_refused_when_any_item_names_the_issue(kind, ref, text, by):
    """Each way of naming an issue, ALONE, keeps it from being stale: the id with no title
    word (CMX-10 is "telemetry"), and the title word with no id — in every kind of item."""
    if by == "title word":
        text = text.lower().replace("cmx-10", "telemetry")
    d = digest()
    items = {"commit": d["commits"]["items"][0], "pr": d["prs"]["items"][0]}
    if kind in items:
        items[kind]["subject" if kind == "commit" else "title"] = text
    else:
        d["sessions"]["items"][0][kind + "s"]["items"][0] = (
            {"ts": 1, "text": text} if kind == "prompt" else text)
    kept, dropped = keeper_run.validate([STALE], d)
    assert kept == []
    assert [x["reason"] for x in dropped] == [f"CMX-10 is not stale: {kind}:{ref} names it"]
    assert keeper_run.validate([STALE], digest())[0] == [STALE]       # and unnamed, it is stale


def test_a_title_word_counts_only_when_it_is_in_the_quote():
    """@3.p2 holds "parser" (CMX-5's title), but only outside the quoted fragment."""
    off = {"issue": "CMX-5", "action": "in_progress",
           "evidence": [ev("prompt", "@3.p2", "tidy the build scripts")]}
    on = {"issue": "CMX-5", "action": "in_progress",
          "evidence": [ev("prompt", "@3.p2", "then the parser")]}
    kept, dropped = keeper_run.validate([off, on], digest())
    assert kept == [on]
    assert [x["reason"] for x in dropped] == ["evidence_unrelated: prompt:@3.p2 is not about CMX-5"]


@pytest.mark.parametrize("title,about", [
    ("a fix for the cli", False),     # every word is under KEYWORD_MIN_CHARS: "the" is quoted
    ("the wire", True),               # "wire" is exactly KEYWORD_MIN_CHARS long
    ("WIRE", True),                   # case does not matter
])
def test_only_title_words_of_keyword_length_make_an_item_about_the_issue(title, about):
    d = digest()
    d["board"]["items"][3]["title"] = title                     # CMX-8; COMMIT names CMX-5
    p = {"issue": "CMX-8", "action": "in_progress", "evidence": [COMMIT]}
    kept, dropped = keeper_run.validate([p], d)
    assert kept == ([p] if about else [])
    assert [x["reason"] for x in dropped] == (
        [] if about else ["evidence_unrelated: commit:abc1234567 is not about CMX-8"])


def test_a_short_item_is_quoted_whole():
    d = digest()
    d["sessions"]["items"][0]["edits"]["items"] = ["a.py"]
    p = {"issue": "CMX-10", "action": "flag_stale", "evidence": [ev("edit", "@3.e1", "a.py")]}
    assert keeper_run.validate([p], d)[0] == [p]
    p["evidence"] = [ev("edit", "@3.e1", "a.p")]
    assert keeper_run.validate([p], d)[0] == []


@pytest.mark.parametrize("bad", [None, {"proposals": []}, "x"])
def test_a_non_list_answer_proposes_nothing(bad):
    assert keeper_run.validate(bad, digest()) == ([], [])


def test_lowercase_board_state_still_starts():
    d = digest()
    d["board"]["items"][0]["state"] = "BACKLOG"
    assert keeper_run.validate([VALID[0]], d)[0]


class FakeTracker:
    def __init__(self, fail_on=None, comment_says="set"):
        self.writes, self.fail_on, self.comment_says = [], fail_on, comment_says

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
        return self.comment_says

    def create_issue(self, title, description="", state_names=None):
        self._rec("create", title, description, state_names)
        return {"identifier": "CMX-100"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela")
    sent, saved, built = [], [], []
    monkeypatch.setattr(inbox, "enqueue", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(keeper, "build", lambda wf, since=None: built.append(since) or digest())
    monkeypatch.setattr(keeper, "save_cursor", lambda wf, c: saved.append(c))
    monkeypatch.setattr(keeper, "repo_roots", lambda wf: ("/repo", ["/repo"]))
    return {"sent": sent, "saved": saved, "built": built}


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
    out, seen = _run([VALID[0], VALID[2], INVALID[2][0]], since=600)
    assert seen["model"] == "sonnet" and seen["cwd"] == "/repo" and state["built"] == [600]
    assert seen["prompt"].startswith(keeper_run.PROMPT)
    facts = json.loads(seen["prompt"][len(keeper_run.PROMPT):])
    assert {"kind": "prompt", "ref": "@3.p1", "text": "Start on   CMX-8 please"} in facts["evidence"]
    assert {"kind": "pr", "ref": "#41", "text": "CMX-6: ship the export",
            "event": "merged"} in facts["evidence"]
    assert facts["recaps"] == [{"session": "@3", "recap": "working on CMX-5"}]
    assert facts["board"] == digest()["board"]["items"]
    assert out["dropped"] == 1 and len(out["proposals"]) == 2
    assert [r["outcome"] for r in rows()] == ["dropped", "proposed", "proposed"]
    assert rows()[0]["reason"] == "evidence commit:fffffff000 is not in the digest"
    assert {r["batch"] for r in rows()} == {out["batch"]}
    (kind, summary, payload), = state["sent"]
    assert kind == "keeper_proposals" and payload["batch"] == out["batch"]
    assert payload["proposals"] == out["proposals"] and payload["dropped"] == 1
    assert payload["workflow"] == WF.path
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
    out = keeper_run.decide(batch, approve=True, by="liav", source=fake, env={})
    assert out["results"] == ["set", "set", "struck", "set", "set", "created CMX-100"]
    note = f"approved by liav, batch {batch}"
    kinds = [(w[0], w[1]) for w in fake.writes]
    assert kinds == [
        ("transition", "CMX-5"), ("comment", "CMX-5"), ("transition", "CMX-8"),
        ("comment", "CMX-8"), ("close", "CMX-6"), ("comment", "CMX-6"),
        ("comment", "CMX-7"), ("comment", "CMX-10"), ("create", "parser docs"),
        ("comment", "CMX-100")]
    assert fake.writes[0][2] == "in_progress"
    assert fake.writes[8][3] == ("Backlog",)       # never a READY state the dispatcher claims
    comments = [w[2] for w in fake.writes if w[0] == "comment"]
    assert all(note in c and "— chela keeper (" in c for c in comments)
    assert "— chela keeper (in_progress, approved by liav" in comments[0]
    assert "— chela keeper (done, approved by liav" in comments[2]
    assert "commit abc1234567: “wire the parser”" in comments[0]
    assert comments[3].startswith("draft PR is open\n\n")
    assert comments[4].startswith("⚠️ Flagged stale\n\n")
    assert [r["outcome"] for r in rows()][-6:] == ["accepted"] * 6
    assert keeper_run.decide(batch, approve=True, source=fake, env={})["error"].startswith(
        f"no pending batch {batch}")
    assert len(fake.writes) == 10


def test_a_missing_attribution_comment_is_named(state):
    batch = _batch(state, [VALID[0], VALID[2], VALID[5], VALID[3]])
    fake = FakeTracker(comment_says="failed")
    assert keeper_run.decide(batch, approve=True, source=fake, env={})["results"] == [
        "set (attribution comment failed)", "struck (attribution comment failed)",
        "created CMX-100 (attribution comment failed)", "failed"]
    assert [r["outcome"] for r in rows()][-4:] == ["accepted", "accepted", "accepted", "failed"]


@pytest.mark.parametrize("env, word", [({"CLAUDECODE": "1"}, "Claude Code"),
                                       ({"CHELA_ACTOR": "orchestrator"}, "CHELA_ACTOR")])
def test_an_agent_cannot_decide(state, env, word):
    batch = _batch(state, VALID[:1])
    fake = FakeTracker()
    for approve in (True, False):
        assert word in keeper_run.decide(batch, approve=approve, source=fake, env=env)["error"]
    assert fake.writes == [] and keeper_run._batch_path(batch).exists()


@pytest.mark.parametrize("only", [set(), {0}, {4}, {1, 4}])
def test_a_bad_selection_leaves_the_batch_pending(state, only):
    batch = _batch(state, VALID[:3])
    fake = FakeTracker()
    assert keeper_run.decide(batch, approve=True, only=only, source=fake, env={}) == {
        "error": "--only must name proposals 1 to 3"}
    assert fake.writes == []
    assert keeper_run.decide(batch, approve=False, source=fake, env={})["results"]


def test_an_unopenable_tracker_leaves_the_batch_pending(state, monkeypatch):
    batch = _batch(state, VALID[:1])

    def boom(path):
        raise RuntimeError("WORKFLOW.md moved")
    monkeypatch.setattr("chela.workflow.load_workflow", boom)
    assert keeper_run.decide(batch, approve=True, env={}) == {
        "error": "the tracker could not be opened: WORKFLOW.md moved"}
    assert keeper_run._batch_path(batch).exists()


def test_an_unsaveable_batch_keeps_the_window(state, monkeypatch):
    def deny(self, *a, **kw):
        raise PermissionError("read-only")
    monkeypatch.setattr(keeper_run.Path, "write_text", deny)
    out, _ = _run(VALID[:1])
    assert out == {"error": "the batch could not be saved: read-only"}
    assert state["saved"] == [] and state["sent"] == [] and rows() == []


def test_a_failed_enqueue_still_keeps_the_batch(state, monkeypatch):
    def down(*a):
        raise RuntimeError("inbox locked")
    monkeypatch.setattr(inbox, "enqueue", down)
    out, _ = _run(VALID[:1])
    assert out["notified"] is False and keeper_run._batch_path(out["batch"]).exists()
    assert state["saved"] == [digest()["cursor"]]


def test_dismiss_writes_nothing_and_logs_rejected(state):
    batch = _batch(state, VALID[:2])
    fake = FakeTracker()
    assert keeper_run.decide(batch, approve=False, by="liav", source=fake, env={})["results"] == [
        "rejected"] * 2
    assert fake.writes == [] and [r["outcome"] for r in rows()][-2:] == ["rejected"] * 2
    assert [r["by"] for r in rows()[-2:]] == ["liav"] * 2


def test_approve_one_by_one(state):
    batch = _batch(state, VALID[:3])
    fake = FakeTracker()
    assert keeper_run.decide(batch, approve=True, only={2}, source=fake, env={})["results"] == [
        "rejected", "set", "rejected"]
    assert [w[:2] for w in fake.writes] == [("transition", "CMX-8"), ("comment", "CMX-8")]


def test_a_failed_write_is_logged_and_the_rest_still_apply(state):
    batch = _batch(state, [VALID[2], VALID[0]])
    fake = FakeTracker(fail_on="close")
    out = keeper_run.decide(batch, approve=True, source=fake, env={})
    assert out["results"] == ["failed: tracker down", "set"]
    assert [r["outcome"] for r in rows()][-2:] == ["failed", "accepted"]


def test_an_already_applied_write_is_accepted_without_a_comment(state):
    """The board already said so: the person's approval stands (accepted, which the
    acceptance rate counts), but nothing changed, so there is nothing to attribute."""
    batch = _batch(state, [VALID[0], VALID[2]])
    fake = FakeTracker()
    fake.transition = lambda issue, edge: "already"
    fake.close_tasks = lambda ids: {i: "already" for i in ids}
    assert keeper_run.decide(batch, approve=True, source=fake, env={})["results"] == [
        "already", "already"]
    assert fake.writes == []
    assert [(r["outcome"], r["result"]) for r in rows()[-2:]] == [("accepted", "already")] * 2


def test_approve_reopens_the_tracker_of_the_proposing_workflow(state, monkeypatch):
    batch = _batch(state, VALID[:1])
    opened, fake = [], FakeTracker()
    monkeypatch.setattr("chela.workflow.load_workflow", lambda path: opened.append(path) or "wf")
    monkeypatch.setattr("chela.sources.get_source", lambda wf: fake if wf == "wf" else None)
    assert keeper_run.decide(batch, approve=True, env={})["results"] == ["set"]
    assert opened == [WF.path] and [w[:2] for w in fake.writes][0] == ("transition", "CMX-5")


def test_the_stored_batch_is_the_validated_one(state):
    """Approval applies what survived validation, never what the model said."""
    batch = _batch(state, [p for p, _ in INVALID[:6]] + [VALID[0]])
    fake = FakeTracker()
    assert keeper_run.decide(batch, approve=True, source=fake, env={})["results"] == ["set"]
    assert [w[:2] for w in fake.writes] == [("transition", "CMX-5"), ("comment", "CMX-5")]


@pytest.mark.parametrize("bad", ["../x", "ab/cd", "", "ａｂ"])
def test_a_bad_batch_id_is_refused(state, bad):
    assert keeper_run.decide(bad, approve=True, source=FakeTracker(), env={})["error"].startswith(
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


PAGE = {"pageInfo": {"hasNextPage": False, "endCursor": None}}


class Transport:
    def __init__(self, reply=None, exc=None, issues=("CMX-4",), read_fails=False):
        self.calls, self.reply, self.exc = [], reply, exc
        self.issues, self.read_fails = issues, read_fails

    def __call__(self, query, variables):
        self.calls.append((query, variables))
        if query == linear.COMMENT_MUTATION:
            if self.exc:
                raise self.exc
            return self.reply
        if query == linear.ISSUES_BY_NUMBER_QUERY:
            if self.read_fails:
                raise LinearError("network", "down")
            return {"issues": {"nodes": [{"id": f"uuid-{i}", "identifier": i}
                                         for i in self.issues], **PAGE}}
        if query == linear.TEAM_STATES_QUERY:
            return {"teams": {"nodes": [{"id": "team", "states": {"nodes": [
                {"id": "st-todo", "name": "Todo"}, {"id": "st-backlog", "name": "Backlog"}]}}]}}
        if query == linear.CREATE_ISSUE_MUTATION:
            return {"issueCreate": {"success": True, "issue": {
                "id": "u", "identifier": "CMX-50", "title": variables["input"]["title"]}}}
        raise AssertionError(query)


def _linear(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    (tmp_path / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n---\n")
    from chela.workflow import load_workflow
    return LinearSource(load_workflow(tmp_path / "WORKFLOW.md"), transport=transport)


OK = {"commentCreate": {"success": True}}


@pytest.mark.parametrize("kw, issue, body, want, sent", [
    ({"reply": OK}, "cmx-4", "hi", "set", True),
    ({"reply": {"commentCreate": {"success": False}}}, "CMX-4", "hi", "failed", True),
    ({"exc": LinearError("network", "down")}, "CMX-4", "hi", "failed", True),
    ({"reply": OK, "read_fails": True}, "CMX-4", "hi", "failed", False),
    ({"reply": OK, "issues": ("CMX-40",)}, "CMX-4", "hi", "missing", False),
    ({"reply": OK}, "ABC-4", "hi", "skipped", False),
    ({"reply": OK}, "CMX-4", "  ", "skipped", False)])
def test_linear_comment(tmp_path, monkeypatch, kw, issue, body, want, sent):
    t = Transport(**kw)
    assert _linear(t, tmp_path, monkeypatch).comment(issue, body) == want
    assert [v for q, v in t.calls if q == linear.COMMENT_MUTATION] == (
        [{"input": {"issueId": "uuid-CMX-4", "body": body}}] if sent else [])


@pytest.mark.parametrize("states, want", [(None, "st-todo"), (("Backlog",), "st-backlog"),
                                          (("Nope", "Backlog"), "st-backlog")])
def test_linear_create_issue_state(tmp_path, monkeypatch, states, want):
    t = Transport()
    src = _linear(t, tmp_path, monkeypatch)
    assert src.create_issue("x", state_names=states)["identifier"] == "CMX-50"
    created, = [v for q, v in t.calls if q == linear.CREATE_ISSUE_MUTATION]
    assert created["input"]["stateId"] == want


def test_linear_create_issue_unknown_states(tmp_path, monkeypatch):
    with pytest.raises(LinearError, match="no state named Nope / Gone"):
        _linear(Transport(), tmp_path, monkeypatch).create_issue("x", state_names=("Nope", "Gone"))


def test_cli_run_and_approve(state, monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CHELA_ACTOR", raising=False)
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


def test_cli_empty_only_is_not_approve_all(state, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CHELA_ACTOR", raising=False)
    batch = _batch(state, VALID[:2])
    fake = FakeTracker()
    monkeypatch.setattr("chela.sources.get_source", lambda wf: fake)
    monkeypatch.setattr(main.workflow, "load_workflow", lambda p: WF())
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "approve", batch, "--only", ""])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 1 and fake.writes == []
    assert "--only must name proposals 1 to 2" in capsys.readouterr().err


def test_cli_dismiss_writes_nothing(state, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CHELA_ACTOR", raising=False)
    monkeypatch.setenv("USER", "liav")
    batch = _batch(state, VALID[:2])
    fake = FakeTracker()
    monkeypatch.setattr("chela.sources.get_source", lambda wf: fake)
    monkeypatch.setattr(main.workflow, "load_workflow", lambda p: WF())
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "dismiss", batch])
    main.main()
    assert capsys.readouterr().out == "  1. rejected\n  2. rejected\n" and fake.writes == []
    assert [(r["outcome"], r["by"]) for r in rows()[-2:]] == [("rejected", "liav")] * 2


def test_cli_says_when_the_inbox_is_off(state, monkeypatch, capsys):
    monkeypatch.setattr(inbox, "enqueue", lambda *a: False)
    monkeypatch.setattr(main.workflow, "load_workflow", lambda p: WF())
    monkeypatch.setattr(keeper_run, "ask_model", lambda prompt, *, model, cwd: (VALID[:1], None))
    monkeypatch.setattr("sys.argv", ["chela", "keeper", "run", "W.md"])
    main.main()
    assert "not queued in the inbox" in capsys.readouterr().out


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


def test_linear_comment_with_a_config_error_writes_nothing(tmp_path, monkeypatch):
    t = Transport(reply=OK)
    src = _linear(t, tmp_path, monkeypatch)
    src.config_error = "no API key"
    t.calls.clear()
    assert src.comment("CMX-4", "hi") == "failed"
    assert t.calls == []
