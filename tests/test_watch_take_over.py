"""CMX-138: ``chela watch @N`` from a side session never silently steals a LIVE orchestrator's pin.

Live on 2026-10-14 a side session (@7) started a new agent (@14) and ran ``chela watch @14``:
the pin moved @4 → @7 while @4 was alive, and the decisions inbox, run verdicts and restart
requests went to the wrong window. The explicit form now claims under the same rule as
``--if-unclaimed`` (empty, undeliverable, or already the caller) — otherwise it adds the watch
routed to the CURRENT holder — and taking the role from a live holder is ``--take-over``.

Same harness as :mod:`tests.test_watch_if_unclaimed`: the real CLI in a subprocess against a temp
``CHELA_DIR``, with only the tmux/claude reads faked.
"""

from __future__ import annotations

from tests.test_watch_if_unclaimed import NOW, OLD, _pinned, _run

WINDOWS = {"@A": "orchestrator", "@B": "side", "@C": "worker"}
ALL_LIVE = {"@A": "idle", "@B": "busy", "@C": "busy"}


def _watch(tmp_path, argv, store, statuses=ALL_LIVE):
    return _run(tmp_path, argv, store=store, statuses=statuses, windows=WINDOWS)


def test_watch_window_from_side_session_keeps_the_live_pin(tmp_path):
    """⛔ The 2026-10-14 regression: @B watching @C must leave @A the orchestrator."""
    proc, after = _watch(tmp_path, ["watch", "@C"], _pinned("@A"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "watching @C — events go to the orchestrator @A (live)"
    assert after["orchestrator"] == "@A"
    assert after["orchestrator_epoch"] == NOW
    assert after["orchestrator_name"] == "orchestrator"
    # The watch itself landed — @C's events are queued for the store's orchestrator, @A.
    assert after["watches"]["@C"]["name"] == "worker"
    assert after["queue"] == [], "no orchestrator.moved notice: the pin did not move"
    # ...and no identity warning: nothing was being registered.
    assert "could not resolve a session identity" not in proc.stderr


def test_watch_window_note_is_kept_when_deferring(tmp_path):
    proc, after = _watch(tmp_path, ["watch", "@C", "--note", "fix it"], _pinned("@A"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ('watching @C — note: "fix it" — events go to the '
                                   'orchestrator @A (live)')
    assert after["watches"]["@C"]["note"] == "fix it"


def test_events_for_the_watched_window_are_delivered_to_the_holder(tmp_path, monkeypatch):
    """The routing half: what the inbox delivers to after the watch is the live holder."""
    from chela import inbox
    _, after = _watch(tmp_path, ["watch", "@C"], _pinned("@A"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    assert inbox.orchestrator_wid(after) == "@A"
    state, _ = inbox.address_state(after, ALL_LIVE, NOW)
    assert state == inbox.ADDR_OK


def test_take_over_moves_the_pin_and_fires_the_notice(tmp_path):
    proc, after = _watch(tmp_path, ["watch", "@C", "--take-over"], _pinned("@A"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] == (
        "taking over the orchestrator role from @A (live) — it will no longer receive the "
        "decisions inbox")
    assert "watching @C; events -> @B when idle" in proc.stdout
    assert after["orchestrator"] == "@B"
    assert "@C" in after["watches"]
    moved = [e for e in after["queue"] if e["kind"] == "orchestrator.moved"]
    assert len(moved) == 1
    assert moved[0]["payload"]["reason"] == "taken_over"
    assert (moved[0]["payload"]["old"], moved[0]["payload"]["new"]) == ("@A", "@B")
    # The remedy the notice names must be one that works against a live holder.
    assert "run chela watch --take-over from the session" in moved[0]["summary"]
    assert "ran chela watch --take-over while the previous" in moved[0]["summary"]


def test_bare_take_over_registers_over_a_live_holder(tmp_path):
    proc, after = _watch(tmp_path, ["watch", "--take-over"], _pinned("@A"))
    assert proc.returncode == 0, proc.stderr
    assert "taking over the orchestrator role from @A (live)" in proc.stdout
    assert "registered @B as the orchestrator" in proc.stdout
    assert after["orchestrator"] == "@B"
    assert [e["payload"]["reason"] for e in after["queue"]] == ["taken_over"]


def test_take_over_with_no_live_holder_says_nothing_about_displacing(tmp_path):
    proc, after = _watch(tmp_path, ["watch", "@C", "--take-over"], None)
    assert proc.returncode == 0, proc.stderr
    assert "taking over" not in proc.stdout
    assert after["orchestrator"] == "@B"


def test_dead_holder_is_claimed_as_today(tmp_path):
    """Recovery unchanged: @A runs no claude (ADDR_GONE) → @B claims."""
    proc, after = _watch(tmp_path, ["watch", "@C"], _pinned("@A"),
                         statuses={"@B": "busy", "@C": "busy"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "watching @C; events -> @B when idle"
    assert after["orchestrator"] == "@B"


def test_new_epoch_is_claimed_as_today(tmp_path):
    proc, after = _watch(tmp_path, ["watch", "@C"], _pinned("@A", OLD))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "watching @C; events -> @B when idle"
    assert after["orchestrator"] == "@B"
    assert after["orchestrator_epoch"] == NOW


def test_bare_watch_new_epoch_still_claims(tmp_path):
    proc, after = _watch(tmp_path, ["watch"], _pinned("@A", OLD))
    assert proc.returncode == 0, proc.stderr
    assert "registered @B as the orchestrator" in proc.stdout
    assert after["orchestrator"] == "@B"


def test_holder_re_watching_keeps_claiming(tmp_path):
    """The orchestrator's own `chela watch @N` is the normal dispatch path — unchanged."""
    proc, after = _run(tmp_path, ["watch", "@C"], store=_pinned("@A"), statuses=ALL_LIVE,
                       caller="@A", windows=WINDOWS)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "watching @C; events -> @A when idle"
    assert after["orchestrator"] == "@A"


def test_if_unclaimed_and_take_over_are_refused_together(tmp_path):
    proc, after = _watch(tmp_path, ["watch", "--if-unclaimed", "--take-over"], _pinned("@A"))
    assert proc.returncode == 2
    assert "contradict" in proc.stderr
    assert after["orchestrator"] == "@A"


def test_if_unclaimed_refusal_has_no_take_over_hint(tmp_path):
    """--if-unclaimed output is unchanged: one line (a hook's stdout is not a human)."""
    proc, _ = _watch(tmp_path, ["watch", "--if-unclaimed"], _pinned("@A"))
    assert proc.stdout.strip() == "orchestrator is @A (live) — not claiming"
