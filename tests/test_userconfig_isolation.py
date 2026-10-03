"""CMX-7 — a dashboard setting written in one test must not leak into the next.

``userconfig._PATH`` is latched from ``config.CHELA_DIR`` at import, i.e. the session-wide
sandbox every test on an xdist worker shares. ``tests/test_share_requests.py`` turns
``share_typing`` on through ``userconfig.set_``; when it ran before
``tests/test_share_typing_gate.py`` on one worker, ``test_share_typing_defaults_off`` saw
it — three judge baselines in a row were red on this, and every other run was green.
conftest's ``_isolate_chela_dir`` now points ``userconfig`` at the per-test dir too. These
two tests run in file order: the first writes, the second must not see it.
"""
from __future__ import annotations

from chela import config, userconfig


def test_userconfig_follows_the_per_test_chela_dir():
    assert userconfig._PATH == config.CHELA_DIR / "config.json"


def test_a_setting_written_by_one_test_is_set_here_first(monkeypatch):
    monkeypatch.delenv("CHELA_SHARE_TYPING", raising=False)
    userconfig.set_(config.SHARE_TYPING_KEY, True)
    assert config.share_typing_enabled() is True


def test_is_not_seen_by_the_next_test(monkeypatch):
    monkeypatch.delenv("CHELA_SHARE_TYPING", raising=False)
    assert userconfig.get(config.SHARE_TYPING_KEY) is None
    assert config.share_typing_setting() == (False, "default")
