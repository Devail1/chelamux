"""CMX-387: the two boot flags that hide the dashboard's Share and Telegram controls.

``sharingConfigured`` is "a relay is set" (the same test ``POST /share`` refuses on).
``telegramConfigured`` is "the bridge's token AND chat id resolve", read the way the bridge
reads them (env, then ``chela.env``, then ``secrets.env``), never whether the bridge process
is up this second: a restart must not flicker the controls away."""
from __future__ import annotations

import pytest

from chela.dashboard import app as dash


@pytest.mark.parametrize("relay,expected", [
    ("", False),
    ("wss://relay.example", True),
])
def test_sharing_is_configured_exactly_when_a_relay_is_set(monkeypatch, relay, expected):
    monkeypatch.setattr(dash.config, "COLLAB_RELAY", relay)
    assert dash._sharing_configured() is expected


@pytest.fixture
def tg(monkeypatch, tmp_path):
    """No Telegram var in the env, no chela.env, an empty $CHELA_DIR for secrets.env."""
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CHELA_ENV_FILE", "")
    monkeypatch.setattr(dash.config, "CHELA_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize("env,expected", [
    ({}, False),
    ({"TELEGRAM_BOT_TOKEN": "123:abc"}, False),
    ({"TELEGRAM_CHAT_ID": "-100"}, False),
    ({"TELEGRAM_BOT_TOKEN": "   ", "TELEGRAM_CHAT_ID": "-100"}, False),
    ({"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "  "}, False),
    ({"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-100"}, True),
])
def test_telegram_needs_both_the_token_and_the_chat_id_from_the_env(monkeypatch, tg, env, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert dash._telegram_configured() is expected


def test_telegram_read_from_secrets_env_when_the_dashboard_env_lacks_it(tg):
    """The dashboard process does not carry the bridge's credentials; secrets.env does."""
    (tg / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=-100\n")
    assert dash._telegram_configured() is True


def test_telegram_read_from_chela_env(monkeypatch, tg):
    f = tg / "custom.env"
    f.write_text("TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=-100\n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(f))
    assert dash._telegram_configured() is True


def test_telegram_is_configuration_not_liveness(monkeypatch, tg):
    """A running bridge without config is not configured; a configured bridge mid-restart is."""
    monkeypatch.setattr(dash, "_telegram_bridge_running", lambda: True)
    assert dash._telegram_configured() is False
    (tg / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=-100\n")
    monkeypatch.setattr(dash, "_telegram_bridge_running", lambda: False)
    assert dash._telegram_configured() is True


def test_an_unreadable_bridge_env_fails_closed(monkeypatch, tg, caplog):
    """Any error resolving the bridge env reads as NOT configured (never a crash of the page)."""
    import chela.telegram.senders as senders

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")

    def boom():
        raise RuntimeError("unreadable")

    monkeypatch.setattr(senders, "bridge_env", boom)
    with caplog.at_level("WARNING", logger="dashboard"):
        assert dash._telegram_configured() is False
    assert "could not read the Telegram bridge env" in caplog.text


def test_the_page_renders_with_both_services_off(monkeypatch, tg):
    """End to end through index(): the real helpers, nothing configured."""
    import json
    import re

    monkeypatch.setattr(dash.config, "COLLAB_RELAY", "")
    html = dash.app.test_client().get("/").get_data(as_text=True)
    block = re.search(r'<script type="application/json" id="chela-boot">(.*?)</script>', html, re.S)
    assert block is not None
    boot = json.loads(block.group(1))
    assert (boot["sharingConfigured"], boot["telegramConfigured"]) == (False, False)
