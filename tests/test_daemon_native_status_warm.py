"""CMX-189: the daemon (`chela run`) reads `chela.sessions` tier 3 too — via
`doctor.check_and_notify`'s `relay.transcripts` fact, same as `chela telegram` — but
CMX-188 only warmed the cache in `cmd_telegram`. `agent_manager`'s status cache is
per-process module state, so a `chela run` process that never calls
`start_background_refresh` itself keeps `session_by_pid`/`cwd_by_pid` empty forever,
silently skipping tier 3 on every doctor sweep this daemon runs.
"""
from __future__ import annotations

from types import SimpleNamespace

from chela import main


def _stub_daemon_startup(monkeypatch) -> None:
    """Stop `cmd_run` after zero loop passes and neuter everything else it touches
    before the loop, so this test proves ONLY that the warm-up call itself is wired —
    not that any particular daemon-loop subsystem runs."""
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)
    monkeypatch.setattr(main.GracefulShutdown, "stopping", True)
    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.capabilities, "effective", lambda: [])
    monkeypatch.setattr(main.capabilities, "announce", lambda caps, log: None)
    monkeypatch.setattr(main.capabilities, "publish", lambda caps, boot_id: None)


def test_cmd_run_warms_the_native_status_cache_itself(monkeypatch):
    """🔴 WIRING (production call-site) — the per-process cache read in
    `chela.sessions.resolve_window`'s tier 3 stays green in isolation no matter what
    `cmd_run` does; only driving `cmd_run` itself proves the warm-up call survives."""
    _stub_daemon_startup(monkeypatch)
    calls = []
    monkeypatch.setattr(main.agent_manager, "start_background_refresh",
                        lambda *a, **kw: calls.append(1))

    main.cmd_run(SimpleNamespace())

    assert calls == [1], (
        "chela run (the daemon) never warmed agent_manager's native-status cache itself "
        "— tier 3 stays cold forever in this process"
    )
