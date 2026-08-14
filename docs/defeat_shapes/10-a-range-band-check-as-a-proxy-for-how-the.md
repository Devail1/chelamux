## 10. A range/band check as a proxy for "how the value was produced"

**Assertion form:** the guard asserts a produced value falls inside (or outside) a specific
numeric band, as a stand-in for a claim about *mechanism* — "this came from asking the kernel,
not from arithmetic" — rather than observing the mechanism directly. A paired "wiring" half
source-matches the one call site that's supposed to feed the value through.

**Mutation that defeats it:** two independent mutations, both against the same target.
- The band check is defeated by generating the arithmetic guess from a *different* band than
  the one the test happens to check. It's still a guess, still not the kernel — the mutation
  just moved to a part of the number line the test wasn't looking at. Any fixed band the test
  picks, the next mutation can dodge; there is no band exhaustive enough to close this, short
  of the entire feasible port space.
- The paired source match is defeated by leaving the matched line in place, unmodified, and
  *shadowing* its result on the very next line — a pattern this same script already uses
  legitimately elsewhere (a retry path re-picking a port after occupying the first one), so
  it isn't even an unusual shape to write.

**Why this is a distinct shape from 5, 6, and 9:** shape 5 is about reading a *constant* off
source instead of a rendered value; shape 6 is coverage resting on a coincidence in the data;
shape 9 is no guard at all. This shape is subtler than any of those: a real seam exists (the
production function is directly callable), the test drives it hundreds of times, and it
genuinely fails against the first mutation tried. It looks, and mostly is, the "guard form
that survives" shape 9 itself prescribes — right up until a second experiment targets the
*specific number range* the assertion happens to check, rather than the code path.

**The deeper problem, and why "guard form that survives" isn't a fix here:** the property
being claimed — "the kernel was asked" — is a mechanism, not an outcome. A mechanism has no
observable trace in the return value alone; a kernel-assigned port and a well-guessed one are
bit-for-bit indistinguishable numbers. The *only* outcome where the two mechanisms provably
differ is **contention** (something else already holds the port a guess would have picked) —
and a unit test, run on an otherwise-idle box, does not reproduce contention. Widening the
band the test checks doesn't change this; it only raises the number of mutations needed to
find an unchecked band, the same treadmill shape 1's "just pick a different dead-code wrapper"
represents for source matches.

**Guard form that survives:** stop looking for a wider proxy and
ask whether the property is observable at all under the constraints a test can actually
create (no root, no exhausting the OS's whole ephemeral range, no reliably-reproducible
contention). If it isn't, **declare `NOT GUARDED`**: name exactly what's unprotected, why
(the mechanisms are indistinguishable outside contention), and what covers the fix instead
(a one-line, self-evidently correct change, and/or the original bug report — here, a real CI
flake under contention — that already proved the old code wrong once). A declared gap that
says this beats a third band nobody can prove is the last one.

**Found:** CMX-275 rework round 2 (2026-08-13), PR #345 — both halves of the round-1 WIRING
guard for `scripts/smoke-fresh-install.sh`'s `pick_free_port()` (see shape 9) survived the
judge's mutations: the behavioral test's band check was defeated by a guess drawn from
`[40000, 60000)` instead of `[20000, 40000)`, and the static source match was defeated by
leaving `DASH_PORT=$(pick_free_port)` in place and overriding `$DASH_PORT` on the next line.
Resolved round 3 by declaring the gap `NOT GUARDED` in `tests/test_smoke_fresh_install.py`
rather than writing a third proxy.
