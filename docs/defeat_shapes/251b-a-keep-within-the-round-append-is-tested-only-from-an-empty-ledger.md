## 251b. A "keep within the round, reset across rounds" append is tested only from an empty ledger

**Assertion form:** a per-task ledger accumulates across calls, but only within one round:
`old = rec["intervals"] if rec.get("round") == rnd else []`, then append the new entry
(`chela/fleet.py` `_freeze_judges`, the judge freeze ledger). The tests freeze a judge once
and assert the ledger holds one open interval, and a separate test proves another round's
ledger does not shield a stuck judge. All green.

**Mutation that defeats it:** drop the inherit branch — `old = []`. A second freeze in the
same round now wipes the first freeze's closed interval, so the judge loses that frozen
time from its wall and suite deadline.

**Why it looks covered:** every freeze test starts with NO ledger on disk, so "keep the
old intervals" and "start fresh" both append to an empty list and read the same. The
cross-round test reads the ledger through the watchdog, never through a second freeze. This
is shape [[88]] mirrored: there the always-reset was tested only from an already-reset row;
here the inherit is tested only from a row with nothing to inherit.

**Guard form that survives:** seed the ledger with a closed interval first, then freeze
again, and assert both directions in one parametrized test: same round ⇒ the earlier
interval is still there ahead of the new open one; another round ⇒ it is gone. Each row is
the other's negative control (`old = []` reds the first, `old = rec["intervals"]` the
second).

**Found:** CMX-251 rework round 3, PR #775; fixed in `tests/test_fleet_freeze.py`
(`test_a_second_freeze_keeps_the_rounds_earlier_intervals`).
