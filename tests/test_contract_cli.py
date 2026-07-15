"""⚙️⚖️ CONTRACT-AS-CODE — the CLI WIRING, driven end-to-end through ``main()``.

The gate itself (``chela.contract.merge`` / ``escalate``) is proven in ``test_contract.py``.
These tests prove the OTHER half: that typing ``chela merge`` / ``chela escalate`` actually
reaches that gate. They drive the real argparse dispatch in ``main.main()`` so the call-site
is under test — corrupt ``elif args.command == "merge": cmd_merge(args)`` to ``… : pass`` and
``contract.merge`` is never called, turning these red. Every other CLI command has this kind
of smoke test; the two contract commands must too, or a reverted dispatch merges silently.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from chela import main


def _drive(argv: list[str]):
    """Run ``main.main()`` with ``argv`` as the process args (argparse reads ``sys.argv``)."""
    with patch.object(sys, "argv", ["chela", *argv]):
        main.main()


def test_chela_merge_reaches_the_contract_gate():
    """``chela merge cmx-84`` must invoke ``contract.merge`` — the dispatch call-site is the
    guard here. Mutate it to ``pass`` and this fails: the gate is never consulted."""
    fake = {"ok": True, "task_id": "cmx-84", "base": "dev",
            "merge_commit_sha": "deadbeef1234", "event_seq": 7}
    with patch.object(main.contract, "merge", return_value=fake) as m:
        _drive(["merge", "cmx-84"])
    m.assert_called_once()
    assert m.call_args.args[0] == "cmx-84"      # the run the human asked to merge


def test_chela_merge_passes_the_reason_through():
    fake = {"ok": True, "task_id": "cmx-84", "base": "dev",
            "merge_commit_sha": "x", "event_seq": 1}
    with patch.object(main.contract, "merge", return_value=fake) as m:
        _drive(["merge", "cmx-84", "--reason", "dogfood"])
    m.assert_called_once()
    assert m.call_args.kwargs.get("reason") == "dogfood"


def test_chela_merge_refusal_exits_nonzero():
    """A refused merge must not exit 0 — a silent success on a refusal is the whole bug class
    the contract exists to prevent."""
    fake = {"ok": False, "task_id": "cmx-84", "tier": "escalate", "error": "base is not dev"}
    with patch.object(main.contract, "merge", return_value=fake):
        with pytest.raises(SystemExit) as ei:
            _drive(["merge", "cmx-84"])
    assert ei.value.code == 1


def test_chela_escalate_reaches_the_contract_path():
    """``chela escalate "<summary>"`` must invoke ``contract.escalate`` — same dispatch guard.
    Mutate the call-site to ``pass`` and this fails: nothing is recorded or pushed."""
    fake = {"ok": True, "kind": "decision", "run": None, "event_seq": 3, "notified": False}
    with patch.object(main.contract, "escalate", return_value=fake) as e:
        _drive(["escalate", "should we ship X?"])
    e.assert_called_once()
    assert e.call_args.args[0] == "should we ship X?"


def test_chela_escalate_carries_kind_and_run_through():
    fake = {"ok": True, "kind": "merge", "run": "cmx-84", "event_seq": 4, "notified": True}
    with patch.object(main.contract, "escalate", return_value=fake) as e:
        _drive(["escalate", "conflict on the base", "--kind", "merge", "--run", "cmx-84"])
    e.assert_called_once()
    assert e.call_args.kwargs.get("kind") == "merge"
    assert e.call_args.kwargs.get("run") == "cmx-84"


def test_chela_escalate_failure_exits_nonzero():
    fake = {"ok": False, "error": "an escalation with no summary is not an escalation"}
    with patch.object(main.contract, "escalate", return_value=fake):
        with pytest.raises(SystemExit) as ei:
            _drive(["escalate", "x"])
    assert ei.value.code == 2
