"""The Dispatch tab (CMX-220): docs/SETTINGS_UI_INVENTORY.md's second group
("Dispatch / judge / critic policy") on ``chela.config.dashboard_setting`` — the
same precedence layer CMX-217 proved on Timing (tests/test_timing_settings.py),
generalised here past "every knob is a plain positive number":

- a bool pair (``judge_enabled``/``critic_enabled``, kind="bool") that must
  never fail validation, only normalize;
- two free-text knobs (``dispatch_workflows``, ``merge_base``, kind="text"),
  the second with an extra safety check since it feeds ``chela.contract``'s
  autonomous-merge fallback;
- a K/M/G/T-suffixed size (``worktree_disk_budget_bytes``, kind="size");
- and, unlike every Timing knob (which all reject ``<= 0``), three of these
  legitimately accept ``0`` (``max_reworks``, ``judge_max_unknown_retries``,
  ``gate_wait_seconds`` all mean "disabled" at zero) while one (``gate_max_waits``)
  does not (a ``BoundedSemaphore`` cannot be sized 0).

Four of the nine (``restart_required``) are latched at some OTHER module's own
import — ``chela.config`` itself for the judge/critic kill switches and the
dispatcher's workflow list, ``chela.contract`` for the autonomous merge base —
proving the precedence layer survives crossing a module boundary, not just
"read this function again."
"""
from __future__ import annotations

import importlib
import json

import pytest

DISPATCH_ENV_VARS = (
    "CHELA_DISPATCH_WORKFLOWS", "CHELA_MAX_REWORKS", "CHELA_JUDGE",
    "CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "CHELA_CRITIC",
    "CHELA_WORKTREE_DISK_BUDGET", "CHELA_MERGE_BASE",
    "CHELA_GATE_WAIT_S", "CHELA_GATE_MAX_WAITS",
)


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    """config + userconfig against a temp CHELA_DIR, so nothing here reads or
    writes the real ~/.chela/config.json."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    for knob_env in DISPATCH_ENV_VARS:
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

def test_registry_has_exactly_the_nine_settings_inventory_knobs(mods):
    config, _ = mods
    assert {k.key for k in config.DISPATCH_KNOBS} == {
        "dispatch_workflows", "max_reworks", "judge_enabled",
        "judge_max_unknown_retries", "critic_enabled",
        "worktree_disk_budget_bytes", "merge_base",
        "gate_wait_seconds", "gate_max_waits",
    }


def test_exactly_four_knobs_are_restart_required(mods):
    """⚖️ Corrupt (flip any of these, or leave a fifth marked restart_required)
    → RED: this is what the UI's "restart" badge — and CMX-220's own task
    description ("9 knobs, and 4 of them are NOT hot") — depend on being
    exactly right."""
    config, _ = mods
    restart = {k.key for k in config.DISPATCH_KNOBS if k.restart_required}
    assert restart == {"dispatch_workflows", "judge_enabled", "critic_enabled", "merge_base"}
    hot = {k.key for k in config.DISPATCH_KNOBS if not k.restart_required}
    assert hot == {
        "max_reworks", "judge_max_unknown_retries",
        "worktree_disk_budget_bytes", "gate_wait_seconds", "gate_max_waits",
    }


def test_named_readers_return_their_own_knobs_stored_value(mods):
    """Same shape as test_timing_settings.py's cross-check: store a DISTINCT
    value per knob, then assert each reader returns exactly its own — a reader
    wired to the wrong knob would return someone else's value instead."""
    config, userconfig = mods
    readers = {
        "max_reworks": config.max_reworks,
        "judge_max_unknown_retries": config.judge_max_unknown_retries,
        "worktree_disk_budget_bytes": config.worktree_disk_budget_bytes,
    }
    for key, reader in readers.items():
        knob = next(k for k in config.DISPATCH_KNOBS if k.key == key)
        assert reader() == knob.default

    distinct = {"max_reworks": 11, "judge_max_unknown_retries": 12,
                "worktree_disk_budget_bytes": 999_000}
    for key, value in distinct.items():
        userconfig.set_(key, value)
    for key, reader in readers.items():
        assert reader() == distinct[key], (
            f"{reader.__name__}() did not return {key}'s own stored value "
            f"({distinct[key]!r}) — got {reader()!r}, suggesting it reads a different knob"
        )


# --- GUARD 1: precedence is env > userconfig > default ------------------------

def test_precedence_env_beats_userconfig_beats_default(mods, monkeypatch):
    config, userconfig = mods
    assert config.max_reworks() == 2

    userconfig.set_("max_reworks", 5)
    assert config.max_reworks() == 5

    monkeypatch.setenv("CHELA_MAX_REWORKS", "1")
    assert config.max_reworks() == 1

    monkeypatch.delenv("CHELA_MAX_REWORKS", raising=False)
    assert config.max_reworks() == 5


# --- GUARD 2: zero is valid for three knobs, not for a fourth -----------------

def test_zero_disables_rework_and_unknown_retries(mods):
    """⚖️ Corrupt (reuse Timing's blanket ``value <= 0`` rejection here) → RED:
    0 legitimately disables rework / the cannot-verify retry (see max_reworks()'s
    and judge_max_unknown_retries()'s own docstrings)."""
    config, _ = mods
    assert config.set_dispatch("max_reworks", "0") is None
    assert config.max_reworks() == 0
    assert config.set_dispatch("judge_max_unknown_retries", "0") is None
    assert config.judge_max_unknown_retries() == 0


def test_negative_numbers_are_rejected(mods):
    config, userconfig = mods
    err = config.set_dispatch("max_reworks", "-1")
    assert err is not None
    assert userconfig.get("max_reworks") is None


def test_gate_max_waits_floor_is_one_not_zero(mods):
    """⚖️ Corrupt (drop this knob's floor=1 in favor of the shared floor=0
    default) → RED: gateanswer.py's BoundedSemaphore cannot be sized 0."""
    config, userconfig = mods
    err = config.set_dispatch("gate_max_waits", "0")
    assert err is not None
    assert userconfig.get("gate_max_waits") is None
    assert config.set_dispatch("gate_max_waits", "1") is None


def test_gate_wait_seconds_zero_is_valid_it_means_never_wait(mods):
    config, _ = mods
    assert config.set_dispatch("gate_wait_seconds", "0") is None
    assert config.dispatch_value("gate_wait_seconds") == 0


# --- GUARD 3: bool knobs never fail validation, only normalize ---------------

@pytest.mark.parametrize("raw,expected", [
    ("false", False), ("0", False), ("no", False), ("off", False),
    ("true", True), ("1", True), ("anything-else", True),
])
def test_bool_kind_normalizes_and_never_errors(mods, raw, expected):
    config, _ = mods
    err, value = config.validate_dispatch("judge_enabled", raw)
    assert err is None
    assert value is expected


def test_set_dispatch_bool_persists_and_clears(mods):
    config, userconfig = mods
    assert config.set_dispatch("critic_enabled", "false") is None
    assert userconfig.get("critic_enabled") is False
    assert config.dispatch_value("critic_enabled") is False

    assert config.set_dispatch("critic_enabled", "") is None
    assert userconfig.get("critic_enabled") is None
    assert config.dispatch_value("critic_enabled") is True


# --- GUARD 4: merge_base is a safety-adjacent text field ----------------------

@pytest.mark.parametrize("bad", ["-oops", "main branch", "a..b", "br@nch"])
def test_merge_base_rejects_unsafe_branch_names(mods, bad):
    """⚖️ Corrupt (drop the ``check=`` callback for this knob) → RED: nothing
    else in this module stops a leading '-' or embedded whitespace from being
    stored."""
    config, userconfig = mods
    err = config.set_dispatch("merge_base", bad)
    assert err is not None
    assert userconfig.get("merge_base") is None


def test_merge_base_accepts_a_plain_branch_name(mods):
    config, _ = mods
    assert config.set_dispatch("merge_base", "dogfood/dev-2") is None
    assert config.dispatch_value("merge_base") == "dogfood/dev-2"


def test_merge_base_does_not_itself_widen_the_forbidden_bases_gate(mods):
    """CMX-220 makes ``merge_base`` dashboard-writable but must NOT loosen the
    merge-safety boundary: "main" is a syntactically fine branch name (this
    module has no opinion on WHICH branch, only that it's safe to shell out
    with) — chela.contract.FORBIDDEN_BASES / the NEVER-line check is what
    actually refuses it, live, at merge time, unconditionally, regardless of
    where the string came from (see tests/test_contract.py's own
    env-cannot-widen guards for that half of the boundary)."""
    config, _ = mods
    assert config.set_dispatch("merge_base", "main") is None


# --- GUARD 5: worktree_disk_budget_bytes parses K/M/G/T sizes ----------------

def test_worktree_disk_budget_accepts_suffixed_sizes(mods):
    config, _ = mods
    assert config.set_dispatch("worktree_disk_budget_bytes", "20G") is None
    assert config.worktree_disk_budget_bytes() == 20 * 1024**3


def test_worktree_disk_budget_rejects_negative(mods):
    config, userconfig = mods
    err = config.set_dispatch("worktree_disk_budget_bytes", "-5G")
    assert err is not None
    assert userconfig.get("worktree_disk_budget_bytes") is None


def test_worktree_disk_budget_zero_means_off(mods):
    config, _ = mods
    assert config.set_dispatch("worktree_disk_budget_bytes", "0") is None
    assert config.worktree_disk_budget_bytes() == 0


def test_worktree_disk_budget_garbage_falls_through_to_off(mods, monkeypatch):
    config, _ = mods
    monkeypatch.setenv("CHELA_WORKTREE_DISK_BUDGET", "not-a-size")
    assert config.worktree_disk_budget_bytes() == 0


# --- unknown key / batch semantics --------------------------------------------

def test_set_dispatch_rejects_unknown_key(mods):
    config, _ = mods
    assert config.set_dispatch("not_a_real_knob", "x") is not None


def test_set_dispatch_does_not_clobber_other_knobs(mods):
    config, _ = mods
    config.set_dispatch("max_reworks", "7")
    config.set_dispatch("gate_max_waits", "3")
    assert config.max_reworks() == 7
    assert config.dispatch_value("gate_max_waits") == 3


# --- restart-required constants: latched, not live ----------------------------

def test_judge_and_critic_enabled_are_latched_not_live(tmp_path, monkeypatch):
    """JUDGE_ENABLED/CRITIC_ENABLED are read ONCE at chela.config's own import
    (same shape STATUS_CMD_TIMEOUT_S/STATUS_TTL_S promise in agent_manager.py) —
    a dashboard write is only visible after a reload (== the daemon restarting).
    ⚖️ Corrupt (make these re-resolve on every access instead of latching once)
    → this specific test goes RED on the "NOT live" assertion."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    for e in DISPATCH_ENV_VARS:
        monkeypatch.delenv(e, raising=False)
    import chela.config as config
    importlib.reload(config)
    import chela.userconfig as userconfig
    importlib.reload(userconfig)

    assert config.JUDGE_ENABLED is True
    userconfig.set_("judge_enabled", False)
    assert config.JUDGE_ENABLED is True          # stale until a restart/reload

    importlib.reload(config)
    assert config.JUDGE_ENABLED is False          # the "restart" picks it up


def test_dispatch_workflows_is_latched_not_live(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    for e in DISPATCH_ENV_VARS:
        monkeypatch.delenv(e, raising=False)
    import chela.config as config
    importlib.reload(config)
    import chela.userconfig as userconfig
    importlib.reload(userconfig)

    assert config.DISPATCH_WORKFLOWS == []
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("# wf\n")
    userconfig.set_("dispatch_workflows", str(wf))
    assert config.DISPATCH_WORKFLOWS == []        # stale until a restart/reload

    importlib.reload(config)
    assert config.DISPATCH_WORKFLOWS == [wf.resolve()]


def test_autonomous_base_reads_through_the_dispatch_registry(tmp_path, monkeypatch):
    """chela.contract.AUTONOMOUS_BASE lives in a DIFFERENT module — proving the
    registry generalises across a module boundary, not just within config.py."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    for e in DISPATCH_ENV_VARS:
        monkeypatch.delenv(e, raising=False)
    import chela.config as config
    importlib.reload(config)
    import chela.userconfig as userconfig
    importlib.reload(userconfig)
    import chela.contract as contract
    importlib.reload(contract)

    assert contract.AUTONOMOUS_BASE == "dev"

    userconfig.set_("merge_base", "dogfood")
    importlib.reload(contract)
    assert contract.AUTONOMOUS_BASE == "dogfood"

    monkeypatch.setenv("CHELA_MERGE_BASE", "release-train")
    importlib.reload(contract)
    assert contract.AUTONOMOUS_BASE == "release-train"   # env still wins


# --- the write path (POST /api/config/dispatch) --------------------------------

@pytest.fixture(autouse=True)
def no_live_state(monkeypatch):
    """Same seam as tests/test_agent_model.py's/test_timing_settings.py's
    ``no_live_state``: keep this file from touching the machine's real
    scheduler.db / WORKFLOW.md."""
    from chela.dashboard import app as dash
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [])


@pytest.fixture()
def client(mods):
    from chela.dashboard import app as dash
    return dash.app.test_client()


def test_get_reports_stored_default_and_effective(mods, client):
    body = client.get("/api/config/dispatch").get_json()
    knobs = {k["key"]: k for k in body["knobs"]}
    assert set(knobs) == {k.key for k in mods[0].DISPATCH_KNOBS}
    row = knobs["max_reworks"]
    assert row["stored"] == ""
    assert row["default"] == 2
    assert row["effective"] == 2
    assert row["env"] == "CHELA_MAX_REWORKS"
    assert row["source"] == "default"
    assert row["restart_required"] is False
    assert row["kind"] == "number"


def test_get_marks_restart_required_rows(mods, client):
    body = client.get("/api/config/dispatch").get_json()
    knobs = {k["key"]: k for k in body["knobs"]}
    for key in ("dispatch_workflows", "judge_enabled", "critic_enabled", "merge_base"):
        assert knobs[key]["restart_required"] is True
    for key in ("max_reworks", "judge_max_unknown_retries",
                "worktree_disk_budget_bytes", "gate_wait_seconds", "gate_max_waits"):
        assert knobs[key]["restart_required"] is False


def test_post_sets_a_valid_value(mods, client):
    config, _ = mods
    resp = client.post("/api/config/dispatch", json={"max_reworks": "5"})
    assert resp.status_code == 200
    knobs = {k["key"]: k for k in resp.get_json()["knobs"]}
    assert knobs["max_reworks"]["effective"] == 5
    assert _stored(config)["max_reworks"] == 5


def test_post_sets_a_bool_and_a_text_knob_together(mods, client):
    config, _ = mods
    resp = client.post("/api/config/dispatch", json={
        "judge_enabled": "false",
        "merge_base": "dogfood",
    })
    assert resp.status_code == 200
    knobs = {k["key"]: k for k in resp.get_json()["knobs"]}
    assert knobs["judge_enabled"]["effective"] is False
    assert knobs["merge_base"]["effective"] == "dogfood"


def test_post_rejects_an_unsafe_merge_base_and_keeps_the_current_one(mods, client):
    config, userconfig = mods
    userconfig.set_("merge_base", "dogfood")
    resp = client.post("/api/config/dispatch", json={"merge_base": "-oops"})
    assert resp.status_code == 400
    assert "merge_base" in resp.get_json()["errors"]
    assert _stored(config)["merge_base"] == "dogfood"


def test_a_partial_batch_rejects_the_whole_write(mods, client):
    """One bad key in a multi-key POST fails the request, and neither key's
    value changes — no partial application of a batch write, same guarantee
    /api/config/timing makes."""
    config, _ = mods
    resp = client.post("/api/config/dispatch", json={
        "max_reworks": "5",
        "gate_max_waits": "0",
    })
    assert resp.status_code == 400
    assert "max_reworks" not in _stored(config)


def test_post_empty_clears_back_to_default(mods, client):
    config, userconfig = mods
    userconfig.set_("max_reworks", 5)
    resp = client.post("/api/config/dispatch", json={"max_reworks": ""})
    assert resp.status_code == 200
    knobs = {k["key"]: k for k in resp.get_json()["knobs"]}
    assert knobs["max_reworks"]["stored"] == ""
    assert knobs["max_reworks"]["effective"] == 2
    assert "max_reworks" not in _stored(config)


def test_post_rejects_an_unknown_key(mods, client):
    resp = client.post("/api/config/dispatch", json={"not_a_real_knob": "5"})
    assert resp.status_code == 400
    assert "not_a_real_knob" in resp.get_json()["errors"]
