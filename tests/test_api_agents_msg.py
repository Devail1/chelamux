"""``/api/agents/msg`` (CMX-482): the dashboard's "message agent" box is the USER typing.

So its text arrives as a user turn (bucket 5 in ``docs/TMUX_TOUCHPOINTS.md``): a
``/command`` through ``send_tmux`` raw, anything else through ``deliver_prompt`` (which
refuses to append to an unsent draft, CMX-117). It no longer goes through
``send_message`` and its old silent tmux fallback — that is the agent-message path, and
since CMX-482 it never types into a pane at all.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from chela import messenger
from chela.dashboard import app as dash


def _post(message: str, *, agent: str = "cmx-9", draft: str | None = None,
          resolved: str | None = "@4"):
    calls: dict[str, list] = {"prompt": [], "tmux": [], "message": []}

    def deliver_prompt(wid, text):
        if draft:
            raise messenger.PendingDraft(draft)
        calls["prompt"].append((wid, text))
        return True

    with patch.object(messenger, "resolve_window", return_value=resolved), \
            patch.object(messenger, "deliver_prompt", side_effect=deliver_prompt), \
            patch.object(messenger, "send_tmux",
                         side_effect=lambda wid, text, **kw: calls["tmux"].append((wid, text))
                         or True), \
            patch.object(messenger, "send_message",
                         side_effect=lambda *a, **kw: calls["message"].append(a)
                         or messenger.DELIVERED):
        resp = dash.app.test_client().post("/api/agents/msg",
                                           json={"agent": agent, "message": message})
    return resp, calls


def test_prose_goes_in_as_the_users_own_prompt_not_an_agent_message():
    resp, calls = _post("please rebase")
    assert resp.status_code == 200
    assert resp.get_json() == {"sent": True, "agent": "cmx-9"}
    assert calls == {"prompt": [("@4", "please rebase")], "tmux": [], "message": []}


def test_a_slash_command_goes_in_raw():
    resp, calls = _post("/compact")
    assert resp.get_json() == {"sent": True, "agent": "cmx-9"}
    assert calls == {"prompt": [], "tmux": [("@4", "/compact")], "message": []}


def test_an_unsent_draft_is_refused_not_merged():
    resp, calls = _post("please rebase", draft="half a thought")
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["sent"] is False and "draft" in body["error"]
    assert calls == {"prompt": [], "tmux": [], "message": []}


def test_an_unknown_agent_is_a_404_and_nothing_is_sent():
    resp, calls = _post("hi", resolved=None)
    assert resp.status_code == 404
    assert calls == {"prompt": [], "tmux": [], "message": []}


@pytest.mark.parametrize("outcome, sent", [(messenger.DELIVERED, True),
                                           (messenger.UNREACHABLE, False),
                                           ("held", False)])
def test_trigger_reports_sent_only_for_a_delivered_outcome(outcome, sent):
    with patch("chela.scheduler.list_tasks", return_value=[]), \
            patch.object(messenger, "send_message", return_value=outcome) as send:
        resp = dash.app.test_client().post("/api/agents/trigger", json={"agent": "cmx-9"})
    assert resp.get_json()["sent"] is sent
    send.assert_called_once_with("dashboard", "cmx-9", "Please run your work cycle now.")


def test_broadcast_reports_a_bool_per_agent_never_a_truthy_outcome_string():
    results = {"a": messenger.DELIVERED, "b": messenger.UNREACHABLE, "c": "held"}
    with patch.object(messenger, "broadcast", return_value=results) as sent:
        resp = dash.app.test_client().post("/api/agents/broadcast", json={"message": "hi"})
    assert resp.get_json() == {"a": True, "b": False, "c": False}
    sent.assert_called_once_with("dashboard", "hi", "normal")
