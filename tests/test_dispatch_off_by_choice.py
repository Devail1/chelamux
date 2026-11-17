"""CMX-388: ``CHELA_DISPATCH=off`` tells dispatch OFF BY CHOICE apart from OFF BY ACCIDENT.

The empty-``CHELA_DISPATCH_WORKFLOWS`` WARN exists because of the nine-hour silent-off
incident (docs/CONFIG.md "A config is not a capability"), and it must stay. Only the
explicit switch earns the quiet line:

* empty workflows, no switch → the daemon's startup WARNING and doctor's WARN, unchanged;
* the switch → an INFO line at startup and an INFO in doctor;
* the switch AND workflows → a contradiction: doctor ERROR, and dispatch stays OFF.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chela import capabilities, config, doctor
from chela.dashboard import app as dash

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def chela_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir()
    return tmp_path


def _set(monkeypatch, *, workflows, off: bool):
    """What config latches at import, for one (workflows, switch) combination — the switch
    wins, exactly as config.py computes DISPATCH_WORKFLOWS."""
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS_CONFIGURED", list(workflows))
    monkeypatch.setattr(config, "DISPATCH_OFF_BY_CHOICE", off)
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [] if off else list(workflows))


def _doctor(monkeypatch, *, workflows, off):
    _set(monkeypatch, workflows=workflows, off=off)
    return doctor.audit(doctor.fact("daemon.capabilities"))


def _levels(findings, needle):
    return [f.level for f in findings if needle in f.title]


# --- doctor -------------------------------------------------------------------------------

def test_empty_workflows_without_the_switch_still_WARNS(chela_dir, monkeypatch):
    """The regression test: the incident lesson stays enforced. Making the switch also
    silence the accidental-empty case turns this RED."""
    findings = _doctor(monkeypatch, workflows=[], off=False)
    assert _levels(findings, "Work dispatcher: OFF") == [doctor.WARN]
    assert _levels(findings, "Run reconciliation: OFF") == [doctor.WARN]
    assert not [f for f in findings if f.level == doctor.INFO]
    assert not [f for f in findings if "by choice" in f.title]


def test_the_switch_is_INFO_not_a_warning(chela_dir, monkeypatch):
    findings = _doctor(monkeypatch, workflows=[], off=True)
    by_choice = [f for f in findings if f.title == "Work dispatcher: OFF (by choice, CHELA_DISPATCH=off)"]
    assert [f.level for f in by_choice] == [doctor.INFO]
    assert "unset CHELA_DISPATCH" in by_choice[0].detail
    assert not [f for f in findings if f.level == doctor.WARN and "dispatcher" in f.title.lower()]
    assert not [f for f in findings if f.level == doctor.WARN and "reconciliation" in f.title.lower()]
    assert not [f for f in findings if f.level == doctor.ERROR]


def test_the_switch_with_workflows_is_an_ERROR_and_says_the_switch_wins(chela_dir, monkeypatch, tmp_path):
    wf = tmp_path / "repo" / "WORKFLOW.md"
    findings = _doctor(monkeypatch, workflows=[wf], off=True)
    errors = [f for f in findings if f.level == doctor.ERROR]
    assert len(errors) == 1
    assert "CHELA_DISPATCH=off AND CHELA_DISPATCH_WORKFLOWS names 1 workflow(s)" in errors[0].title
    assert "contradiction" in errors[0].title
    assert "Dispatch stays OFF: the switch wins" in errors[0].detail
    assert str(wf) in errors[0].detail
    # still reported as off by choice, never ON
    assert _levels(findings, "Work dispatcher: OFF (by choice") == [doctor.INFO]
    assert not _levels(findings, "Work dispatcher: ON")


def test_the_contradiction_is_read_from_the_RUNNING_daemon(chela_dir, monkeypatch, tmp_path):
    """A daemon that booted with the contradiction published it; doctor reads that, not
    its own (now clean) shell config."""
    wf = tmp_path / "repo" / "WORKFLOW.md"
    _set(monkeypatch, workflows=[wf], off=True)
    capabilities.publish(capabilities.effective())
    _set(monkeypatch, workflows=[], off=True)
    findings = doctor.audit(doctor.fact("daemon.capabilities"))
    assert any("contradiction" in f.title for f in findings if f.level == doctor.ERROR)


def test_info_renders_with_its_own_symbol():
    f = doctor.Finding(doctor.INFO, "Work dispatcher: OFF (by choice, CHELA_DISPATCH=off)")
    assert f.render() == "i Work dispatcher: OFF (by choice, CHELA_DISPATCH=off)"
    assert doctor.INFO == "info"


# --- the daemon's startup announcement ----------------------------------------------------

def _announce(monkeypatch, caplog, *, workflows, off):
    _set(monkeypatch, workflows=workflows, off=off)
    log = logging.getLogger("test.cmx388")
    with caplog.at_level(logging.DEBUG, logger="test.cmx388"):
        capabilities.announce(capabilities.effective(), log)
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "test.cmx388"]


def test_startup_announces_the_switch_at_INFO(monkeypatch, caplog):
    recs = _announce(monkeypatch, caplog, workflows=[], off=True)
    assert (logging.INFO, "dispatch: OFF (by choice, CHELA_DISPATCH=off)") in recs
    assert not [m for lvl, m in recs if lvl >= logging.WARNING and ("dispatch" in m.lower() or "reconcil" in m.lower())]


def test_startup_without_the_switch_still_WARNS(monkeypatch, caplog):
    recs = _announce(monkeypatch, caplog, workflows=[], off=False)
    assert any(lvl == logging.WARNING and m.startswith("Work dispatcher: OFF") for lvl, m in recs)
    assert not [m for _, m in recs if "by choice" in m]


def test_startup_with_the_contradiction_logs_an_ERROR_once(monkeypatch, caplog, tmp_path):
    recs = _announce(monkeypatch, caplog, workflows=[tmp_path / "W.md"], off=True)
    errors = [m for lvl, m in recs if lvl == logging.ERROR]
    assert len(errors) == 1
    assert "Dispatch stays OFF (the switch wins)" in errors[0]
    assert (logging.INFO, "dispatch: OFF (by choice, CHELA_DISPATCH=off)") in recs


def test_the_capability_is_off_and_published_with_the_choice(monkeypatch, tmp_path):
    wf = tmp_path / "W.md"
    _set(monkeypatch, workflows=[wf], off=True)
    caps = {c.key: c.as_dict() for c in capabilities.effective()}
    assert caps["dispatch"]["on"] is False
    assert caps["dispatch"]["off_by_choice"] is True
    assert caps["dispatch"]["warn_when_off"] is False
    assert caps["dispatch"]["configured_workflows"] == [str(wf)]
    assert caps["dispatch"]["workflows"] == []
    assert caps["reconcile"]["off_by_choice"] is True
    assert "configured_workflows" not in caps["reconcile"]
    _set(monkeypatch, workflows=[wf], off=False)
    caps = {c.key: c.as_dict() for c in capabilities.effective()}
    assert caps["dispatch"]["on"] is True
    assert caps["dispatch"]["off_by_choice"] is False
    assert caps["dispatch"]["configured_workflows"] == []


# --- config: the knob, its precedence, and that the switch wins ---------------------------

@pytest.mark.parametrize("env,stored,enabled", [
    (None, None, True),          # default: dispatch on (today)
    ("off", None, False),
    ("OFF", None, False),
    ("false", None, False),
    ("0", None, False),
    ("no", None, False),
    ("on", None, True),
    ("true", None, True),
    ("", None, True),            # empty env = unset
    (None, "off", False),        # the dashboard's config.json
    ("on", "off", True),         # env beats config.json
    ("off", "on", False),
])
def test_dispatch_switch_precedence(monkeypatch, env, stored, enabled):
    from chela import userconfig
    if env is None:
        monkeypatch.delenv("CHELA_DISPATCH", raising=False)
    else:
        monkeypatch.setenv("CHELA_DISPATCH", env)
    monkeypatch.setattr(userconfig, "get", lambda key, default=None: stored if key == "dispatch_enabled" else default)
    assert config.dispatch_value("dispatch_enabled") is enabled


def test_the_knob_is_registered_restart_required():
    row = next(k for k in config.dispatch_snapshot() if k["key"] == "dispatch_enabled")
    assert row["env"] == "CHELA_DISPATCH"
    assert row["kind"] == "bool"
    assert row["restart_required"] is True


def _latched(tmp_path, **env) -> dict:
    """What a fresh process latches at import — the module constants the daemon runs on."""
    code = ("import json; from chela import config; print(json.dumps({"
            "'off': config.DISPATCH_OFF_BY_CHOICE, "
            "'workflows': [str(p) for p in config.DISPATCH_WORKFLOWS], "
            "'configured': [str(p) for p in config.DISPATCH_WORKFLOWS_CONFIGURED]}))")
    base = {k: v for k, v in os.environ.items() if not k.startswith("CHELA_")}
    base.update(CHELA_DIR=str(tmp_path / "state"), CHELA_ENV_FILE="", **env)
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=base, capture_output=True,
                         text=True, timeout=60, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_switch_wins_over_configured_workflows_at_import(tmp_path):
    wf = str((tmp_path / "W.md").resolve())
    assert _latched(tmp_path, CHELA_DISPATCH="off", CHELA_DISPATCH_WORKFLOWS=wf) == {
        "off": True, "workflows": [], "configured": [wf]}
    assert _latched(tmp_path, CHELA_DISPATCH_WORKFLOWS=wf) == {
        "off": False, "workflows": [wf], "configured": [wf]}
    assert _latched(tmp_path) == {"off": False, "workflows": [], "configured": []}


# --- the dashboard's Settings row ---------------------------------------------------------

def _dispatch_row(monkeypatch, off: bool) -> dict:
    monkeypatch.setattr(dash.config, "DISPATCH_OFF_BY_CHOICE", off)
    data = dash.app.test_client().get("/api/settings").get_json()
    return next(it for sec in data["sections"] for it in sec["items"] if it.get("key") == "dispatch")


def test_settings_row_reads_off_by_choice_with_the_one_line(monkeypatch):
    row = _dispatch_row(monkeypatch, True)
    assert row["label"] == "Work dispatcher"
    assert row["on"] is False
    assert row["state"] == "Off by choice"
    assert row["detail"] == "Dispatch is off by choice: unset `CHELA_DISPATCH` to enable (docs/CONFIG.md)"


def test_settings_row_without_the_switch_is_not_off_by_choice(monkeypatch):
    row = _dispatch_row(monkeypatch, False)
    assert row["label"] == "Work dispatcher"
    assert row["state"] != "Off by choice"
    assert "by choice" not in row["detail"]


def test_a_hold_does_not_override_off_by_choice(monkeypatch):
    """Off by choice outranks a leftover hold file: the row is a decision, not a held queue."""
    class _Held:
        def summary(self):
            return "held by someone"

        def as_dict(self):
            return {"summary": "held"}
    monkeypatch.setattr(dash.hold, "active", lambda: _Held())
    row = _dispatch_row(monkeypatch, True)
    assert row["state"] == "Off by choice"
