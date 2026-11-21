## 455. A wired-in term is fixtured where floor division, or the input's own order, already gives the same answer

**Assertion form:** the nightly sweep (CMX-455) sizes a night's sample as
`(budget - overhead) // per_mutation` and interleaves PRs by `rank` (the base's log, newest
first). Both are proven at the helper (`capacity(...)`, `sample_order(..., pr_rank=...)`)
and once through the real night, so the wiring *looks* driven:

- `test_a_night_sizes_its_sample_by_the_measured_pace` used budget 1000, per 400, overhead
  100. `(1000 - 100) // 400 == 1000 // 400 == 2`. Integer division swallowed the overhead
  whole, so the end-to-end test could not see whether it reached the call.
- No end-to-end test fed the battery's `order` callback mutations in an order that differed
  from rank order. Without `rank`, `sample_order` keeps the groups in insertion order, and
  the order a fixture happens to give it is often the order rank would produce anyway.

**Mutation that defeats it:** `cap = capacity(budget, per, over)` →
`cap = capacity(budget, per)`; `sample_order(muts, first, lines, origin, k, cap, rank)` →
`sample_order(muts, first, lines, origin, k, cap)`. Each one drops an argument and leaves
behind a plausible default (zero overhead, insertion order).

**Guard form that survives:**
- Pick numbers where the dropped term changes the **quotient**, not just the dividend:
  budget 1000, per 300, overhead 500 gives `500 // 300 = 1` (then clamped up to the
  one-per-PR floor of 2) against `1000 // 300 = 3`. Check that the floor or clamp
  downstream doesn't also hide the difference (shape [[398b]]).
- For an ordering argument, hand the production callback its input in the order **opposite**
  to the ranked order (the older PR's mutation first) and assert the newer PR's comes out
  first. Related: [[365b]] (input order stands in for a tie-breaker) and [[75b]] (a dropped
  wiring kwarg degrades to a plausible default).
