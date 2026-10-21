"""The sidebar layout (CMX-82) — group order, custom groups + membership and the View
settings are ONE server-side store, so two devices agree.

Two Flask test clients stand in for the phone and the laptop: what one writes through
``/api/sidebar/layout`` the other reads back. The one-time migration of a browser's old
localStorage layout writes only while the server holds none (first device wins), and a
write names its fields — it never resets the ones it did not name.

The client half (the page writes there, re-reads it on refresh, keeps collapsed groups
per device, writes nothing before its first load) is in
``tests/sidebar_layout_shared.test.mjs``, which runs two real copies of nav.js.
"""
from __future__ import annotations

import pytest

from chela import sidebar_layout as sl
from chela.dashboard import app as dash


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Never the real ``~/.chela/sidebar-layout.json``."""
    path = tmp_path / "sidebar-layout.json"
    monkeypatch.setattr(sl, "_STORE", path)
    return path


@pytest.fixture
def phone():
    return dash.app.test_client()


@pytest.fixture
def laptop():
    return dash.app.test_client()


def _get(client):
    return client.get("/api/sidebar/layout").get_json()["layout"]


def _post(client, body):
    resp = client.post("/api/sidebar/layout", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


CUSTOM = {"groups": [{"id": "g1", "name": "Infra"}], "assign": {"w:@3": "g1", "run:CMX-8": "g1"}}


def test_nothing_saved_reads_as_null_not_as_defaults(phone):
    # null is the migration's cue — an empty-but-present layout would block every import
    assert _get(phone) is None


def test_a_reorder_on_one_device_is_read_by_the_other(phone, laptop):
    _post(phone, {"order": ["/srv/b", "/srv/a"]})
    assert _get(laptop)["order"] == ["/srv/b", "/srv/a"]


def test_a_custom_group_and_its_membership_travel(phone, laptop):
    _post(laptop, {"custom": CUSTOM})
    assert _get(phone)["custom"] == CUSTOM


def test_a_view_change_travels(phone, laptop):
    _post(phone, {"view": {"groupBy": "custom", "sort": "name", "env": ["interactive"], "showPR": False}})
    assert _get(laptop)["view"] == {"groupBy": "custom", "sort": "name", "env": ["interactive"],
                                    "showPR": False}


def test_a_write_keeps_the_fields_it_did_not_name(phone, laptop):
    _post(phone, {"view": {"groupBy": "state"}})
    _post(laptop, {"order": ["/srv/x"]})
    got = _get(phone)
    assert got["view"] == {"groupBy": "state"}, "the laptop's reorder reset the phone's view"
    assert got["order"] == ["/srv/x"]


def test_the_import_writes_when_the_server_has_no_layout(phone):
    out = _post(phone, {"import": True, "order": ["/srv/a"], "custom": CUSTOM})
    assert out["imported"] is True
    assert _get(phone)["order"] == ["/srv/a"]
    assert _get(phone)["custom"] == CUSTOM


def test_the_import_never_overwrites_an_existing_layout(phone, laptop):
    _post(phone, {"import": True, "order": ["/srv/first"]})
    out = _post(laptop, {"import": True, "order": ["/srv/second"], "view": {"groupBy": "none"}})
    assert out["imported"] is False
    # the second device is handed the FIRST device's layout to adopt
    assert out["layout"]["order"] == ["/srv/first"]
    assert _get(phone)["order"] == ["/srv/first"]
    assert _get(phone)["view"] == {}


def test_the_import_never_overwrites_a_layout_saved_by_an_ordinary_write(phone, laptop):
    _post(phone, {"view": {"groupBy": "date"}})
    assert _post(laptop, {"import": True, "view": {"groupBy": "none"}})["imported"] is False
    assert _get(phone)["view"] == {"groupBy": "date"}


def test_an_assignment_to_a_group_that_does_not_exist_is_dropped(phone):
    _post(phone, {"custom": {"groups": [{"id": "g1", "name": "A"}],
                             "assign": {"w:@1": "g1", "w:@2": "gone"}}})
    assert _get(phone)["custom"]["assign"] == {"w:@1": "g1"}


def test_garbage_is_bounded_not_stored(phone):
    _post(phone, {"order": ["ok", 7, "", "ok"], "view": {"groupBy": {"x": 1}, "sort": "name"},
                  "collapsed": ["/srv/a"]})
    got = _get(phone)
    assert got["order"] == ["ok"]
    assert got["view"] == {"sort": "name"}
    assert "collapsed" not in got, "collapsed groups are per device — never stored here"


def test_a_non_object_body_is_refused(phone):
    assert phone.post("/api/sidebar/layout", json=["x"]).status_code == 400


def test_the_store_is_written_atomically(store, phone):
    _post(phone, {"order": ["/srv/a"]})
    assert store.exists()
    assert not store.with_suffix(".json.tmp").exists()


# CMX-278: the desktop sidebar's width, dragged on one device, follows to the others.
@pytest.mark.parametrize("sent,stored", [
    (300, 300), (300.6, 301), (264, 264), (480, 480),
    (263, 264), (100, 264), (481, 480), (9999, 480),   # clamped
    (True, None), ("300", None), (None, None), (float("nan"), None), (float("inf"), None),
])
def test_the_width_travels_clamped(phone, laptop, sent, stored):
    _post(phone, {"width": sent})
    assert _get(laptop)["width"] == stored


def test_a_width_write_keeps_the_other_fields(phone):
    _post(phone, {"order": ["/srv/a"]})
    _post(phone, {"width": 320})
    assert (_get(phone)["order"], _get(phone)["width"]) == (["/srv/a"], 320)
