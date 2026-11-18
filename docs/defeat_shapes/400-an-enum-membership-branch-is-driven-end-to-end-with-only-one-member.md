## 400. An enum-membership branch is driven end to end with only one member, and the value it passes along is never read back

**Assertion form:** an entry point takes a `trigger` (here `idle` / `fallback` / `fixed` /
hand run) and does `if trigger in (IDLE, FALLBACK): finish_day(...)`, and also copies the
trigger into its result for the notice and the day record. Every end-to-end test calls the
entry point with `trigger="idle"`; the other members are tested only at the helpers that
pick or budget them (the scheduler, `run_budget`). The end-to-end tests assert the day
record's seconds/pending/done/status — but not the trigger it kept, and not the notice text.

**Why that's a gap:** with one member in every fixture, `in (IDLE, FALLBACK)` and `== IDLE`
are the same predicate to the suite — the second member's branch is dead. And a field that is
written through (`result["trigger"] = trigger`) but whose every consumer goes unasserted can be
replaced by `None` without any test noticing: the helpers that READ it are tested with a
hand-built dict that already carries it.

**Mutation that defeats it:** narrow `in (IDLE, FALLBACK)` to `== IDLE` (a fallback run never
reports back, so its day stays `pending` forever and the seconds it used are never charged);
or write `"trigger": None` into the result (the notice no longer says idle/fallback, the day
record loses the trigger). Both stayed green over 12598 tests.

**Guard form that survives:** parametrize the end-to-end test over EVERY member the
membership test admits, and in each row read the passed-along value back at each place it
lands — the result, the persisted record, and the rendered notice text — not just the fields
the first member's story cared about. Related: [[86b|shape 86b]] (a once-per-key latch fixtured
with a single key), [[02|shape 2]] (a fixture parked on a default).

**Found:** CMX-400 rework round 1 (2026-11-18), PR #944 — `sweep_nightly.run`'s
`finish_day` call and its result's `trigger`. Closed in `tests/test_sweep_nightly_idle.py` by
`test_an_idle_or_fallback_run_reports_back_to_the_day_and_says_why_it_started`.
