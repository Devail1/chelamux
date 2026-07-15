"""🎭 THE PERSONA LAYER — the registry is a DECLARATION, so these tests corrupt it and the
orchestrator prompt and watch them go red.

Two things must stay true for the persona layer to be more than decoration:

* the **registry is complete** — all three personas (judge · critic · orchestrator) are
  declared, each with a non-empty trigger / mode / action-surface / prompt-source. Drop a
  persona or blank a field and the completeness test must fail. (Corrupting the registry is
  the mutation.)
* the **orchestrator prompt is load-bearing, not a stub** — it encodes the invariants the
  contract turns on: the Autonomous / Escalate / Never taxonomy, the attended-autonomous /
  act-then-log rule (act within the standing-auth merge envelope, escalate the rest — NOT
  confirm-each), and the gated action surface (`chela merge`, never raw `gh pr merge`). Delete
  any one of those from ``orchestrator.md`` and the contract test must fail.
"""
from __future__ import annotations

import pytest

from chela import dispatcher, judge, personas
from chela.dashboard import app as dash

REQUIRED_KEYS = ("judge", "critic", "orchestrator")

# The declaration fields that MUST be non-empty for a persona to be usefully declared. These
# are exactly the fields the dashboard panel renders and the layer promises to carry.
NONEMPTY_FIELDS = ("key", "title", "trigger", "mode", "action_surface", "prompt_source", "summary")


def test_all_three_personas_are_declared():
    keys = [p.key for p in personas.registry()]
    # Exactly the three, no more, no fewer — and PERSONA_KEYS agrees with the registry.
    assert keys == list(REQUIRED_KEYS)
    assert tuple(keys) == personas.PERSONA_KEYS


@pytest.mark.parametrize("persona", personas.registry(), ids=lambda p: p.key)
def test_every_persona_field_is_non_empty(persona):
    # Blank any declared field (drop a trigger, empty an action-surface) and this goes red —
    # a persona declared with a hollow field is not declared.
    for f in NONEMPTY_FIELDS:
        val = getattr(persona, f)
        assert isinstance(val, str) and val.strip(), f"{persona.key}.{f} is empty"
    # docs is a tuple of pointers; it may be empty in principle, but every persona here cites
    # at least the pattern doc, so assert the shape (not a magic count).
    assert isinstance(persona.docs, tuple)


def test_registry_is_rebuilt_fresh_so_enabled_tracks_the_env(monkeypatch):
    # `enabled` is a live read of the config flag, not a value frozen at import — flip the
    # judge flag and a fresh registry() reflects it. (Corrupt registry() to return a cached
    # list and this goes red.)
    monkeypatch.setenv("CHELA_JUDGE", "false")
    judge = personas.find("judge")
    assert judge is not None and judge.enabled is False
    monkeypatch.setenv("CHELA_JUDGE", "true")
    assert personas.find("judge").enabled is True


def test_orchestrator_is_declared_but_dormant():
    # The whole point of this task's boundary: the orchestrator is EMBEDDED (declared) but NOT
    # launched. It must read as dormant, and its prompt source is the in-repo prompt file.
    orch = personas.find("orchestrator")
    assert orch is not None
    assert orch.mode == "attended-autonomous"
    assert orch.enabled is False
    assert orch.prompt_source == "chela/personas/orchestrator.md"


def test_as_dict_is_json_shaped():
    d = personas.find("judge").as_dict()
    assert set(d) >= {"key", "title", "trigger", "mode", "action_surface",
                      "prompt_source", "summary", "enabled", "docs"}
    assert isinstance(d["docs"], list)
    assert isinstance(d["enabled"], bool)


# --- the orchestrator prompt actually encodes the contract -------------------------------

def _orchestrator_prompt() -> str:
    assert personas.ORCHESTRATOR_PROMPT.exists(), "orchestrator.md is missing"
    return personas.ORCHESTRATOR_PROMPT.read_text(encoding="utf-8")


def test_orchestrator_prompt_encodes_the_three_tier_taxonomy():
    # Autonomous / Escalate / Never — all three tier words must be present. Remove any one
    # (hollow the taxonomy) → red.
    text = _orchestrator_prompt()
    for tier in ("Autonomous", "Escalate", "Never"):
        assert tier in text, f"orchestrator.md dropped the '{tier}' tier"


def test_orchestrator_prompt_encodes_attended_autonomous_act_then_log():
    # The mode is attended-autonomous with act-then-log provenance: it ACTS within the
    # standing-auth merge envelope and ESCALATES the rest — explicitly NOT confirm-each.
    text = _orchestrator_prompt()
    assert "attended-autonomous" in text
    assert "act-then-log" in text or "act, then log" in text
    assert "standing-auth" in text.lower() or "standing auth" in text.lower()
    # names the fact it is NOT (a downgrade to confirm-each), so the invariant is explicit
    assert "confirm-each" in text


def test_orchestrator_prompt_encodes_the_gated_action_surface():
    # Acts through gated `chela` commands, never raw gh/pkill/shell. Remove the no-raw-gh rule
    # or the gated `chela merge` and this goes red.
    text = _orchestrator_prompt()
    assert "chela merge" in text
    assert "gh pr merge" in text          # named as the thing it must NEVER reach
    assert "chela escalate" in text


# --- the WIRING: /api/personas is the production surface that feeds the panel -------------
#
# The tests above prove the registry is a complete, correct DECLARATION — pure data. But the
# dashboard panel never touches the registry directly; it fetches /api/personas. If that route
# stops serializing the registry (an empty loop, a wrong source), the panel goes blank and
# every test above still passes — the exact gap a corruption of ``api_personas`` opens. These
# import the real Flask app and drive the route, so the serialization loop is under test.

@pytest.fixture
def client():
    return dash.app.test_client()


def test_api_personas_route_serializes_the_registry(client):
    """🔴 WIRING — the route's payload IS the registry, in order, one entry per persona.

    Corrupt ``api_personas`` to iterate ``[]`` (or any source that is not the registry) and
    this goes red: the served keys no longer equal the declared keys.
    """
    res = client.get("/api/personas")
    assert res.status_code == 200
    served = [p["key"] for p in res.get_json()["personas"]]
    # the SAME three the registry declares, in registry order — not empty, not a subset
    assert served == list(personas.PERSONA_KEYS)
    assert served == [p.key for p in personas.registry()]
    # …and each served entry carries the declared fields the panel renders — a route that
    # emitted three empty dicts would pass a bare count but fail this.
    for entry in res.get_json()["personas"]:
        for field in ("title", "trigger", "mode", "action_surface", "prompt_source"):
            assert entry.get(field), f"{entry.get('key')}.{field} missing from /api/personas"


def test_api_personas_stamps_the_judge_live_status(client, monkeypatch):
    """🔴 WIRING — the route stamps the judge's live 'reviewing cmx-N' from a running run.

    ``_judge_live_status`` reads ``judge_state='running'`` off ``dispatcher.list_runs`` and the
    route grafts it onto the judge entry. Sever either half and the note vanishes.
    """
    monkeypatch.setattr(
        dispatcher, "list_runs",
        lambda *a, **k: [{"judge_state": judge.J_RUNNING, "task_number": 3}],
    )
    payload = client.get("/api/personas").get_json()
    judge_entry = next(p for p in payload["personas"] if p["key"] == "judge")
    assert judge_entry.get("status") == "reviewing cmx-3"
    # …and only the judge is stamped — the note is judge-specific, not sprayed across the layer.
    others = [p for p in payload["personas"] if p["key"] != "judge"]
    assert all("status" not in p or not p["status"] for p in others)
