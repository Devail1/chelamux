"""🔴 GUARDS for CMX-195's two `chela/main.py` WIRING sites.

`chela/restore.py`, `chela/roster.py` and `chela/inbox.py` are all thoroughly tested as
libraries — and the judge proved every one of those tests stays green when the CLI half is
neutralised. A library nobody calls, and an exit code nobody checks, protect nothing:

* `chela restore` must exit NONZERO while any row is MANUAL. That exit code is the whole
  interface to a restart procedure ("run restore, then check the code before declaring the
  box recovered"). Neutralised, the command prints an orphan list and reports SUCCESS.
* `chela watch` must WARN when no session identity resolved. Measured live 2026-07-30:
  `orchestrator_session` was `null`, which disarmed CMX-82's self-heal before it ran and
  looked identical to a healthy registration. `inbox.register` returning the fact is only
  half the fix — nothing surfaced it to the operator.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chela import main
from chela import restore as restore_mod

NOW = "9001-1784099999"
OLD = "786-1784045825"


def _verdict(verdict, store="session-ids", wid="@1"):
    return restore_mod.Verdict(
        store=store, wid=wid, label="l", stamped_epoch=OLD,
        session_id="sid-1", cwd="/home/x", verdict=verdict, new_wid="@9",
    )


@pytest.fixture()
def restore_env(monkeypatch):
    """Stub every read `cmd_restore` makes, so only the exit-code wiring is under test."""
    from chela import dispatcher, epoch, inbox, sessionids
    from chela.telegram import bindings as bindings_mod

    monkeypatch.setattr(epoch, "current", lambda: NOW)
    monkeypatch.setattr(epoch, "describe", lambda e: str(e))
    monkeypatch.setattr(inbox, "load", lambda: {"watches": {}})
    monkeypatch.setattr(dispatcher, "list_runs", lambda *a, **k: [])
    monkeypatch.setattr(sessionids, "entries", lambda: {})
    monkeypatch.setattr(restore_mod, "scan_all", lambda *a, **k: [])
    monkeypatch.setattr(bindings_mod.BindingRegistry, "load",
                        classmethod(lambda cls, *a, **k: bindings_mod.BindingRegistry("1")))
    return monkeypatch


def test_restore_exits_nonzero_while_any_row_is_MANUAL(restore_env, capsys):
    restore_env.setattr(restore_mod, "plan", lambda *a, **k: [_verdict("MANUAL")])

    with pytest.raises(SystemExit) as exc:
        main.cmd_restore(SimpleNamespace(apply=False))

    assert exc.value.code == 1, (
        "a MANUAL row means an agent is still orphaned — restore must fail loudly so a "
        "restart procedure cannot read as recovered"
    )
    assert "MANUAL" in capsys.readouterr().out


def test_restore_still_exits_nonzero_after_apply_archived_the_manual_row(restore_env, capsys):
    """--apply archives the row, but the AGENT behind it is still gone. Still nonzero."""
    v = _verdict("MANUAL")
    restore_env.setattr(restore_mod, "plan", lambda *a, **k: [v])
    restore_env.setattr(restore_mod, "apply",
                        lambda *a, **k: {"revived": [], "archived": [v], "skipped": []})

    with pytest.raises(SystemExit) as exc:
        main.cmd_restore(SimpleNamespace(apply=True))

    assert exc.value.code == 1
    assert "ARCHIVED" in capsys.readouterr().out


def test_restore_exits_ZERO_when_every_row_is_REVIVABLE(restore_env, capsys):
    """The counterweight: a guard that only ever demands failure would be satisfied by
    `sys.exit(1)` unconditionally, which would make the command useless."""
    restore_env.setattr(restore_mod, "plan", lambda *a, **k: [_verdict("REVIVABLE")])

    main.cmd_restore(SimpleNamespace(apply=False))     # must NOT raise

    assert "REVIVABLE" in capsys.readouterr().out


def test_restore_exits_ZERO_when_nothing_is_orphaned(restore_env, capsys):
    restore_env.setattr(restore_mod, "plan", lambda *a, **k: [])

    main.cmd_restore(SimpleNamespace(apply=False))     # must NOT raise

    assert "nothing orphaned" in capsys.readouterr().out


# --- objective 5's operator-visible half ----------------------------------------------

def _watch_env(monkeypatch, session):
    from chela import inbox, orchestrator
    monkeypatch.setattr(orchestrator, "self_wid", lambda: "@0")
    monkeypatch.setattr(main, "_resolve_wid", lambda w: "@7")
    monkeypatch.setattr(inbox, "watch",
                        lambda *a, **k: {"ok": True, "wid": "@7", "note": "",
                                         "orchestrator": "@0", "epoch": NOW,
                                         "session": session})


def test_watch_warns_when_no_session_identity_resolved(monkeypatch, capsys):
    _watch_env(monkeypatch, None)

    main.cmd_watch(SimpleNamespace(wid="@7", note=""))

    err = capsys.readouterr().err
    assert "could not resolve a session identity" in err, (
        "a registration with no identity is indistinguishable from a healthy one once it "
        "is on disk — CMX-82's self-heal is disarmed and nothing says so"
    )
    assert "@0" in err, "the warning must name the window whose identity failed"


def test_watch_stays_QUIET_when_an_identity_did_resolve(monkeypatch, capsys):
    """The counterweight: warning unconditionally would train the operator to ignore it."""
    _watch_env(monkeypatch, "sid-abc")

    main.cmd_watch(SimpleNamespace(wid="@7", note=""))

    assert "could not resolve a session identity" not in capsys.readouterr().err
