"""CMX-183, Mod 3/4: ``GET /api/needs-you``, the read the orchestrator's "needs you" band polls.

``mods/chela-hooks/hooks/needsyou-cases.ts`` is ONE fixture read by both suites: these prove
the REAL route answers ``response`` for ``sources``; the mod's ``claude plugin test`` cases
feed that same ``response`` to the band. A source that cannot be read is a 503, which the
band shows as nothing, never as "nothing waiting".
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from chela import config, dispatcher, overrides, share_requests

pytest.importorskip("flask")

MOD = Path(__file__).resolve().parent.parent / "mods" / "chela-hooks"
CASES = json.loads(
    (MOD / "hooks" / "needsyou-cases.ts").read_text()
    .split("export const NEEDS_YOU =", 1)[1].rsplit("as const", 1)[0])
SRC = CASES["sources"]


def _store(tmp_path, records) -> None:
    """The share-requests store on disk, as chela.share_requests writes it."""
    (tmp_path / "share-requests.json").write_text(json.dumps(
        {"requests": {r["id"]: r for r in records}, "sessions": {}, "ingested": {}}))


@pytest.fixture
def world(monkeypatch, tmp_path):
    """The three stores the route reads, holding ``sources`` (the share-requests one is
    the REAL store file); ``calls`` records the share-request hygiene the listing route
    also runs."""
    w = {"calls": [], "overrides": (list(SRC["overrides"]), None), "runs": list(SRC["runs"]),
         "share": lambda records: _store(tmp_path, records)}
    w["share"](SRC["share_requests"])
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv(config.DASHBOARD_URL_ENV, CASES["base"])
    monkeypatch.setattr(overrides, "listing_state", lambda: w["overrides"])
    monkeypatch.setattr(share_requests, "ingest_all", lambda: w["calls"].append("ingest_all"))
    monkeypatch.setattr(share_requests, "sweep", lambda: w["calls"].append("sweep"))
    monkeypatch.setattr(dispatcher, "list_runs", lambda: w["runs"])
    return w


@pytest.fixture
def client():
    from chela.dashboard.app import app
    return app.test_client()


def _get(client):
    resp = client.get("/api/needs-you")
    return resp.status_code, resp.get_json()


def test_the_route_answers_the_shared_response(world, client):
    """🔴 GUARD: the exact items, oldest first, with the dashboard URL of each."""
    assert _get(client) == (200, CASES["response"])
    assert world["calls"] == ["ingest_all", "sweep"]


def test_the_fixture_is_the_documented_literal():
    """Kinds, order and URLs spelled out, not read back from the code under test."""
    items = CASES["response"]["items"]
    assert [i["kind"] for i in items] == ["access", "approval", "run"]
    assert items[1]["url"] == "https://chela.example/override/ov-1"
    assert items[2]["since"] == 1792413180       # 2026-10-19T12:33:00Z


def test_nothing_waiting_is_an_empty_list(world, client):
    world["overrides"], world["runs"] = ([], None), [SRC["runs"][1]]
    world["share"]([SRC["share_requests"][1]])
    assert _get(client) == (200, {"items": []})


@pytest.mark.parametrize("status", ["approved", "denied", "expired", "revoked", None])
def test_only_a_pending_access_request_is_listed(world, client, status):
    world["overrides"], world["runs"] = ([], None), []
    world["share"]([{"id": "r-9", "status": status, "kind": "mount", "target": "/x", "filed_at": 5}])
    assert _get(client) == (200, {"items": []})


@pytest.mark.parametrize("status", ["awaiting_review", "changes_requested", "running", "done"])
def test_only_a_needs_human_run_is_listed(world, client, status):
    world["overrides"] = ([], None)
    world["share"]([])
    world["runs"] = [{"task_id": "CMX-1", "status": status, "ended_at": "2026-10-19T12:00:00+00:00"}]
    assert _get(client) == (200, {"items": []})


def test_a_run_with_no_end_time_is_dated_by_its_start(world, client):
    world["overrides"] = ([], None)
    world["share"]([])
    world["runs"] = [{"task_id": "CMX-1", "status": "needs_human", "ended_at": None,
                      "started_at": "2026-10-19T11:00:00+00:00"}]
    _, body = _get(client)
    assert body["items"][0]["since"] == 1792407600


@pytest.mark.parametrize("bad", [None, "", "not a time"])
def test_an_undated_run_waits_from_now_never_from_the_epoch(world, client, bad):
    """An epoch ``since`` would rank the run oldest and print an age of decades."""
    world["runs"] = [{"task_id": "CMX-1", "status": "needs_human", "ended_at": bad, "started_at": bad}]
    before = time.time()
    _, body = _get(client)
    run = [i for i in body["items"] if i["kind"] == "run"][0]
    assert before <= run["since"] <= time.time()
    assert body["items"][-1] == run


def test_labels_fall_back_to_ids(world, client):
    world["overrides"] = ([{"id": "ov-2", "label": None, "created": 1}], None)
    world["share"]([{"id": "r-3", "status": "pending", "kind": "domain", "target": "pypi.org",
                     "filed_at": 2}])
    world["runs"] = []
    _, body = _get(client)
    assert [i["label"] for i in body["items"]] == ["ov-2", "web access to pypi.org"]


def test_every_pending_access_request_counts_past_the_listing_cap(world, client):
    """The dashboard's listing stops at 50 rows; the band's count must not."""
    world["overrides"], world["runs"] = ([], None), []
    world["share"]([{"id": f"r-{n}", "status": "pending", "kind": "mount", "target": "/x",
                     "filed_at": n} for n in range(60)])
    _, body = _get(client)
    assert len(body["items"]) == 60


def test_no_share_store_yet_is_none_waiting(world, client, tmp_path):
    (tmp_path / "share-requests.json").unlink()
    _, body = _get(client)
    assert [i["kind"] for i in body["items"]] == ["approval", "run"]


@pytest.mark.parametrize("content", ["{nope", "[]", '{"requests": []}'])
def test_an_unreadable_share_store_is_a_503_never_none(world, client, tmp_path, content):
    """🔴 GUARD: unlike the store's own loader, which reads garbage as empty."""
    (tmp_path / "share-requests.json").write_text(content)
    status, body = _get(client)
    assert status == 503 and body["items"] == []
    # the store's own reason, not an incidental crash further down
    if content == "{nope":
        assert body["error"].startswith("JSONDecodeError: ")
    else:
        assert body["error"].startswith("ValueError: ")
        assert body["error"].endswith("share-requests.json: no requests table")


def test_without_a_configured_url_links_point_at_the_bound_dashboard(world, client, monkeypatch):
    monkeypatch.delenv(config.DASHBOARD_URL_ENV)
    monkeypatch.setattr(config, "live_dashboard_port", lambda: 5009)
    monkeypatch.setenv("CHELA_DASH_HOST", "127.0.0.1")
    _, body = _get(client)
    assert body["items"][1]["url"] == "http://127.0.0.1:5009/override/ov-1"


def test_an_unreadable_overrides_store_is_a_503_never_an_empty_list(world, client):
    """🔴 GUARD: unknown is not "nothing waiting"."""
    world["overrides"] = ([], "the store is corrupt")
    status, body = _get(client)
    assert status == 503
    assert body["items"] == [] and "the store is corrupt" in body["error"]


@pytest.mark.parametrize("source", ["share", "runs"])
def test_any_source_that_raises_is_a_503(world, client, monkeypatch, source):
    def boom():
        raise RuntimeError("disk gone")
    if source == "share":
        monkeypatch.setattr(share_requests, "pending", boom)
    else:
        monkeypatch.setattr(dispatcher, "list_runs", boom)
    status, body = _get(client)
    assert status == 503
    assert body == {"items": [], "error": "RuntimeError: disk gone"}
