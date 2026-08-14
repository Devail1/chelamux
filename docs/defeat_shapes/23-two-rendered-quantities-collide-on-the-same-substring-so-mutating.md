## 23. Two rendered quantities collide on the same substring, so mutating one is invisible

**Assertion form:** a detail string renders more than one distinct numeric quantity
derived from the same inputs (a ceiling, and a headroom computed as ceiling-minus-current)
side by side in prose. The guard asserts each *expected* number appears as a substring —
but only pins the ceiling's value, not the headroom's, and the two would print identically
if headroom were computed wrong (e.g. mistakenly re-emitting the ceiling instead of
subtracting).

**Mutation that defeats it:** change the derived quantity's formula so it collapses onto
the OTHER quantity's value (`headroom = human_size(max_bytes)` instead of
`human_size(max_bytes - current)`). The string still contains every substring the guard
checks — the ceiling's value was already being asserted, and now headroom prints the same
digits — so the suite stays green even though the number that matters (how much room is
actually left) is wrong.

**Guard form that survives:** when a string renders two or more numbers derived from the
same inputs, choose fixture values where every rendered number is numerically distinct,
and assert each one by the surrounding words that make it unambiguous which quantity it
is (`"currently using 10.0G"`, `"~2.0G headroom"`) — not just the bare digits, which could
belong to either.

**Found:** CMX-280 rework round 3 (2026-08-14), PR #351 —
`test_capability_reports_an_external_bound_as_on` asserted `"12.0G"` (the ceiling) and
`"83%"` but never pinned the headroom value by itself; with `max_bytes=12G` and
`current=10G`, correct headroom is `"2.0G"`, and the judge's mutation
(`headroom = config.human_size(bound["max_bytes"])`) made headroom render `"12.0G"` too —
a substring the test already asserted for the ceiling. 3095 tests stayed green. Closed by
asserting `"currently using 10.0G"` and `"~2.0G headroom"` as distinct, unambiguous
substrings.
