## 397b. A display allowlist is asserted through the unfiltered record and a one-member fixture

**Assertion form:** what gets *recorded* and what gets *shown* are two different things. A
timer records every phase it saw (`RoundTimer.as_dict()` is unfiltered), and a tuple —
CMX-397's `HAND_IN_PHASES` / `NIGHTLY_PHASES` — decides which of them the summary table and
the printed hand-in line show, and in what order. The guards assert on the record ("the
record holds `remerge` with 2s slot wait"), and the only tests that go through the
summary/line use a fixture with ONE phase (`sweep`).

**Mutation that defeats it:** drop any other name from the tuple — `"remerge"` out of
`HAND_IN_PHASES`, `"provision"` out of `NIGHTLY_PHASES`. The record still holds the phase,
so the record test passes; the `sweep`-only summary fixture never had the dropped row, so it
passes too. The user's table and hand-in line silently lose a phase — the measurement the
feature exists to show. The same PR's `0o600` record file shipped the same way: the docstring
said 0600, and no test ever read a mode back.

**Why it looks covered:** each phase is asserted *somewhere*, by name, with exact values. But
none of those assertions is downstream of the filter. This is [[426|shape 426]] (an
allowlist proven with one member) arriving through a second door: the member IS tested, just
on the path that bypasses the allowlist.

**Guard form that survives:** seed ONE record with every phase at once plus one name no tuple
lists, and assert the rendered rows (table and line, not the summary dict alone) equal an
INDEPENDENT literal list — never the tuple under test — both sides: every listed phase shown,
the unlisted one not. For a declared file mode, read it back with `stat.S_IMODE` under
`umask(0)` (a `077` umask hides a `0644` behind the mask), for every writer that uses it.

**Found:** CMX-397 rework round 1, PR #945. `chela/judge_timing.py`; fixed in
`tests/test_hand_in_timings.py` ("the phase lists, pinned against INDEPENDENT literals",
"every timings file is private to the user").
