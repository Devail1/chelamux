"""CMX-164: "Agents at once" (per workflow) and "Judges at once" Settings rows.

Read per tick, so a change applies with no WORKFLOW.md edit and no restart.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from chela import concurrency, dispatcher, event_log, userconfig
from tests.test_dispatcher_claim_race import repo, spawns  # noqa: F401  (fixtures)

THIRD = "- [ ] third item\n"


@pytest.fixture(autouse=True)
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(userconfig, "_PATH", tmp_path / "config.json")


def _running(work) -> list[str]:
    with dispatcher._db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT title FROM runs WHERE workflow_path=? AND status='running'",
            (str(work / "WORKFLOW.md"),))]


# --- agents at once: the dispatcher claims up to the override -----------------

def test_override_raises_the_claim_limit_over_the_file_value(repo, spawns):  # noqa: F811
    assert concurrency.set_agents(repo / "WORKFLOW.md", 2, "t") is None
    dispatcher.tick(repo / "WORKFLOW.md")
    assert spawns.titles == ["first item", "second item"]      # WORKFLOW.md says max: 1


def test_cleared_override_falls_back_to_the_file_value(repo, spawns):  # noqa: F811
    concurrency.set_agents(repo / "WORKFLOW.md", 2, "t")
    concurrency.set_agents(repo / "WORKFLOW.md", "", "t")
    dispatcher.tick(repo / "WORKFLOW.md")
    assert spawns.titles == ["first item"]


def test_override_for_another_workflow_does_not_change_this_one(repo, spawns, tmp_path):  # noqa: F811
    concurrency.set_agents(tmp_path / "other" / "WORKFLOW.md", 3, "t")
    dispatcher.tick(repo / "WORKFLOW.md")
    assert spawns.titles == ["first item"]


def test_lowering_below_the_running_count_stops_nothing_and_claims_nothing(repo, spawns):  # noqa: F811
    concurrency.set_agents(repo / "WORKFLOW.md", 2, "t")
    dispatcher.tick(repo / "WORKFLOW.md")
    (repo / "TODO.md").write_text((repo / "TODO.md").read_text() + THIRD)
    concurrency.set_agents(repo / "WORKFLOW.md", 1, "t")
    dispatcher.tick(repo / "WORKFLOW.md")
    assert spawns.titles == ["first item", "second item"]       # no third claim
    assert sorted(_running(repo)) == ["first item", "second item"]   # nobody stopped


class _Wf:
    def __init__(self, path, file_max):
        self.path, self._max = path, file_max

    def get(self, *keys, default=None):
        return self._max if keys == ("concurrency", "max") else default


@pytest.mark.parametrize("file_max, override, expected", [
    (3, None, 3), (3, 5, 5), (None, None, 1), (0, None, 1), (None, 2, 2),
])
def test_agents_at_once_precedence(tmp_path, file_max, override, expected):
    wf = _Wf(tmp_path / "WORKFLOW.md", file_max)
    if override is not None:
        concurrency.set_agents(wf.path, override, "t")
    assert concurrency.agents_at_once(wf) == expected


def test_a_hand_edited_out_of_range_override_is_ignored(tmp_path):
    wf = _Wf(tmp_path / "WORKFLOW.md", 2)
    userconfig.set_(concurrency.AGENTS_KEY, {str(wf.path.resolve()): 99})
    assert concurrency.agent_override(wf.path) is None
    assert concurrency.agents_at_once(wf) == 2
    userconfig.set_(concurrency.AGENTS_KEY, ["not", "a", "map"])
    assert concurrency.agents_at_once(wf) == 2


# --- validation: 1 to 8, integers only ---------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (1, 1), (8, 8), ("4", 4), (" 3 ", 3),
    (0, None), (9, None), (-1, None), ("9", None), ("0", None), ("2.5", None),
    (2.0, None), (True, None), ("x", None), ("", None), (None, None), ("-2", None),
])
def test_parse_bounds_and_types(raw, expected):
    assert concurrency.parse(raw) == expected


@pytest.mark.parametrize("bad", [0, 9, "abc", 2.5, True, "1e1"])
def test_setters_reject_bad_input_and_store_nothing(tmp_path, bad):
    assert concurrency.set_agents(tmp_path / "WORKFLOW.md", bad, "t") is not None
    assert concurrency.set_judges(bad, "t") is not None
    assert userconfig._load() == {}


# --- judges at once: settings > env > default, in a subprocess, explicit env ---

_PROBE = ("from chela import concurrency, userconfig, config; import sys, json; "
          "v = sys.argv[1]; v and userconfig.set_('judge_max_concurrent', json.loads(v)); "
          "print(json.dumps([*concurrency.judges_at_once(), config.judge_max_concurrent()]))")


@pytest.mark.parametrize("stored, env, expected", [
    ("", None, [1, "default", 1]),
    ("", "3", [3, "env", 3]),
    ("5", "3", [5, "settings", 5]),
    ("5", None, [5, "settings", 5]),
    ("", "0", [1, "env", 1]),          # env floored at 1, never 0 (a wedged queue)
    ("", "junk", [1, "default", 1]),
    ("12", "3", [3, "env", 3]),        # out-of-range stored value falls through
])
def test_judges_precedence_table(tmp_path, stored, env, expected):
    child_env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
                 "CHELA_DIR": str(tmp_path / "chela"), "CHELA_ENV_FILE": ""}
    if env is not None:
        child_env["CHELA_JUDGE_MAX_CONCURRENT"] = env
    out = subprocess.run([sys.executable, "-c", _PROBE, stored], env=child_env,
                         capture_output=True, text=True, timeout=60, check=True)
    assert json.loads(out.stdout) == expected


# --- every change is in the event log: who, old -> new -----------------------

def _events():
    return [e for e in event_log.read()["events"] if e["type"] == "settings.concurrency"]


def test_changes_are_logged_with_who_old_and_new(tmp_path):
    wf = tmp_path / "WORKFLOW.md"
    concurrency.set_agents(wf, 3, "alice")
    concurrency.set_agents(wf, "", "bob")
    concurrency.set_judges(2, "carol")
    concurrency.set_judges(4, "dave")
    payloads = [e["payload"] for e in _events()]
    assert payloads == [
        {"setting": "agents_at_once", "workflow": str(wf.resolve()), "old": None, "new": 3,
         "by": "alice"},
        {"setting": "agents_at_once", "workflow": str(wf.resolve()), "old": 3, "new": None,
         "by": "bob"},
        {"setting": "judges_at_once", "workflow": None, "old": None, "new": 2, "by": "carol"},
        {"setting": "judges_at_once", "workflow": None, "old": 2, "new": 4, "by": "dave"},
    ]


# --- the API validates server-side, not just in the UI -----------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from chela.dashboard import app as dash
    wf = tmp_path / "proj" / "WORKFLOW.md"
    wf.parent.mkdir()
    wf.write_text("---\nproject_key: TST\ntracker:\n  kind: markdown\n  path: TODO.md\n"
                  "concurrency:\n  max: 2\n---\nx\n")
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [wf])
    monkeypatch.setattr(dash, "_concurrency_available_bytes", lambda: (4 << 30, "test.slice"))
    return dash.app.test_client(), wf


@pytest.mark.parametrize("payload", [
    {"value": 9}, {"value": 0}, {"value": "x"}, {"value": 1.5}, {"judges": 9}, {"judges": "a"},
])
def test_api_rejects_out_of_range_and_non_integer(client, payload):
    c, wf = client
    if "judges" not in payload:
        payload = {"workflow": str(wf), **payload}
    resp = c.post("/api/config/concurrency", json=payload)
    assert resp.status_code == 400
    assert userconfig._load() == {}


def test_api_rejects_an_unknown_workflow(client, tmp_path):
    c, _ = client
    resp = c.post("/api/config/concurrency",
                  json={"workflow": str(tmp_path / "nope" / "WORKFLOW.md"), "value": 3})
    assert resp.status_code == 400
    assert userconfig._load() == {}


def test_api_sets_reports_and_resets(client):
    c, wf = client
    body = c.get("/api/config/concurrency").get_json()
    assert body["workflows"] == [{"path": str(wf), "name": "proj", "file_value": 2,
                                  "override": None, "effective": 2}]
    assert body["judges"] == {"effective": 1, "source": "default"}
    assert (body["min"], body["max"]) == (1, 8)
    assert body["judge_peak_bytes"] == int(0.77 * 1024 ** 3)
    assert (body["available_bytes"], body["available_source"]) == (4 << 30, "test.slice")

    body = c.post("/api/config/concurrency", json={"workflow": str(wf), "value": 5}).get_json()
    assert body["workflows"][0]["override"] == 5 and body["workflows"][0]["effective"] == 5
    body = c.post("/api/config/concurrency", json={"judges": 3}).get_json()
    assert body["judges"] == {"effective": 3, "source": "settings"}
    assert _events()[-1]["payload"]["by"].startswith("dashboard@")

    body = c.post("/api/config/concurrency", json={"workflow": str(wf), "value": ""}).get_json()
    assert body["workflows"][0]["override"] is None and body["workflows"][0]["effective"] == 2


@pytest.mark.parametrize("bound, mem, expected", [
    ({"unit": "a.slice", "max_bytes": 10, "current_bytes": 4}, None, (6, "a.slice")),
    ({"unit": "a.slice", "max_bytes": 10, "current_bytes": 12}, None, (0, "a.slice")),
    ({"unit": "a.slice", "max_bytes": 10, "current_bytes": None},
     {"used": 3, "total": 9}, (6, "MemAvailable")),
    (None, {"used": 3, "total": 9}, (6, "MemAvailable")),
    (None, {"used": None, "total": None}, (None, "unknown")),
])
def test_available_memory_prefers_the_slice_line_then_meminfo(monkeypatch, bound, mem, expected):
    from chela import memcap
    from chela.dashboard import app as dash
    monkeypatch.setattr(memcap, "live_bound", lambda: bound)
    monkeypatch.setattr(dash.resources, "_sample_mem",
                        lambda: mem if mem is not None else pytest.fail("meminfo read"))
    assert dash._concurrency_available_bytes() == expected
