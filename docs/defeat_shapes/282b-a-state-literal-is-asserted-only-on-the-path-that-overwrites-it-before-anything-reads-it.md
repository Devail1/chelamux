## 282b. A state literal is asserted only on the path that overwrites it before anything reads it

**Assertion form:** an UPDATE writes a state that the NEXT tick branches on (`pending` = "a
re-run I fired is in flight: wait out the red it replaces, and record a green as a flake").
The test drives every re-run as `RUNNING` then `RED` (`_red_again`), so the `RUNNING` tick
moves the row to `started` before any red or green is read. The test asserts what happens
after `started`, never what `pending` itself makes the next tick do.

**Mutation that defeats it:** in CMX-282's known-flake re-run, `ci_rerun_state='pending'` →
`ci_rerun_state='red'`. With `red`, a stale red read right after the re-run fires is not
waited out: it spends another re-run at once. A green on the same sha is not recorded as a
flake either. Every test stays green, because each one reads the row only after a red
re-read that ends in the same place whether the state was `pending` or `red`.

**Why it looks covered:** the full re-run → re-run → judge sequence is asserted end to end,
including counts and the final state. The literal sits in a transition the test helper
always steps past. It is a cousin of a fixture parked on a default: here the fixture is
the helper's fixed ordering of CI states.

**Guard form that survives:** for each column an UPDATE writes, drive the tick that READS it
straight after the write, with no intermediate state. Here: (1) a red read immediately after
the known-flake re-run is waited out (no re-run, no rework, still `pending`), then acts once
the settle window has passed on THIS re-run's `ci_rerun_at`; (2) a green read immediately
after it, with and without a `RUNNING` tick between, is recorded on `ci_flakes`.

**Found:** CMX-282 rework round 2, PR #797. `chela/dispatcher.py` known-flake re-run write;
fixed in `tests/test_ci_known_flake.py::test_the_stale_red_a_known_flake_rerun_replaces_is_waited_out`
and `::test_a_green_known_flake_rerun_is_recorded_as_a_flake`.
