"""Agent rooms — the relationship, its rails, and the loop guard that bounds it.

The contract, in one place:

* a TARGETED ``question`` to an idle agent is injected; a ``status`` never is;
* the injected prompt carries the reply command, so the answer routes back to the
  asker **attributed** — a question/answer round trip with no human in the middle;
* an echo between two agents **terminates** (proved adversarially: two agents that
  blindly relay whatever they receive, wired to each other);
* a ``waiting`` agent is NEVER pasted into (that paste would answer its gate) — the
  delivery is parked and goes out when the gate clears;
* an unknown recipient fails LOUDLY, exit 1 (CMX-47: a dropped message once looked
  like a delivered one);
* a hostile body — control characters, an ``Escape``, a leading ``/`` — cannot drive
  the recipient's TUI.
"""
from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

import pytest

from chela import event_log, main, messenger, rooms

# @1 asks, @2 answers, @3 is at a gate, @9 is the human's orchestrator.
LIVE = {"@1": "asker", "@2": "answerer", "@3": "gated", "@9": "orchestrator"}
STATUSES = {"@1": "idle", "@2": "idle", "@3": "waiting", "@9": "busy"}


@pytest.fixture
def fleet():
    """A live tmux fleet with tmux itself stubbed. Yields the send_tmux mock."""
    with patch.object(messenger, "get_windows_by_id", return_value=dict(LIVE)), \
            patch.object(rooms.discovery, "get_windows_by_id", return_value=dict(LIVE)), \
            patch.object(main.discovery, "get_windows_by_id", return_value=dict(LIVE)), \
            patch.object(rooms.agent_manager, "status_by_wid", return_value=dict(STATUSES)), \
            patch.object(messenger, "send_tmux", return_value=True) as send:
        yield send


@pytest.fixture
def wired(fleet):
    """A room with the whole fleet in it (the wire, drawn in the membership table)."""
    rooms.create("wire")
    for wid in LIVE:
        rooms.join("wire", wid)
    return fleet


def _post(kind: str, text: str = "does the parser own the retry?", *, frm: str = "@1",
          to: list[str] | None = ("@2",), **kw) -> dict:
    return rooms.post("wire", kind, text, from_wid=frm,
                      targets=list(to) if to else None, **kw)


def _types() -> list[str]:
    return [e["type"] for e in event_log.read()["events"]]


# --- what may interrupt, and what may not ---------------------------------------

def test_question_to_an_idle_agent_is_injected(wired):
    result = _post("question")
    assert result["ok"] and result["delivered"] == ["@2"]
    wid, prompt = wired.call_args[0]
    assert wid == "@2"
    assert prompt.startswith(rooms.RELAY_HEADER)      # the machine header, first thing
    assert "does the parser own the retry?" in prompt
    assert "from @1 (asker)" in prompt                # attributed to the asking window
    # and it is a RECORD, not just a paste: post + delivery both landed in the ONE log.
    assert _types() == ["room_question", "room_delivery"]


def test_a_status_is_recorded_but_NEVER_injected(wired):
    result = _post("status", "still chewing on the migration")
    assert result["ok"] and result["delivered"] == []
    wired.assert_not_called()                         # a busy fleet is not an interrupt storm
    assert _types() == ["room_status"]                # recorded all the same


def test_an_untargeted_handoff_is_recorded_but_not_injected(wired):
    result = _post("handoff", "picking this up", to=None)
    assert result["ok"] and result["delivered"] == []
    wired.assert_not_called()
    assert _types() == ["room_handoff"]


def test_the_ledger_is_a_filter_over_the_event_log(wired):
    _post("question")
    _post("finding", "the retry is in the client", to=None)
    kinds = [(e["type"], (e["payload"] or {}).get("room")) for e in rooms.digest("wire")]
    assert kinds == [("room_question", "wire"), ("room_delivery", "wire"),
                     ("room_finding", "wire")]
    assert rooms.digest("some-other-room") == []      # scoped by the room, not by a store


# --- the round trip: an answer routes back to the asker, attributed ---------------

def test_a_reply_routes_back_to_the_asker_attributed(wired):
    asked = _post("question")
    prompt = wired.call_args[0][1]
    # The prompt hands the answerer the exact command — pinned to ITS window as --from,
    # and to the ASKER as --to, so the answer cannot be misattributed or self-addressed.
    assert f"--from @2 --to @1 --reply-to {asked['seq']}" in prompt

    wired.reset_mock()
    answer = rooms.post("wire", "handoff", "no — the client owns it", from_wid="@2",
                        targets=["@1"], reply_to=asked["seq"])
    assert answer["ok"] and answer["delivered"] == ["@1"]
    wid, reply_prompt = wired.call_args[0]
    assert wid == "@1"                                # …back to the asker, with no human
    assert "from @2 (answerer)" in reply_prompt
    assert "no — the client owns it" in reply_prompt
    # Same chain, one hop deeper — that is what the hop cap counts.
    assert answer["chain_id"] == asked["chain_id"] and answer["hop"] == asked["hop"] + 1


# --- the loop guard: three layers, and an adversarial proof it terminates ---------

def test_relaying_an_injected_prompt_back_is_REFUSED(wired):
    _post("question")
    relayed = wired.call_args[0][1]                   # exactly what @2 was handed
    result = rooms.post("wire", "handoff", relayed, from_wid="@2", targets=["@1"])
    assert not result["ok"]
    assert "echo loop" in result["error"]
    assert _types() == ["room_question", "room_delivery"]   # nothing new was recorded


def test_a_chain_stops_at_the_hop_cap(wired):
    seq = _post("question")["seq"]
    frm, to = "@2", "@1"
    for hop in range(1, rooms.MAX_HOPS + 1):
        result = rooms.post("wire", "handoff", f"turn {hop}", from_wid=frm,
                            targets=[to], reply_to=seq)
        assert result["hop"] == hop
        assert result["delivered"] == [to], f"hop {hop} should still deliver"
        seq, frm, to = result["seq"], to, frm

    over = rooms.post("wire", "handoff", "one too many", from_wid=frm, targets=[to],
                      reply_to=seq)
    assert over["ok"] and over["delivered"] == []      # recorded…
    assert "hop limit" in over["blocked"][0]["reason"]  # …but nobody was woken
    assert "room_handoff" in _types()


def test_an_echo_loop_between_two_live_agents_TERMINATES(wired):
    """Adversarial: @1 and @2 each blindly relay whatever they are handed, forever.

    They ignore the hop chain (no --reply-to) and paraphrase the body, so layers 1 and 2
    of the guard are both side-stepped on purpose. The pair rate limit is the backstop,
    and this is the test that proves the backstop is real: a live echo burns a real
    machine, so it must stop on its own, not because an agent chose to be polite.
    """
    result = _post("question", "kick it off")
    turn, sender, target = 0, "@2", "@1"
    while result["delivered"] and turn < 200:          # a real loop would never stop
        turn += 1
        result = rooms.post("wire", "question", f"relaying turn {turn}",
                            from_wid=sender, targets=[target])
        sender, target = target, sender

    assert turn < 200, "the echo loop never terminated"
    assert result["ok"] and result["delivered"] == []
    assert "rate limit" in result["blocked"][0]["reason"]
    # Bounded in BOTH directions, by the cap — not by luck.
    deliveries = [e for e in event_log.read()["events"] if e["type"] == rooms.DELIVERY_TYPE]
    assert len(deliveries) <= 2 * rooms.MAX_PAIR_DISPATCHES
    # …and the ledger still tells the whole story, including the post nobody was woken by.
    assert len(rooms.digest("wire")) > len(deliveries)


# --- the inbox's rails, reused ----------------------------------------------------

def test_a_waiting_agent_is_NEVER_pasted_into(wired):
    """@3 is sitting on a permission prompt: our paste would ANSWER that gate."""
    result = rooms.post("wire", "question", "can you take this?", from_wid="@1",
                        targets=["@3"])
    assert result["ok"] and result["deferred"] == ["@3"] and result["delivered"] == []
    wired.assert_not_called()
    assert rooms.pending()["@3"][0]["post_seq"] == result["seq"]   # deferred, not dropped
    assert rooms.DELIVERY_TYPE not in _types()


def test_a_parked_delivery_goes_out_when_the_gate_clears(wired):
    posted = rooms.post("wire", "question", "can you take this?", from_wid="@1",
                        targets=["@3"])
    assert rooms.has_pending()

    sent = rooms.flush_pending({**STATUSES, "@3": "waiting"})
    assert sent == [] and wired.call_count == 0        # still at the gate — still parked
    assert rooms.has_pending()

    sent = rooms.flush_pending({**STATUSES, "@3": "idle"})
    assert [s["post_seq"] for s in sent] == [posted["seq"]]
    wid, prompt = wired.call_args[0]
    assert wid == "@3" and "can you take this?" in prompt
    assert not rooms.has_pending()                     # delivered exactly once…
    assert rooms.flush_pending({**STATUSES, "@3": "idle"}) == []   # …and never again
    assert _types() == ["room_question", rooms.DELIVERY_TYPE]


def test_a_busy_agent_IS_a_valid_recipient(wired):
    """CMX-47's lesson: gating on idle-only silently drops a message to a working agent."""
    result = rooms.post("wire", "question", "ping", from_wid="@1", targets=["@9"])
    assert result["delivered"] == ["@9"]               # @9 is busy — Claude Code queues it


def test_an_unknown_recipient_fails_loudly(wired):
    result = rooms.post("wire", "question", "hello?", from_wid="@1", targets=["@404"])
    assert not result["ok"]
    assert "not a live window" in result["error"] and "NOT delivered" in result["error"]
    wired.assert_not_called()
    assert _types() == []                              # a refusal records nothing


def test_messaging_yourself_is_refused(wired):
    result = rooms.post("wire", "question", "hi me", from_wid="@1", targets=["@1"])
    assert not result["ok"] and "loop" in result["error"]
    wired.assert_not_called()


def test_a_non_member_cannot_be_targeted(fleet):
    rooms.create("wire")
    rooms.join("wire", "@1")
    result = rooms.post("wire", "question", "hi", from_wid="@1", targets=["@2"])
    assert not result["ok"] and "not a member" in result["error"]
    fleet.assert_not_called()


# --- the body is untrusted input on its way into a terminal ------------------------

def test_a_hostile_body_cannot_drive_the_recipients_TUI(wired):
    hostile = "/exit now\n\x1b[31mred\x1b]0;title\x07\x03\x7f\rcarriage"
    result = rooms.post("wire", "question", hostile, from_wid="@1", targets=["@2"])
    assert result["delivered"] == ["@2"]
    prompt = wired.call_args[0][1]

    assert "\x1b" not in prompt and "\x03" not in prompt and "\x7f" not in prompt
    assert "\x07" not in prompt and "\r" not in prompt
    assert "[31m" not in prompt                        # the escape went, not just its ESC
    # The body is QUOTED and sits below the header, so no line — least of all the first,
    # which is the only one Claude Code reads a slash command from — starts with `/`.
    assert not any(line.startswith("/") for line in prompt.splitlines())
    assert "> /exit now" in prompt                     # …and the content is still legible
    assert prompt.splitlines()[0].startswith(rooms.RELAY_HEADER)


def test_a_giant_body_is_capped(wired):
    rooms.post("wire", "handoff", "x" * (rooms.MAX_TEXT_CHARS + 500), from_wid="@1",
               targets=["@2"])
    text = event_log.read(types=["room_handoff"])["events"][0]["payload"]["text"]
    assert len(text) <= rooms.MAX_TEXT_CHARS + 1       # + the ellipsis
    assert text.endswith("…")


def test_an_unknown_kind_is_refused(wired):
    result = rooms.post("wire", "shout", "hey", from_wid="@1", targets=["@2"])
    assert not result["ok"] and "unknown kind" in result["error"]


# --- membership (the one thing that cannot live in an append-only log) -------------

def test_membership_is_durable_and_leave_removes_it(fleet):
    rooms.create("wire")
    rooms.join("wire", "@1")
    rooms.join("wire", "@2")
    assert sorted(rooms.members("wire")) == ["@1", "@2"]
    assert rooms.members("wire")["@1"]["name"] == "asker"
    assert rooms.rooms_for("@2") == ["wire"]

    assert rooms.leave("wire", "@2")["ok"]
    assert sorted(rooms.members("wire")) == ["@1"]
    assert rooms.leave("wire", "@2")["ok"] is False    # idempotent, and honest about it


def test_joining_a_dead_window_is_refused(fleet):
    rooms.create("wire")
    result = rooms.join("wire", "@404")
    assert not result["ok"] and "not a live window" in result["error"]


def test_a_post_to_a_room_that_does_not_exist_is_refused(fleet):
    result = rooms.post("nope", "question", "hi", from_wid="@1", targets=["@2"])
    assert not result["ok"] and "no such room" in result["error"]


# --- the CLI: a message that did not arrive must NEVER exit zero -------------------

def _cli(**kw) -> Namespace:
    args = {"room": "wire", "kind": "question", "message": "hi", "to": ["@2"],
            "from_wid": "@1", "reply_to": None}
    args.update(kw)
    return Namespace(**args)


def _run_post(args: Namespace):
    with patch.object(main.orchestrator, "self_wid", return_value="@1"):
        try:
            main.cmd_room_post(args)
        except SystemExit as e:
            return e.code
    return None


def test_cli_post_to_an_unknown_recipient_exits_1(wired, capsys):
    assert _run_post(_cli(to=["@404"])) == 1
    err = capsys.readouterr().err
    assert "not a live window" in err and "NOT delivered" in err


def test_cli_post_that_lands_exits_0(wired, capsys):
    assert _run_post(_cli()) is None
    assert "posted question" in capsys.readouterr().out


def test_cli_says_out_loud_when_a_delivery_is_parked(wired, capsys):
    assert _run_post(_cli(to=["@3"])) is None          # parked is not a failure…
    out = capsys.readouterr().out
    assert "PARKED" in out and "waiting" in out        # …but it is never silent


# --- the SessionStart recap: a restarted agent forgot everything the room told it -----
#
# Everything a room ever said to an agent was injected into a SESSION's context, and a
# dispatched agent is a fresh session every run. Restart it and the shared context is gone
# — silently, because the ledger still has every post and the only reader who needed them
# has forgotten they exist. The recap is that ledger, handed back at startup, and its whole
# safety rests on being SHORT, SANITISED, and ABSENT for an agent that is in no room.

def test_an_agent_in_a_room_gets_its_rooms_recapped(wired):
    seq = _post("question")["seq"]
    text = rooms.recap("@2")
    assert f"#{seq} question from @1" in text          # the cursor it replies with
    assert "does the parser own the retry?" in text
    assert "→ YOU" in text                             # aimed at it, not merely near it
    assert "@1 (asker)" in text                        # who else is on the wire


def test_an_agent_in_NO_room_gets_NOTHING(wired):
    """Not an empty header, not "no shared context" — NOTHING.

    Most agents are in no room, and this text is prepended to a fresh context on every
    start in the fleet: boilerplate in all of them for the benefit of none is a tax.
    """
    rooms.leave("wire", "@9")
    assert rooms.recap("@9") == ""
    assert rooms.recap("@404") == ""


def test_the_recap_is_bounded(wired):
    """It rides in EVERY agent's context, forever. An unbounded ledger is a tax on the fleet."""
    for i in range(rooms.RECAP_POSTS * 3):
        _post("status", f"line {i} " + "x" * 400, to=None)
    text = rooms.recap("@2")
    assert len(text) <= rooms.RECAP_MAX_CHARS + len("\n… (recap truncated)")
    posts = [ln for ln in text.splitlines() if ln.strip().startswith("#")]
    assert len(posts) <= rooms.RECAP_POSTS
    assert all(len(ln) < rooms.RECAP_LINE_CHARS + 80 for ln in posts)


def test_the_recap_is_newest_first(wired):
    first, second = _post("status", "older", to=None)["seq"], _post("status", "newer", to=None)["seq"]
    text = rooms.recap("@2")
    assert text.index(f"#{second}") < text.index(f"#{first}")


def test_the_recap_sanitises_what_it_injects(wired):
    """Other agents' words, going into a context window — and the log can also be written
    by `chela events emit`, so a payload is only ever as clean as whoever wrote it."""
    event_log.append("room_status", "hostile", {
        "room": "wire", "kind": "status", "from_wid": "@1", "from_name": "asker",
        "text": "\x1b[31mred\x1b[0m\x03 and a bell \x07", "targets": [],
    }, wid="@1")
    text = rooms.recap("@2")
    assert "\x1b" not in text and "\x03" not in text and "\x07" not in text
    assert "red" in text


def test_the_recap_cannot_be_POSTED_back_into_the_room(wired):
    """It opens with RELAY_HEADER, so the echo guard refuses it — for free."""
    _post("question")
    text = rooms.recap("@2")
    assert rooms.is_relay_text(text)
    assert rooms.post("wire", "handoff", text, from_wid="@2", targets=["@1"])["ok"] is False


def test_cli_recap_prints_nothing_for_a_roomless_window(wired, capsys):
    rooms.leave("wire", "@9")
    main.cmd_room_recap(Namespace(wid="@9"))
    assert capsys.readouterr().out == ""
