"""CMX-390: a parent's archive cascades to its child sessions, and a pinned session is never
archived. Server-side, so every device gets the same answer.

* :func:`chela.sidebar_archive.archive_tree` / :func:`~chela.sidebar_archive.unarchive_tree`
  walk the session-parent table (:mod:`chela.parentage`, CMX-389). The window facts
  :func:`~chela.sidebar_archive.archive_window` reads are stubbed (the CMX-75 tests in
  ``tests/test_sidebar_archive.py`` hold the real-tmux half); the kill and the spawn are spies.
* The pins live in the server layout (:mod:`chela.sidebar_layout`), by SESSION key.
* The routes: one Archive on a parent closes the whole live subtree; Unarchive resumes it.

The client half (nesting, the Pinned section, the disabled Archive) is
``chela/dashboard/frontend/src/features/sidebar/tests/tree.test.tsx``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chela import parentage
from chela import sidebar_archive as sa
from chela import sidebar_layout as sl
from chela import spawn as spawn_mod

# P launched C1 and C2; C1 launched G. Windows @1 / @2 / @3 / @4.
P, C1, C2, G = "sess-parent", "sess-child-1", "sess-child-2", "sess-grandchild"
WID = {P: "@1", C1: "@2", C2: "@3", G: "@4"}
SID_OF = {w: s for s, w in WID.items()}
TABLE = {
    C1: {"parent": P, "source": "spawn", "ts": 1.0},
    C2: {"parent": P, "source": "first_message", "ts": 2.0},
    G: {"parent": C1, "source": "spawn", "ts": 3.0},
}


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Four live idle windows whose sessions resolve; the kill and the spawn are spied."""
    from chela import agent_manager, discovery, sessionids, sessions

    monkeypatch.setattr(sa, "_STORE", tmp_path / "sidebar-archive.json")
    monkeypatch.setattr(sa, "_REBINDS", tmp_path / "topic-rebinds.json")
    monkeypatch.setattr(sl, "_STORE", tmp_path / "sidebar-layout.json")
    monkeypatch.setattr(sa.epoch, "current", lambda: "e1")
    live = dict(WID)   # sid -> wid of the windows open now
    status: dict[str, str] = {}
    monkeypatch.setattr(discovery, "get_windows_by_id",
                        lambda: {w: f"win{w[1:]}" for w in live.values()})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: str(tmp_path))
    monkeypatch.setattr(sessions, "panes", lambda force=False: {})
    monkeypatch.setattr(sessions, "wid_for_session", lambda sid, pane_map=None: live.get(sid))
    monkeypatch.setattr(sa, "window_status", lambda wid: (True, status.get(wid, "idle")))
    monkeypatch.setattr(sa, "resolve_session_id", lambda wid, pane=None: SID_OF.get(wid))
    monkeypatch.setattr(sa, "_rc_name", lambda wid, name: None)
    monkeypatch.setattr(sa, "_bound_thread", lambda wid: None)
    killed: list[str] = []

    def close(wid):
        killed.append(wid)
        live.pop(SID_OF[wid], None)
    monkeypatch.setattr(sa, "close_window", close)
    spawned: list[str] = []
    n = iter(range(10, 99))

    def spawn(cwd, **kw):
        sid = kw["command"].split()[-1]
        wid = f"@{next(n)}"
        spawned.append(sid)
        live[sid] = wid
        return SimpleNamespace(ok=True, error=None, wid=wid, name=kw.get("name"), cwd=str(cwd))
    monkeypatch.setattr(spawn_mod, "spawn_window", spawn)
    monkeypatch.setattr(agent_manager, "mark_manual_name", lambda wid: None)
    monkeypatch.setattr(sessionids, "set_session_id", lambda wid, sid: None)
    return SimpleNamespace(live=live, status=status, killed=killed, spawned=spawned,
                           wid_of=lambda sid: live.get(sid))


def _tree(world, wid=WID[P], pinned=frozenset(), table=TABLE):
    return sa.archive_tree(wid, orch_wid=None, dispatched=set(), pinned=pinned, parents=table,
                           wid_of=world.wid_of)


def _archived() -> set[str]:
    return set(sa._load()["sessions"])


# --- the parent table read top-down -------------------------------------------------------

def test_children_of_reads_the_table_top_down_and_skips_a_self_parent_or_junk():
    table = {**TABLE, "loop": {"parent": "loop"}, "junk": "not a dict", "none": {"parent": None}}
    assert parentage.children_of(table) == {P: [C1, C2], C1: [G]}


# --- archive_tree --------------------------------------------------------------------------

def test_archiving_the_parent_archives_every_descendant_parent_first(world):
    out = _tree(world)
    assert [(w, o.ok) for w, o in out] == [("@1", True), ("@2", True), ("@3", True), ("@4", True)]
    assert world.killed == ["@1", "@2", "@3", "@4"]
    assert _archived() == {f"s:{s}" for s in (P, C1, C2, G)}


def test_with_no_parents_recorded_only_the_window_itself_is_archived(world):
    out = _tree(world, table={})
    assert [w for w, _ in out] == ["@1"] and world.killed == ["@1"]


def test_a_refused_parent_archives_nothing_below_it(world):
    world.status["@1"] = "busy"
    out = _tree(world)
    assert [(w, o.ok) for w, o in out] == [("@1", False)]
    assert world.killed == [] and _archived() == set()


def test_a_refused_child_keeps_its_own_subtree_and_the_rest_still_goes(world):
    world.status["@2"] = "waiting"   # C1 is waiting on you: it stays, and G stays with it
    out = _tree(world)
    assert [(w, o.ok) for w, o in out] == [("@1", True), ("@2", False), ("@3", True)]
    assert world.killed == ["@1", "@3"]
    assert _archived() == {f"s:{P}", f"s:{C2}"}


def test_a_child_with_no_live_window_ends_its_branch(world):
    world.live.pop(C1)   # C1 was closed (or archived) already; G is not walked through it
    out = _tree(world)
    assert [w for w, _ in out] == ["@1", "@3"] and world.killed == ["@1", "@3"]


def test_a_pinned_child_is_not_archived_and_neither_is_anything_under_it(world):
    out = _tree(world, pinned=frozenset({f"s:{C1}"}))
    assert [w for w, _ in out] == ["@1", "@3"]
    assert world.killed == ["@1", "@3"]
    assert f"s:{C1}" not in _archived() and f"s:{G}" not in _archived()


def test_a_pinned_session_is_never_archived_by_hand(world):
    out = sa.archive_window("@1", orch_wid=None, dispatched=set(), pinned=frozenset({f"s:{P}"}))
    assert not out.ok and out.error == "@1 (win1) is pinned: unpin to archive"
    assert world.killed == [] and _archived() == set()


def test_a_pin_on_another_session_does_not_block_this_one(world):
    out = sa.archive_window("@1", orch_wid=None, dispatched=set(), pinned=frozenset({f"s:{C1}"}))
    assert out.ok and world.killed == ["@1"]


def test_a_loop_in_a_corrupt_table_is_walked_once(world):
    table = {**TABLE, P: {"parent": G}}   # G -> P closes a loop
    out = _tree(world, table=table)
    assert sorted(w for w, _ in out) == ["@1", "@2", "@3", "@4"]


# --- unarchive_tree ------------------------------------------------------------------------

def test_unarchiving_the_parent_resumes_the_archived_subtree(world):
    _tree(world)
    out = sa.unarchive_tree(f"s:{P}", TABLE)
    assert [(k, o.ok) for k, o in out] == [(f"s:{s}", True) for s in (P, C1, C2, G)]
    assert world.spawned == [P, C1, C2, G]
    assert _archived() == set()


def test_unarchive_walks_only_through_archived_sessions(world):
    _tree(world, pinned=frozenset({f"s:{C1}"}))   # C1 (pinned) and G stay live
    sa.archive_window("@4", orch_wid=None, dispatched=set())   # G archived on its own later
    out = sa.unarchive_tree(f"s:{P}", TABLE)
    assert [k for k, _ in out] == [f"s:{P}", f"s:{C2}"]
    assert _archived() == {f"s:{G}"}, "G hangs under a LIVE C1, not under the archived P"


def test_a_failed_parent_resume_resumes_nothing_below_it(world, monkeypatch):
    _tree(world)
    monkeypatch.setattr(spawn_mod, "spawn_window",
                        lambda cwd, **kw: SimpleNamespace(ok=False, error="no tmux", wid=None, name=None))
    out = sa.unarchive_tree(f"s:{P}", TABLE)
    assert [(k, o.ok) for k, o in out] == [(f"s:{P}", False)]
    assert len(_archived()) == 4


def test_a_failed_child_resume_keeps_its_own_subtree_archived(world, monkeypatch):
    _tree(world)
    real = spawn_mod.spawn_window

    def spawn(cwd, **kw):
        if kw["command"].endswith(C1):
            return SimpleNamespace(ok=False, error="boom", wid=None, name=None)
        return real(cwd, **kw)
    monkeypatch.setattr(spawn_mod, "spawn_window", spawn)
    out = sa.unarchive_tree(f"s:{P}", TABLE)
    assert [(k, o.ok) for k, o in out] == [(f"s:{P}", True), (f"s:{C1}", False), (f"s:{C2}", True)]
    assert _archived() == {f"s:{C1}", f"s:{G}"}


# --- the archive's state carries each record's parent -------------------------------------

def test_state_gives_each_archived_session_its_parent(world, monkeypatch):
    monkeypatch.setattr(parentage, "parents", lambda: TABLE)
    _tree(world)
    got = {r["session_id"]: r["parent_session_id"] for r in sa.state()["sessions"]}
    assert got == {P: None, C1: P, C2: P, G: C1}


def test_an_unreadable_parent_table_nests_nothing_and_hides_nothing(world, monkeypatch):
    _tree(world, table={})

    def boom():
        raise RuntimeError("corrupt")
    monkeypatch.setattr(parentage, "parents", boom)
    st = sa.state()
    assert [(r["session_id"], r["parent_session_id"]) for r in st["sessions"]] == [(P, None)]


# --- pins in the server layout ------------------------------------------------------------

def test_pins_keep_only_session_keys(world):
    sl.update({"pins": [f"s:{P}", "w:@3", "run:CMX-1", "s:", f"s:{P}", 7]})
    assert sl.state()["layout"]["pins"] == [f"s:{P}"]
    assert sl.pinned_keys() == frozenset({f"s:{P}"})


def test_no_layout_pins_nothing(world):
    assert sl.pinned_keys() == frozenset()


def test_a_pin_write_leaves_the_other_fields_alone(world):
    sl.update({"order": ["/srv/a"]})
    sl.update({"pins": [f"s:{C1}"]})
    assert sl.state()["layout"]["order"] == ["/srv/a"]
    sl.update({"order": ["/srv/b"]})
    assert sl.state()["layout"]["pins"] == [f"s:{C1}"]


# --- the routes -------------------------------------------------------------------------------

@pytest.fixture
def client(world, monkeypatch):
    from chela.dashboard import app as dash

    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", True)
    monkeypatch.setattr(dash.discovery, "get_all_windows",
                        lambda: {f"win{w[1:]}": w for w in world.live.values()})
    monkeypatch.setattr(dash.inbox, "orchestrator_wid", lambda store=None: None)
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(sa, "drop_resumed_elsewhere", lambda is_live: [])
    monkeypatch.setattr(parentage, "parents", lambda: TABLE)
    return dash.app.test_client()


def test_route_archive_on_the_parent_closes_the_subtree(client, world):
    body = client.post("/api/sidebar/archive", json={"keys": ["w:@1"]}).get_json()
    assert body["ok"] and body["refused"] == []
    assert body["closed"] == ["@1", "@2", "@3", "@4"]
    assert body["archived"] == [f"s:{s}" for s in (P, C1, C2, G)]


def test_route_a_child_key_its_parent_already_closed_is_not_refused(client, world):
    body = client.post("/api/sidebar/archive", json={"keys": ["w:@1", "w:@2"]}).get_json()
    assert body["ok"] and body["refused"] == [] and body["closed"] == ["@1", "@2", "@3", "@4"]


def test_route_a_pinned_session_is_refused_and_nothing_closes(client, world):
    sl.update({"pins": [f"s:{P}"]})
    body = client.post("/api/sidebar/archive", json={"keys": ["w:@1"]}).get_json()
    assert not body["ok"] and body["closed"] == [] and world.killed == []
    assert body["refused"] == [{"key": "w:@1", "error": "@1 (win1) is pinned: unpin to archive"}]


def test_route_a_pinned_child_stays_out_of_its_parents_archive(client, world):
    sl.update({"pins": [f"s:{C2}"]})
    body = client.post("/api/sidebar/archive", json={"keys": ["w:@1"]}).get_json()
    assert body["ok"] and body["closed"] == ["@1", "@2", "@4"]
    assert world.live == {C2: "@3"}


def test_route_a_refused_child_is_reported_by_its_window(client, world):
    world.status["@3"] = "busy"
    body = client.post("/api/sidebar/archive", json={"keys": ["w:@1"]}).get_json()
    assert not body["ok"] and body["closed"] == ["@1", "@2", "@4"]
    assert body["refused"] == [{"key": "w:@3", "error": "@3 (win3) is working — not archived"}]


def test_route_an_unreadable_pane_map_refuses_the_archive_and_closes_nothing(client, world, monkeypatch):
    from chela import sessions

    def boom(force=False):
        raise OSError("tmux is gone")
    monkeypatch.setattr(sessions, "panes", boom)
    body = client.post("/api/sidebar/archive", json={"keys": ["w:@1"]}).get_json()
    assert not body["ok"] and body["closed"] == [] and world.killed == []
    assert [r["key"] for r in body["refused"]] == ["w:@1"]
    assert "child sessions" in body["refused"][0]["error"]


def test_route_unarchive_on_the_parent_resumes_all_of_it(client, world):
    client.post("/api/sidebar/archive", json={"keys": ["w:@1"]})
    body = client.post("/api/sidebar/unarchive", json={"keys": [f"s:{P}"]}).get_json()
    assert body["ok"] and body["refused"] == []
    assert [r["key"] for r in body["resumed"]] == [f"s:{s}" for s in (P, C1, C2, G)]
    assert world.spawned == [P, C1, C2, G]


def test_route_unarchive_naming_parent_and_child_resumes_the_child_once(client, world):
    client.post("/api/sidebar/archive", json={"keys": ["w:@1"]})
    body = client.post("/api/sidebar/unarchive", json={"keys": [f"s:{P}", f"s:{C1}"]}).get_json()
    assert body["ok"] and world.spawned == [P, C1, C2, G]


def test_route_unarchive_reports_a_failed_child_as_refused(client, world, monkeypatch):
    client.post("/api/sidebar/archive", json={"keys": ["w:@1"]})
    real = spawn_mod.spawn_window

    def spawn(cwd, **kw):
        if kw["command"].endswith(C2):
            return SimpleNamespace(ok=False, error="boom", wid=None, name=None)
        return real(cwd, **kw)
    monkeypatch.setattr(spawn_mod, "spawn_window", spawn)
    body = client.post("/api/sidebar/unarchive", json={"keys": [f"s:{P}"]}).get_json()
    assert not body["ok"] and body["refused"] == [{"key": f"s:{C2}", "error": "boom"}]


def test_route_archive_state_nests_by_parent(client, world):
    client.post("/api/sidebar/archive", json={"keys": ["w:@1"]})
    st = client.get("/api/sidebar/archive").get_json()
    assert {r["session_id"]: r["parent_session_id"] for r in st["sessions"]} == {P: None, C1: P, C2: P, G: C1}
