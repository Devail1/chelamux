"""CMX-320: the notification digest — interrupt only for needs-you events, batch the rest.

Pins: each event kind routed by the rules (defaults, a custom list, the feature off); the
four parts rendered from a fixture event log; the 150-word cap and the link on every line;
and the negative control — a needs-you event is delivered on the tick it is queued, never
held for the digest.
"""
from __future__ import annotations

import json
import time
from argparse import Namespace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from chela import digest, event_log, inbox, main, messenger, notify, userconfig

ORCH = "@1"
JLM = ZoneInfo("Asia/Jerusalem")
DASH = "http://dash.test"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_DIGEST_STATE", str(tmp_path / "digest.json"))
    monkeypatch.setenv("CHELA_DASHBOARD_URL", DASH)
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(userconfig, "_PATH", tmp_path / "config.json")
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))
    return tmp_path


def _config(tmp_path, **digest_cfg):
    (tmp_path / "config.json").write_text(json.dumps({"digest": digest_cfg}))


ON = {"enabled": True, "interrupt": list(digest.DEFAULT_INTERRUPT), "times": ["09:00", "16:00"],
      "tz": "Asia/Jerusalem"}


# --- routing ------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [
    "run_needs_human",
    "orchestrator.merge_override_requested",
    "orchestrator.release_requested",
    "override_approved", "override_failed", "release_denied",
    "run_failed", "orchestrator.merge_failed", "release_major_tag_failed",
    "run_ci_known_flake",
    "suite_oom_kill", "judge_oom",
    "run_permission_denied",
    "keeper_proposals",
    "orchestrator.moved",
    "orchestrator.escalation.unknown_ci",
    "run_merged_outside_gate", "run_unjudged_merged",
    "digest",
])
def test_needs_you_kinds_interrupt_by_default(kind):
    assert digest.route(kind, digest.rules({"enabled": True})) == "interrupt"


@pytest.mark.parametrize("kind", [
    "run_review", "run_judge_clean", "run_judge_not_started", "run_ci_flake", "run_ci_red",
    "run_pr_conflicting", "run_stuck", "run_changes_requested", "run_selfcheck_long",
    "run_parked", "run_park_overdue", "run_reopened", "ops_findings", "nightly_sweep",
    "split_filed", "run_base_merge_infra", "finished", "room_delivery", "room_receipt",
    "", None,
])
def test_everything_else_waits_for_the_digest_by_default(kind):
    assert digest.route(kind, digest.rules({"enabled": True})) == "digest"


@pytest.mark.parametrize("kind", ["run_review", "run_needs_human", "ops_findings"])
def test_with_the_feature_off_every_kind_interrupts(kind):
    assert digest.route(kind, digest.rules({})) == "interrupt"
    assert digest.route(kind, digest.rules({"enabled": "true"})) == "interrupt"


def test_a_configured_interrupt_list_replaces_the_defaults():
    rule = digest.rules({"enabled": True, "interrupt": ["run_review", "run_ci_*"]})
    assert digest.route("run_review", rule) == "interrupt"
    assert digest.route("run_ci_red", rule) == "interrupt"
    assert digest.route("run_needs_human", rule) == "digest"
    assert digest.route("digest", rule) == "interrupt"        # the digest itself, always


def test_a_pattern_matches_the_whole_kind_not_a_prefix():
    rule = digest.rules({"enabled": True, "interrupt": ["run_failed"]})
    assert digest.route("run_failed", rule) == "interrupt"
    assert digest.route("run_failed_twice", rule) == "digest"
    assert digest.route("xrun_failed", rule) == "digest"


@pytest.mark.parametrize("stored, want", [
    (None, {"enabled": False, "interrupt": list(digest.DEFAULT_INTERRUPT),
            "times": ["09:00", "16:00"], "tz": None}),
    ({"enabled": True}, {"enabled": True, "interrupt": list(digest.DEFAULT_INTERRUPT),
                         "times": ["09:00", "16:00"], "tz": None}),
    ({"enabled": True, "interrupt": ["a"], "times": ["07:30"], "tz": "UTC"},
     {"enabled": True, "interrupt": ["a"], "times": ["07:30"], "tz": "UTC"}),
    ({"enabled": 1, "interrupt": "run_review", "times": ["25:00"], "tz": 3},
     {"enabled": False, "interrupt": list(digest.DEFAULT_INTERRUPT),
      "times": ["09:00", "16:00"], "tz": None}),
    ({"enabled": True, "times": []},
     {"enabled": True, "interrupt": list(digest.DEFAULT_INTERRUPT),
      "times": ["09:00", "16:00"], "tz": None}),
    ({"enabled": True, "interrupt": ["a", 2], "times": ["9:5"]},
     {"enabled": True, "interrupt": list(digest.DEFAULT_INTERRUPT),
      "times": ["09:00", "16:00"], "tz": None}),
    ("not an object", {"enabled": False, "interrupt": list(digest.DEFAULT_INTERRUPT),
                       "times": ["09:00", "16:00"], "tz": None}),
])
def test_rules_read_config_json_and_fall_back_per_field(sandbox, stored, want):
    if stored is not None:
        (sandbox / "config.json").write_text(json.dumps({"digest": stored}))
    assert digest.rules() == want


def test_an_unreadable_config_never_withholds_an_event(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("config.json is mid-write")
    monkeypatch.setattr(userconfig, "get", boom)
    assert digest.rules()["enabled"] is False
    assert digest.route("run_review") == "interrupt"


# --- the inbox gate, and the negative control ---------------------------------------

def _store(*kinds, unlogged=False):
    """A queue of events with no task_id, so delivery's staleness re-check passes them."""
    store = inbox._empty()
    store["orchestrator"] = ORCH
    for k in kinds:
        ev = inbox._event(k, f"{k} happened", {"subject": "CMX-1"})
        if unlogged:
            ev["unlogged"] = True
        store["queue"].append(ev)
    return store


@pytest.fixture
def sends(monkeypatch):
    calls = []
    monkeypatch.setattr(inbox.messenger, "send_tmux",
                        lambda wid, text: (calls.append((wid, text)), True)[1])
    return calls


def test_a_needs_you_event_is_delivered_now_never_held_for_the_digest(sandbox, sends):
    """NEGATIVE CONTROL: rules on, no digest due — the needs-human event still goes out on
    this tick, ahead of the run_review queued before it."""
    _config(sandbox, **ON)
    store = _store("run_review", "run_needs_human")
    sent = inbox.deliver(store, {ORCH: inbox.IDLE})
    assert [e["kind"] for e in sent] == ["run_needs_human"]
    assert sends == [(ORCH, "run_needs_human happened")]
    assert store["queue"] == []


def test_with_the_rules_off_the_queue_is_delivered_as_before(sandbox, sends):
    store = _store("run_review", "run_needs_human")
    sent = inbox.deliver(store, {ORCH: inbox.IDLE})
    assert [e["kind"] for e in sent] == ["run_review"]
    assert [e["kind"] for e in store["queue"]] == ["run_needs_human"]


def test_held_events_leave_the_queue_even_while_the_orchestrator_is_busy(sandbox, sends):
    _config(sandbox, **ON)
    store = _store("run_judge_clean", "run_needs_human")
    assert inbox.deliver(store, {ORCH: inbox.WAITING}) == []
    assert [e["kind"] for e in store["queue"]] == ["run_needs_human"]


def test_a_held_enqueued_event_is_appended_to_the_event_log(sandbox):
    _config(sandbox, **ON)
    store = _store("ops_findings", unlogged=True)
    held = inbox.hold_for_digest(store)
    assert [e["kind"] for e in held] == ["ops_findings"]
    logged = event_log.read(types=["ops_findings"])["events"]
    assert [(e["type"], e["summary"]) for e in logged] == [("ops_findings", "ops_findings happened")]


def test_a_held_event_the_tick_already_logged_is_not_logged_twice(sandbox):
    _config(sandbox, **ON)
    inbox.hold_for_digest(_store("run_judge_clean"))
    assert event_log.read(types=["run_judge_clean"])["events"] == []


def test_enqueue_marks_its_event_unlogged_and_the_gate_logs_it(sandbox, sends):
    _config(sandbox, **ON)
    assert inbox.enqueue("split_filed", "CMX-9 was split", {"task_id": "CMX-9"})
    assert inbox.load()["queue"][0]["unlogged"] is True
    with inbox.locked_store() as store:
        inbox.deliver(store, {ORCH: inbox.IDLE})
    assert inbox.load()["queue"] == []
    assert sends == []
    assert [e["summary"] for e in event_log.read(types=["split_filed"])["events"]] == \
        ["CMX-9 was split"]


# --- the digest from a fixture event log --------------------------------------------

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=JLM).timestamp()
PR = "https://github.com/acme/repo/pull/"


def _ev(seq, minutes, type_, summary="", **payload):
    return {"seq": seq, "ts": T0 + minutes * 60, "type": type_, "summary": summary,
            "payload": payload}


FIXTURE = [
    # before the last digest: a run that went into review and is still there
    _ev(1, -300, "run_review", task_id="CMX-10", pr_url=PR + "10"),
    # before the last digest, and merged before it: not done-since-last, not waiting
    _ev(2, -290, "orchestrator.merge", task_id="CMX-11", pr_url=PR + "11"),
    # since: two merges, one with its judge verdict logged AFTER the merge
    _ev(3, 10, "orchestrator.merge", task_id="CMX-12", pr_url=PR + "12"),
    _ev(4, 11, "run_judge_clean", task_id="CMX-12", pr_url=PR + "12"),
    _ev(5, 20, "orchestrator.release_pushed", tag="v1.2.0"),
    # waiting: parked; judge-clean-unmerged; one that went back to its agent (not waiting)
    _ev(6, 30, "run_parked", task_id="CMX-13"),
    _ev(7, 31, "run_judge_clean", task_id="CMX-14", pr_url=PR + "14"),
    _ev(8, 32, "run_review", task_id="CMX-15", pr_url=PR + "15"),
    _ev(9, 33, "run_changes_requested", task_id="CMX-15", pr_url=PR + "15"),
    # needs you: still open; and one resolved by a later quieter event
    _ev(10, 40, "run_needs_human", task_id="CMX-16", pr_url=PR + "16"),
    _ev(11, 41, "orchestrator.merge_override_requested", task_id="CMX-17", pr_url=PR + "17"),
    _ev(12, 42, "orchestrator.merge", task_id="CMX-17", pr_url=PR + "17"),
    # changed
    _ev(13, 50, "task.created", "created CMX-18: x", identifier="CMX-18",
        url="https://linear.app/acme/issue/CMX-18"),
    _ev(14, 51, "run_reopened", task_id="CMX-11", pr_url=PR + "11"),
    # noise that belongs to no part
    _ev(15, 52, "daemon_start"),
]


def _write_fixture(events):
    for e in events:
        rec = event_log.append(e["type"], e["summary"], e["payload"])
        assert rec is not None
    # pin the fixture's timestamps (append stamps "now")
    path = event_log.core_path()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row, e in zip(rows, events):
        row["ts"] = e["ts"]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_the_digest_renders_four_parts_from_a_fixture_event_log(sandbox):
    _config(sandbox, **ON)
    _write_fixture(FIXTURE)
    text, payload = digest.compose(now=T0 + 3600, since=T0)
    assert text.splitlines() == [
        "chela digest since Tue 09:00",
        "Needs you 1",
        f"• CMX-16 needs a human {PR}16",
        "Waiting on others 3",
        f"• CMX-14 judge clean, awaiting merge {PR}14",
        f"• CMX-13 parked {DASH}/",
        f"• CMX-10 in review {PR}10",
        "Done since last 3",
        f"• CMX-17 merged {PR}17",
        f"• v1.2.0 released {DASH}/",
        f"• CMX-12 merged {PR}12",
        "Changed 2",
        f"• CMX-11 reopened {PR}11",
        "• CMX-18 new issue https://linear.app/acme/issue/CMX-18",
    ]
    assert payload == {"since": T0, "counts": {"needs_you": 1, "waiting": 3, "done": 3,
                                               "changed": 2}}


def test_a_needs_you_event_stays_until_something_resolves_it():
    events = [_ev(1, 0, "run_needs_human", task_id="CMX-1", pr_url=PR + "1"),
              _ev(2, 1, "run_stuck", task_id="CMX-1"),
              _ev(3, 2, "room_delivery", task_id="CMX-1"),
              _ev(4, 3, "daemon_start", task_id="CMX-1")]
    assert [e["seq"] for e in digest.build(events, T0 - 1, digest.rules({}))["needs_you"]] == [1]
    events.append(_ev(5, 4, "run_review", task_id="CMX-1"))
    assert digest.build(events, T0 - 1, digest.rules({}))["needs_you"] == []


def test_a_release_request_is_resolved_by_its_push():
    events = [_ev(1, 0, "orchestrator.release_requested", tag="v2.0.0"),
              _ev(2, 1, "orchestrator.release_pushed", tag="v2.0.0")]
    parts = digest.build(events, T0 - 1, digest.rules({}))
    assert parts["needs_you"] == [] and [e["seq"] for e in parts["done"]] == [2]


def test_held_notices_are_listed_under_changed():
    events = [_ev(1, 0, "run_ci_red", task_id="CMX-1", pr_url=PR + "1"),
              _ev(2, 1, "ops_findings", "2 findings")]
    lines = digest.render_lines(digest.build(events, T0 - 1, digest.rules({})), "x")
    assert f"• CMX-1 CI red, sent back {PR}1" in lines
    assert f"• ops_findings ops scan findings {DASH}/" in lines


def test_a_reopened_run_can_be_waiting_again():
    events = [_ev(1, 0, "orchestrator.merge", task_id="CMX-1"),
              _ev(2, 1, "run_reopened", task_id="CMX-1"),
              _ev(3, 2, "run_review", task_id="CMX-1")]
    assert [e["seq"] for e in digest.build(events, T0, digest.rules({}))["waiting"]] == [3]


def test_a_merged_run_is_not_waiting_without_a_reopen():
    events = [_ev(1, 0, "run_review", task_id="CMX-1"),
              _ev(2, 1, "run_closed", task_id="CMX-1"),
              _ev(3, 2, "run_judge_clean", task_id="CMX-1")]
    assert digest.build(events, T0, digest.rules({}))["waiting"] == []


def test_needs_you_shows_at_most_three_newest_first_and_counts_the_rest():
    events = [_ev(i, i, "run_needs_human", task_id=f"CMX-{i}", pr_url=PR + str(i))
              for i in range(1, 6)]
    lines = digest.render_lines(digest.build(events, T0 - 1, digest.rules({})), "x")
    assert lines[1:6] == ["Needs you 5", f"• CMX-5 needs a human {PR}5",
                          f"• CMX-4 needs a human {PR}4", f"• CMX-3 needs a human {PR}3",
                          f"• +2 more {DASH}/"]


def test_the_word_cap_holds_and_every_item_line_has_a_link():
    events = []
    for i in range(1, 80):
        events.append(_ev(i, i, "orchestrator.merge", task_id=f"CMX-{i}", pr_url=PR + str(i)))
        events.append(_ev(1000 + i, i, "run_review", task_id=f"CMX-{1000 + i}"))
        events.append(_ev(2000 + i, i, "run_needs_human", task_id=f"CMX-{2000 + i}"))
        events.append(_ev(3000 + i, i, "task.created", identifier=f"CMX-{3000 + i}"))
    lines = digest.render_lines(digest.build(events, T0 - 1, digest.rules({})), "x")
    assert sum(len(line.split()) for line in lines) <= 150
    items = [line for line in lines if line.startswith("•")]
    assert items and all(line.split()[-1].startswith(("http://", "https://")) for line in items)
    assert sum(line.startswith("• CMX-2") for line in lines) == 3      # needs you kept whole
    assert "Done since last 79" in lines and f"• CMX-79 merged {PR}79" in lines


def test_under_the_cap_nothing_is_trimmed():
    events = [_ev(i, i, "orchestrator.merge", task_id=f"CMX-{i}", pr_url=PR + str(i))
              for i in range(1, 6)]
    lines = digest.render_lines(digest.build(events, T0 - 1, digest.rules({})), "x")
    assert not any("more" in line for line in lines)
    assert sum(line.startswith("• CMX-") for line in lines) == 5


def test_trimming_takes_from_the_longest_part_first():
    parts = {"needs_you": [], "changed": [],
             "waiting": [_ev(i, i, "run_review", task_id=f"W{i}", pr_url=PR) for i in range(3)],
             "done": [_ev(i, i, "orchestrator.merge", task_id=f"D{i}", pr_url=PR)
                      for i in range(60)]}
    lines = digest.render_lines(parts, "x")
    assert sum(line.startswith("• W") for line in lines) == 3
    assert any(line.startswith("• D") for line in lines)


# --- the schedule ---------------------------------------------------------------------

def _at(h, m, day=27):
    return datetime(2026, 10, day, h, m, tzinfo=JLM).timestamp()


@pytest.mark.parametrize("now, slot", [
    (_at(8, 59), _at(16, 0, 26)),
    (_at(9, 0), _at(9, 0)),
    (_at(15, 59), _at(9, 0)),
    (_at(16, 0), _at(16, 0)),
    (_at(23, 30), _at(16, 0)),
])
def test_last_slot_is_the_latest_configured_time_in_the_zone(now, slot):
    assert digest.last_slot(now, digest.rules(dict(ON))) == slot


def test_due_once_per_slot_and_never_without_a_first_record():
    rule = digest.rules(dict(ON))
    assert digest.due(_at(9, 1), rule, _at(8, 0)) is True
    assert digest.due(_at(9, 1), rule, _at(9, 0, 27) + 30) is False
    assert digest.due(_at(9, 1), rule, None) is False
    assert digest.due(_at(9, 1), digest.rules({**ON, "enabled": False}), _at(8, 0)) is False


def test_daemon_tick_starts_the_clock_then_sends_at_the_next_slot(sandbox, monkeypatch):
    _config(sandbox, **ON)
    queued, pushed = [], []
    monkeypatch.setattr(inbox, "enqueue", lambda k, s, p: (queued.append((k, s)), True)[1])
    monkeypatch.setattr(notify, "enabled", lambda: True)
    monkeypatch.setattr(notify, "send", lambda text, title=None: (pushed.append(title), True)[1])

    assert digest.daemon_tick(now=_at(8, 0)) is None             # first run: clock starts
    assert digest.last_sent() == _at(8, 0)
    assert digest.daemon_tick(now=_at(8, 59)) is None
    out = digest.daemon_tick(now=_at(9, 0))
    assert out is not None and out["queued"] is True and out["pushed"] is True
    assert [k for k, _ in queued] == ["digest"] and "\n" not in queued[0][1]
    assert pushed == ["chela digest"]
    assert digest.last_sent() == _at(9, 0)
    assert digest.daemon_tick(now=_at(9, 5)) is None             # not twice per slot
    assert [e["type"] for e in event_log.read(types=["digest"])["events"]] == ["digest"]


def test_a_failed_delivery_still_spends_the_slot(sandbox, monkeypatch):
    _config(sandbox, **ON)
    queued = []
    monkeypatch.setattr(inbox, "enqueue", lambda k, s, p: (queued.append(k), True)[1])
    monkeypatch.setattr(notify, "enabled", lambda: True)

    def down(text, title=None):
        raise OSError("network down")
    monkeypatch.setattr(notify, "send", down)
    digest.mark_sent(_at(8, 0))
    with pytest.raises(OSError):
        digest.daemon_tick(now=_at(9, 0))
    assert digest.last_sent() == _at(9, 0)
    assert digest.daemon_tick(now=_at(9, 1)) is None
    assert queued == ["digest"]                                   # once, not every tick


def test_daemon_tick_does_nothing_with_the_feature_off(sandbox):
    assert digest.daemon_tick(now=_at(9, 0)) is None
    assert digest.last_sent() is None


def test_an_unreadable_state_file_reads_as_never_sent(sandbox):
    (sandbox / "digest.json").write_text("{torn")
    assert digest.last_sent() is None
    (sandbox / "digest.json").write_text('{"last_sent": "yesterday"}')
    assert digest.last_sent() is None


def test_the_digest_kind_is_delivered_with_the_rules_on(sandbox, sends):
    _config(sandbox, **ON)
    store = _store("digest")
    assert [e["kind"] for e in inbox.deliver(store, {ORCH: inbox.IDLE})] == ["digest"]


# --- the CLI -------------------------------------------------------------------------

def test_chela_digest_prints_without_moving_the_clock(sandbox, capsys):
    _config(sandbox, **ON)
    _write_fixture(FIXTURE)
    main.cmd_digest(Namespace(send=False, since_hours=1e6, json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["needs_you"] == 1 and "Needs you 1" in out["text"]
    assert digest.last_sent() is None


def test_chela_digest_send_delivers_and_moves_the_clock(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(inbox, "enqueue", lambda k, s, p: True)
    monkeypatch.setattr(notify, "enabled", lambda: False)
    before = time.time()
    main.cmd_digest(Namespace(send=True, since_hours=None, json=False))
    assert capsys.readouterr().out.startswith("chela digest since ")
    assert digest.last_sent() >= before
