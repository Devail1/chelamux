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
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from chela import discovery, messenger, rooms
from chela.dashboard import app as dash

LIVE = {"@1": "researcher", "@2": "executor", "@3": "risk-manager"}


@pytest.fixture
def client():
    return dash.app.test_client()


@pytest.fixture(autouse=True)
def fleet():
    """A live tmux fleet, with tmux itself stubbed."""
    with patch.object(messenger, "get_windows_by_id", return_value=dict(LIVE)), \
            patch.object(rooms.discovery, "get_windows_by_id", return_value=dict(LIVE)), \
            patch.object(discovery, "get_windows_by_id", return_value=dict(LIVE)):
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
    assert room == "wire-researcher-executor"
    assert rooms._ROOM_RE.match(room), "auto-name must be a legal room id"


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
