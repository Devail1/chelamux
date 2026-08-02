"""The Timing tab (CMX-217): the general dashboard-setting precedence layer
(`chela.config.dashboard_setting`), proved on the "Daemon loop intervals" knob
group — the cheapest of the ~40 candidates in `docs/SETTINGS_UI_INVENTORY.md`
(CMX-207), because every member is already read per call, nothing latched at
import (`agent_manager.py`'s two aside), and nothing trust-boundary-adjacent.

Three properties matter here, each mirroring the pre-existing
`agent_permission_mode`/`agent_model` rails (see tests/test_agent_model.py):

1. **Precedence is userconfig.json > env var > built-in default**, exactly
   like `_projects_dir()`'s doc-comment already promises for its one knob —
   this generalises it, it does not invent a new order.
2. **A bad value at any level falls through, never raises.** The dispatcher
   and daemon read these unattended; a hand-edited config.json or a typo'd
   env var must degrade to the next level, not take a tick down.
3. **The write path validates server-side.** `/api/config/timing`'s `<input>`
   is a convenience, not the gate — an unknown key or a non-numeric/
   non-positive value is rejected 400 and the stored value is left untouched,
   fail-closed, same shape the model/permission-mode enums already use.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    """config + userconfig against a temp CHELA_DIR, so nothing here reads or
    writes the real ~/.chela/config.json."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    for knob_env in (
        "CHELA_SCHEDULER_POLL_INTERVAL", "CHELA_CAPTURE_INTERVAL_SECONDS",
        "CHELA_CACHE_STALE_SECONDS", "CHELA_CONTEXT_RETENTION_DAYS",
        "CHELA_DISPATCH_TICK_INTERVAL", "CHELA_STATUS_CMD_TIMEOUT_S",
        "CHELA_STATUS_TTL_S", "CHELA_DOCTOR_CHECK_INTERVAL",
        "CHELA_DEFAULT_CONTEXT_WINDOW",
    ):
        monkeypatch.delenv(knob_env, raising=False)
    import chela.config as config
    importlib.reload(config)
    import chela.userconfig as userconfig
    importlib.reload(userconfig)
    return config, userconfig


def _stored(config) -> dict:
    try:
        return json.loads((config.CHELA_DIR / "config.json").read_text())
    except FileNotFoundError:
        return {}


# --- the registry itself -----------------------------------------------------

def test_every_named_reader_has_a_registry_entry(mods):
    """The nine functions the rest of chela calls (main.py, capabilities.py,
    context.py, dispatcher.py, agent_manager.py) all resolve through the
    registry, not a private literal — a knob added to one but not the other
    would silently desync the API/UI from what production actually reads."""
    config, _ = mods
    readers = {
        "scheduler_poll_interval_seconds": config.scheduler_poll_interval,
        "capture_interval_seconds": config.capture_interval_seconds,
        "cache_stale_seconds": config.cache_stale_seconds,
        "context_retention_days": config.context_snapshot_retention_days,
        "dispatch_tick_interval_seconds": config.dispatch_tick_interval,
        "status_cmd_timeout_seconds": config.status_cmd_timeout_s,
        "status_ttl_seconds": config.status_ttl_s,
        "doctor_check_interval_seconds": config.doctor_check_interval,
        "default_context_window": config.default_context_window,
    }
    keys = {k.key for k in config.TIMING_KNOBS}
    assert keys == set(readers)
    for key, reader in readers.items():
        knob = next(k for k in config.TIMING_KNOBS if k.key == key)
        assert reader() == knob.default == config.timing_value(key)


# --- GUARD 1: precedence is userconfig > env > default -----------------------

def test_precedence_userconfig_beats_env_beats_default(mods, monkeypatch):
    """⚖️ Corrupt (read env before userconfig, or skip userconfig entirely) →
    this goes RED for the middle/last assertion."""
    config, userconfig = mods
    assert config.scheduler_poll_interval() == 30                  # built-in default

    monkeypatch.setenv("CHELA_SCHEDULER_POLL_INTERVAL", "45")
    assert config.scheduler_poll_interval() == 45                  # env beats default

    userconfig.set_("scheduler_poll_interval_seconds", 90)
    assert config.scheduler_poll_interval() == 90                  # userconfig beats env

    userconfig.set_("scheduler_poll_interval_seconds", "")         # clear
    assert config.scheduler_poll_interval() == 45                  # falls back to env


# --- GUARD 2: a bad value at any level falls through, never raises -----------

@pytest.mark.parametrize("bad", ["not-a-number", "", None, "12.5x", "[]"])
def test_bad_userconfig_value_falls_through_to_env(mods, monkeypatch, bad):
    config, userconfig = mods
    monkeypatch.setenv("CHELA_SCHEDULER_POLL_INTERVAL", "45")
    userconfig._save({"scheduler_poll_interval_seconds": bad})
    assert config.scheduler_poll_interval() == 45


def test_bad_env_value_falls_through_to_default(mods, monkeypatch):
    config, _ = mods
    monkeypatch.setenv("CHELA_SCHEDULER_POLL_INTERVAL", "not-a-number")
    assert config.scheduler_poll_interval() == 30


def test_corrupt_config_file_does_not_crash(mods):
    """The daemon runs unattended; a truncated config.json degrades to
    env/default, it does not raise out of a tick."""
    config, _ = mods
    path = config.CHELA_DIR / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert config.scheduler_poll_interval() == 30


def test_float_knobs_cast_correctly(mods, monkeypatch):
    config, userconfig = mods
    assert config.status_cmd_timeout_s() == 45.0
    monkeypatch.setenv("CHELA_STATUS_CMD_TIMEOUT_S", "60")
    assert config.status_cmd_timeout_s() == 60.0
    userconfig.set_("status_cmd_timeout_seconds", 90)
    assert config.status_cmd_timeout_s() == 90.0


# --- GUARD 3: set_timing() validates and fails closed -------------------------

def test_set_timing_persists_a_valid_value(mods):
    config, userconfig = mods
    err = config.set_timing("scheduler_poll_interval_seconds", "45")
    assert err is None
    assert config.scheduler_poll_interval() == 45
    assert userconfig.get("scheduler_poll_interval_seconds") == 45


def test_set_timing_empty_clears(mods):
    config, userconfig = mods
    config.set_timing("scheduler_poll_interval_seconds", "45")
    err = config.set_timing("scheduler_poll_interval_seconds", "")
    assert err is None
    assert userconfig.get("scheduler_poll_interval_seconds") is None
    assert config.scheduler_poll_interval() == 30


@pytest.mark.parametrize("bad", ["bogus", "12.5x", "[]", "1;2"])
def test_set_timing_rejects_non_numeric(mods, bad):
    config, userconfig = mods
    err = config.set_timing("scheduler_poll_interval_seconds", bad)
    assert err is not None
    assert userconfig.get("scheduler_poll_interval_seconds") is None


@pytest.mark.parametrize("bad", ["0", "-1", "-30"])
def test_set_timing_rejects_non_positive(mods, bad):
    """⚖️ Corrupt (drop the `value <= 0` check) → RED: a 0s poll interval would
    busy-loop the daemon."""
    config, userconfig = mods
    err = config.set_timing("scheduler_poll_interval_seconds", bad)
    assert err is not None
    assert userconfig.get("scheduler_poll_interval_seconds") is None


def test_set_timing_rejects_unknown_key(mods):
    config, _ = mods
    err = config.set_timing("not_a_real_knob", "45")
    assert err is not None


def test_set_timing_does_not_clobber_other_knobs(mods):
    config, userconfig = mods
    config.set_timing("scheduler_poll_interval_seconds", "45")
    config.set_timing("doctor_check_interval_seconds", "1800")
    assert config.scheduler_poll_interval() == 45
    assert config.doctor_check_interval() == 1800


def test_persists_across_a_restart(mods):
    """Written by the dashboard process, read by a freshly-started daemon —
    the store is the file, not in-process state."""
    config, userconfig = mods
    config.set_timing("scheduler_poll_interval_seconds", "45")
    importlib.reload(userconfig)
    importlib.reload(config)
    assert config.scheduler_poll_interval() == 45


# --- the write path (POST /api/config/timing) ---------------------------------

@pytest.fixture(autouse=True)
def no_live_state(monkeypatch):
    """Same seam as tests/test_agent_model.py's `no_live_state`: keep this file
    from touching the machine's real scheduler.db / WORKFLOW.md."""
    from chela.dashboard import app as dash
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [])


@pytest.fixture()
def client(mods):
    from chela.dashboard import app as dash
    return dash.app.test_client()


def test_get_reports_stored_default_and_effective(mods, client):
    body = client.get("/api/config/timing").get_json()
    knobs = {k["key"]: k for k in body["knobs"]}
    assert set(knobs) == {k.key for k in mods[0].TIMING_KNOBS}
    row = knobs["scheduler_poll_interval_seconds"]
    assert row["stored"] == ""
    assert row["default"] == 30
    assert row["effective"] == 30
    assert row["env"] == "CHELA_SCHEDULER_POLL_INTERVAL"


def test_post_sets_a_valid_value(mods, client):
    config, userconfig = mods
    resp = client.post("/api/config/timing", json={"scheduler_poll_interval_seconds": "45"})
    assert resp.status_code == 200
    knobs = {k["key"]: k for k in resp.get_json()["knobs"]}
    assert knobs["scheduler_poll_interval_seconds"]["effective"] == 45
    assert _stored(config)["scheduler_poll_interval_seconds"] == 45


def test_post_sets_multiple_values_at_once(mods, client):
    config, _ = mods
    resp = client.post("/api/config/timing", json={
        "scheduler_poll_interval_seconds": "45",
        "doctor_check_interval_seconds": "1800",
    })
    assert resp.status_code == 200
    knobs = {k["key"]: k for k in resp.get_json()["knobs"]}
    assert knobs["scheduler_poll_interval_seconds"]["effective"] == 45
    assert knobs["doctor_check_interval_seconds"]["effective"] == 1800


@pytest.mark.parametrize("bad", ["bogus", "0", "-5", "12.5x"])
def test_post_rejects_a_bad_value_and_keeps_the_current_one(mods, client, bad):
    config, userconfig = mods
    userconfig.set_("scheduler_poll_interval_seconds", 45)
    resp = client.post("/api/config/timing", json={"scheduler_poll_interval_seconds": bad})
    assert resp.status_code == 400
    assert "scheduler_poll_interval_seconds" in resp.get_json()["errors"]
    assert _stored(config)["scheduler_poll_interval_seconds"] == 45
    assert config.scheduler_poll_interval() == 45


def test_post_rejects_an_unknown_key(mods, client):
    resp = client.post("/api/config/timing", json={"not_a_real_knob": "45"})
    assert resp.status_code == 400
    assert "not_a_real_knob" in resp.get_json()["errors"]


def test_post_empty_clears_back_to_default(mods, client):
    config, userconfig = mods
    userconfig.set_("scheduler_poll_interval_seconds", 45)
    resp = client.post("/api/config/timing", json={"scheduler_poll_interval_seconds": ""})
    assert resp.status_code == 200
    knobs = {k["key"]: k for k in resp.get_json()["knobs"]}
    assert knobs["scheduler_poll_interval_seconds"]["stored"] == ""
    assert knobs["scheduler_poll_interval_seconds"]["effective"] == 30
    assert "scheduler_poll_interval_seconds" not in _stored(config)


def test_a_partial_batch_rejects_the_whole_write(mods, client):
    """One bad key in a multi-key POST fails the request, and neither key's
    value changes — no partial application of a batch write."""
    config, userconfig = mods
    resp = client.post("/api/config/timing", json={
        "scheduler_poll_interval_seconds": "45",
        "doctor_check_interval_seconds": "not-a-number",
    })
    assert resp.status_code == 400
    assert "scheduler_poll_interval_seconds" not in _stored(config)
