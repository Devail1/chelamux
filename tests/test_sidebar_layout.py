"""The sidebar layout (CMX-82): group order, custom groups + membership and View settings are
SERVER state, shared by every device; collapsed state is not.

Driven through the dashboard's Flask routes with two test clients — a phone and a laptop —
against a store in ``tmp_path`` (never ``~/.chela``). The page half (no write-back before
the first load, collapsed staying in the browser) is ``tests/sidebar_layout.test.mjs``.
"""
from __future__ import annotations

import json

import pytest

from chela import sidebar_layout as sl

ORDER = ["/srv/code/beta", "/srv/code/alpha"]
VIEW = {"groupBy": "custom", "sort": "name", "env": ["interactive"], "showPR": False}
CUSTOM = {"groups": [{"id": "g1", "name": "Infra"}], "assign": {"w:@5": "g1", "run:CMX-3": "g1"}}


@pytest.fixture
def dash(monkeypatch, tmp_path):
    from chela.dashboard import app as dash

    monkeypatch.setattr(sl, "_STORE", tmp_path / "sidebar-layout.json")
    return dash


@pytest.fixture
def devices(dash):
    return dash.app.test_client(), dash.app.test_client()


def test_no_layout_yet_reads_as_not_existing(devices):
    phone, _ = devices
    st = phone.get("/api/sidebar/layout").get_json()
    assert st["exists"] is False
    assert st["layout"] == {"order": [], "view": {}, "custom": {"groups": [], "assign": {}}}


def test_a_change_on_one_device_is_read_by_the_other(devices):
    phone, laptop = devices
    assert phone.post("/api/sidebar/layout", json={"order": ORDER}).get_json()["ok"]
    assert phone.post("/api/sidebar/layout", json={"custom": CUSTOM}).get_json()["ok"]
    assert phone.post("/api/sidebar/layout", json={"view": VIEW}).get_json()["ok"]
    st = laptop.get("/api/sidebar/layout").get_json()
    assert st["exists"] is True
    assert st["layout"] == {"order": ORDER, "view": VIEW, "custom": CUSTOM}


def test_a_partial_save_keeps_the_fields_it_does_not_name(devices):
    """The laptop changes the View; the phone's reorder and custom groups survive it."""
    phone, laptop = devices
    phone.post("/api/sidebar/layout", json={"order": ORDER, "custom": CUSTOM})
    laptop.post("/api/sidebar/layout", json={"view": {"groupBy": "none"}})
    st = phone.get("/api/sidebar/layout").get_json()["layout"]
    assert st == {"order": ORDER, "view": {"groupBy": "none"}, "custom": CUSTOM}


@pytest.mark.parametrize("field", ["collapsed", "sidebarCollapsed", "archivedOpen"])
def test_collapsed_state_is_refused_never_shared(devices, field, tmp_path):
    """Collapsed groups / the folded sidebar / the Archived section are per device: a client
    that sends one is wrong, and the store keeps none of it."""
    phone, laptop = devices
    phone.post("/api/sidebar/layout", json={"order": ORDER})
    r = phone.post("/api/sidebar/layout", json={"order": ["/x"], field: ["/srv/code/alpha"]})
    assert r.status_code == 400
    st = laptop.get("/api/sidebar/layout").get_json()
    assert field not in st["layout"]
    assert st["layout"]["order"] == ORDER, "a refused save half-applied"
    assert field not in json.loads((tmp_path / "sidebar-layout.json").read_text())


def test_migration_imports_once_and_never_overwrites(devices):
    phone, laptop = devices
    first = phone.post("/api/sidebar/layout/migrate", json={"order": ORDER, "view": VIEW}).get_json()
    assert first["ok"] and first["imported"] is True
    assert first["layout"]["order"] == ORDER
    # a later device's old layout is NOT imported — it adopts the server's
    later = laptop.post("/api/sidebar/layout/migrate",
                        json={"order": ["/other"], "view": {"groupBy": "none"}}).get_json()
    assert later["ok"] and later["imported"] is False
    assert later["layout"]["order"] == ORDER and later["layout"]["view"] == VIEW
    assert laptop.get("/api/sidebar/layout").get_json()["layout"]["order"] == ORDER


def test_migration_never_overwrites_a_layout_saved_normally(devices):
    phone, laptop = devices
    phone.post("/api/sidebar/layout", json={"view": {"groupBy": "state"}})
    out = laptop.post("/api/sidebar/layout/migrate", json={"view": VIEW, "order": ORDER}).get_json()
    assert out["imported"] is False
    st = phone.get("/api/sidebar/layout").get_json()["layout"]
    assert st["view"] == {"groupBy": "state"} and st["order"] == []


@pytest.mark.parametrize("body", [
    None, {}, [], {"order": "nope"}, {"order": [1, 2]}, {"view": []},
    {"view": {"groupBy": {"nested": 1}}}, {"custom": {"groups": [{"id": "g1"}]}},
    {"custom": {"groups": [], "assign": {"w:@1": 5}}},
])
def test_a_malformed_layout_is_refused(devices, body):
    phone, _ = devices
    assert phone.post("/api/sidebar/layout", json=body).status_code == 400
    assert phone.post("/api/sidebar/layout/migrate", json=body).status_code == 400
    assert phone.get("/api/sidebar/layout").get_json()["exists"] is False


def test_an_assignment_to_a_group_that_does_not_exist_is_dropped(devices):
    phone, _ = devices
    phone.post("/api/sidebar/layout", json={"custom": {"groups": [{"id": "g1", "name": "A"}],
                                                       "assign": {"w:@1": "g1", "w:@2": "gone"}}})
    assert phone.get("/api/sidebar/layout").get_json()["layout"]["custom"]["assign"] == {"w:@1": "g1"}


def test_a_corrupt_field_on_disk_does_not_lose_the_others(dash, tmp_path):
    (tmp_path / "sidebar-layout.json").write_text(json.dumps({"order": ORDER, "view": "garbage"}))
    st = sl.state()
    assert st["exists"] is True
    assert st["layout"]["order"] == ORDER and st["layout"]["view"] == {}
