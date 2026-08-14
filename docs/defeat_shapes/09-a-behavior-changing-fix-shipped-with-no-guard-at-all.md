## 9. A behavior-changing fix shipped with no guard at all

**Assertion form:** none. The PR states plainly that it adds no test or guard — "this is a
production script fix" — and the suite is green because nothing in it was ever pinned to the
invariant the fix introduces.

**Mutation that defeats it:** revert the fix verbatim. With nothing asserting the new
behavior, a one-line revert to the exact pre-fix code is indistinguishable from the fix
itself — the whole suite, unrelated to the change, stays green.

**Guard form that survives:** when the fix is "stop guessing X, ask the real source of truth
for X instead," a single guard rarely closes the whole gap on its own — read whichever of the
two applies:
- If the "real source of truth" can be called on its own (a function, a `--print-X` mode),
  a *behavioral* test can drive it directly and prove its output has a property a guess could
  never have. Cheap, but only proves the function is honest — not that production code still
  calls it. See shape 7 for that half.
- A *static* exact-line match on the call site closes the shape-7 gap the behavioral test
  leaves — see shape 7 for when a source-text match is the strong form instead of the weak
  one shape 1 warns about.

**Found:** CMX-275 rework round 1 (2026-08-13), PR #345. `scripts/smoke-fresh-install.sh`'s
dashboard port picker changed from a blind `$(( 20000 + (RANDOM % 20000) ))` guess to a real
`bind(('127.0.0.1', 0))` kernel probe, with "no test or guard was added or changed" stated in
the PR body. The judge reverted the diff in a throwaway checkout and 3027 tests, including
every other test in `tests/test_smoke_fresh_install.py`, stayed green — nothing anywhere
checked where `$DASH_PORT` actually came from, only that some dashboard eventually answered
200. Round 1 factored the probe into `pick_free_port()`, exposed it via a `--print-port` fast
path, and paired a behavioral test (repeated samples must include at least one port outside
the `[20000, 40000)` band a blind guess is confined to) with a static exact-line match on the
production `DASH_PORT=$(pick_free_port)` call site (shape 7: the behavioral test alone
doesn't notice that specific line reverted while `pick_free_port()` itself stays honest). The
structure (a real seam plus a paired behavioral+static test) was the right shape, but round 2
defeated *both* halves without touching the fix — see shape 10, which is about the specific
way "the kernel was asked" turned out to be a proxy no band or source match could pin down.
`pick_free_port()`/`--print-port` were kept; the two tests were replaced with a declared
`NOT GUARDED`.
