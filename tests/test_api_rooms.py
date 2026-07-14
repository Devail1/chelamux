"""``/api/rooms`` — the HTTP half of the Wall's wire gesture.

The wire is a UI for ``chela room join`` and nothing more, so the contracts here are
all about **not becoming a second implementation of rooms**:

* ``GET /api/rooms`` is ``rooms.status()`` — the *same* dict ``chela room status``
  prints. One authority; the wall and the CLI cannot disagree about who is in what.
* ``POST /api/rooms/join`` writes through ``rooms.join`` — the same call the CLI
  makes — and is **idempotent**: dragging the same wire twice does not duplicate a
  membership or fork a second room.
* Wiring onto a tile that is **already in a room** joins THAT room. A third agent
  joins the conversation; it does not get a second room beside it.
* A window that died mid-drag fails the join **whole** (nothing written), rather
  than leaving a room of one behind.
* **The auto-name identifies the PAIR, not its labels.** Window *names* collide (two
  shells; two repos with the same basename — ``discovery.get_windows_by_id`` says so
  in its own docstring); *ids* never do. A name-only slug would regenerate the same
  room id for a LATER, unrelated pair, and since ``rooms.create`` is idempotent that
  pair would silently JOIN THE FIRST PAIR'S ROOM — and a room *dispatches*, so one
  pair's conversation would be pasted into the other pair's terminals.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela import discovery, messenger, rooms
from chela.dashboard import app as dash

LIVE = {"@1": "researcher", "@2": "executor", "@3": "risk-manager"}
# The fleet that breaks a name-derived room id: four windows, one name (`chela shell`
# names every plain shell the same until its cwd resolves).
SAME_NAME = {"@1": "shell", "@2": "shell", "@3": "shell", "@4": "shell"}


@pytest.fixture
def client():
    return dash.app.test_client()


@pytest.fixture(autouse=True)
def fleet():
    """A live tmux fleet, with tmux itself stubbed."""
    with _fleet_of(LIVE):
        yield


@contextmanager
def _fleet_of(windows: dict[str, str]):
    with patch.object(messenger, "get_windows_by_id", return_value=dict(windows)), \
            patch.object(rooms.discovery, "get_windows_by_id", return_value=dict(windows)), \
            patch.object(discovery, "get_windows_by_id", return_value=dict(windows)):
        yield


def _join(client, wids, room=None):
    body = {"wids": wids}
    if room:
        body["room"] = room
    return client.post("/api/rooms/join", json=body)


def _members(room):
    return sorted(rooms.members(room))


# --- the write ------------------------------------------------------------------

def test_a_wire_between_two_tiles_creates_the_room_and_both_memberships(client):
    res = _join(client, ["@1", "@2"])
    assert res.status_code == 200
    room = res.get_json()["room"]
    assert res.get_json()["ok"] is True
    assert _members(room) == ["@1", "@2"]


def test_the_room_is_auto_named_from_the_agents_a_drop_never_opens_a_modal(client):
    # A name prompt on drop would make the gesture two acts. It is derived instead.
    room = _join(client, ["@1", "@2"]).get_json()["room"]
    assert room.startswith("wire-researcher-executor-")   # + the id salt, see below
    assert rooms._ROOM_RE.match(room), "auto-name must be a legal room id"
    assert len(room) <= 40, "and must fit rooms._ROOM_RE's 40 chars"


def test_two_pairs_of_SAME_NAMED_windows_get_TWO_ROOMS__names_collide_ids_never_do(client):
    """🔴 The one that routes a private conversation into the wrong terminals.

    Four windows all called ``shell``. Wire @1→@2, then @3→@4. A room id derived from
    the DISPLAY NAMES is ``wire-shell-shell`` both times; ``rooms.create`` is
    idempotent, so the second pair does not get a room — it JOINS THE FIRST PAIR'S,
    and a room does active dispatch (a handoff/question is pasted into every member's
    TUI). @3 and @4's traffic would land in @1 and @2's terminals.
    """
    with _fleet_of(SAME_NAME):
        first = _join(client, ["@1", "@2"]).get_json()["room"]
        second = _join(client, ["@3", "@4"]).get_json()["room"]

    assert first != second, "two unrelated pairs were wired into ONE room"
    assert _members(first) == ["@1", "@2"], "and the second pair leaked into the first"
    assert _members(second) == ["@3", "@4"]
    assert len(rooms.rooms()) == 2


def test_the_auto_name_is_still_STABLE_for_the_same_pair__re_wiring_is_idempotent(client):
    # The salt is the sorted WIDS, so the same pair always names the same room (in
    # either drag direction) — the collision fix must not cost idempotency.
    with _fleet_of(SAME_NAME):
        a = _join(client, ["@1", "@2"]).get_json()["room"]
        b = _join(client, ["@2", "@1"]).get_json()["room"]   # dragged the other way
    assert a == b
    assert list(rooms.rooms()) == [a]


def test_dragging_the_same_wire_twice_is_IDEMPOTENT(client):
    first = _join(client, ["@1", "@2"]).get_json()["room"]
    second = _join(client, ["@1", "@2"]).get_json()["room"]
    assert first == second                      # no second room beside the first
    assert _members(first) == ["@1", "@2"]      # and no duplicated membership
    assert list(rooms.rooms()) == [first]


def test_wiring_onto_a_tile_ALREADY_in_a_room_joins_THAT_room(client):
    room = _join(client, ["@1", "@2"]).get_json()["room"]
    res = _join(client, ["@3", "@2"])           # drag @3's wire onto the roomed @2
    assert res.get_json()["room"] == room       # it joined; it did not fork
    assert _members(room) == ["@1", "@2", "@3"]
    assert list(rooms.rooms()) == [room]


def test_an_explicit_room_name_is_honoured(client):
    assert _join(client, ["@1", "@2"], room="ops").get_json()["room"] == "ops"
    assert _members("ops") == ["@1", "@2"]


def test_a_drop_on_the_SAME_tile_creates_NOTHING(client):
    res = _join(client, ["@1", "@1"])           # deduped to one wid → not a room
    assert res.status_code == 400
    assert rooms.rooms() == {}


def test_a_drop_with_no_target_creates_NOTHING(client):
    assert _join(client, ["@1"]).status_code == 400
    assert client.post("/api/rooms/join", json={}).status_code == 400
    assert rooms.rooms() == {}


def test_a_window_that_died_mid_drag_fails_the_join_WHOLE(client):
    res = _join(client, ["@1", "@404"])
    assert res.status_code == 404
    assert "not a live window" in res.get_json()["error"]
    assert rooms.rooms() == {}                  # no room of one left behind


def test_a_window_that_dies_BETWEEN_the_check_and_the_WRITE_still_writes_NOTHING(client):
    """🔴 The docstring's promise, taken literally: nothing is written, or all of it is.

    Pre-checking every wid and then writing them one at a time is not atomic — the
    window can die in the gap, and every membership written before it stays written.
    The route answers 4xx while the store holds a ROOM OF ONE: exactly what the
    gesture's whole contract says is impossible.

    Here @2 dies after the route has resolved both windows but before its own join.
    """
    real = messenger.resolve_window
    calls = []

    def dying(wid):
        calls.append(wid)
        # Both resolve during the pre-check; @2 is gone by the time it is written.
        if wid == "@2" and calls.count("@2") > 1:
            return None
        return real(wid)

    with patch.object(messenger, "resolve_window", side_effect=dying):
        res = _join(client, ["@1", "@2"])

    assert res.status_code >= 400, "a dead member must fail the join"
    assert rooms.members(res.get_json().get("room") or "") == {}
    assert not any(rooms.rooms().values()), f"a room of one survived: {rooms.rooms()}"


# --- the input: a wid list is untrusted, and each wid costs tmux spawns ----------

def test_a_wids_list_that_is_not_a_LIST_is_refused__a_string_would_iterate_CHARACTERS(client):
    res = client.post("/api/rooms/join", json={"wids": "@1@2"})
    assert res.status_code == 400
    assert rooms.rooms() == {}


def test_an_absurdly_long_wids_list_is_refused_before_it_reaches_tmux(client):
    # Every wid is resolved against the live window table (tmux subprocesses); an
    # unbounded list is an unbounded number of them from one request.
    res = client.post("/api/rooms/join", json={"wids": [f"@{i}" for i in range(500)]})
    assert res.status_code == 400
    assert "at most" in res.get_json()["error"]
    assert rooms.rooms() == {}


# --- the read -------------------------------------------------------------------

def test_GET_rooms_reflects_a_CLI_created_room__one_authority(client):
    rooms.join("ops", "@1")                     # exactly what `chela room join` does
    rooms.join("ops", "@2")
    payload = client.get("/api/rooms").get_json()
    assert payload == rooms.status()            # the API IS the CLI's view, verbatim
    assert sorted(payload["rooms"]["ops"]["members"]) == ["@1", "@2"]
    # keyed by WID — which is what the Wall has (gs-id on the tile)
    assert payload["rooms"]["ops"]["members"]["@1"]["name"] == "researcher"


def test_GET_rooms_is_empty_when_nothing_is_wired(client):
    assert client.get("/api/rooms").get_json()["rooms"] == {}


# --- leave (the click on a tile's room badge) -----------------------------------

def test_leaving_drops_one_member_and_leaves_the_rest(client):
    room = _join(client, ["@1", "@2"]).get_json()["room"]
    res = client.post("/api/rooms/leave", json={"wid": "@1", "room": room})
    assert res.get_json()["ok"] is True
    assert _members(room) == ["@2"]


def test_leave_requires_both_a_wid_and_a_room(client):
    assert client.post("/api/rooms/leave", json={"wid": "@1"}).status_code == 400
