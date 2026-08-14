## 22. A field declared identically on every branch of one function, tested through only one branch

**Assertion form:** a single function returns several `Capability` objects — one per
branch (knob-on-and-usable, knob-on-but-unusable, externally-bound, off) — and every
branch declares the same field (`live_reload=True`). One test drives a boot snapshot
through the code that actually reads the field (`capabilities.live()`) — but only by
publishing from ONE of the branches (usually whichever one the fixture defaults to) and
flipping config so the *next* read lands on a different branch. That proves the flag
matters on the branch that was published from; it proves nothing about the flag on the
other N-1 branches, because `live()` only ever consults the flag on the row that was
actually in the boot snapshot.

**Mutation that defeats it:** flip `live_reload` to `False` on any branch OTHER than the
one the existing test happens to publish from. Every `effective()`-level test for that
capability reads config fresh and can't tell the flag apart; the one `live()` test never
publishes a snapshot FROM the mutated branch, so it never exercises that branch's own
flag either.

**Guard form that survives:** for each branch of the function, publish a boot snapshot
from THAT branch specifically, then change config so a later read would land on a
different branch, and assert `live()` picks up the change. One test per branch, not one
test for the function.

**Found:** CMX-280 rework round 3 (2026-08-14), PR #351 — this is
[[21|entry 21]] recurring one level down: not siblings across two functions, but branches
inside one. `_memory_slice_capability` returns four `Capability` objects (memcap-available
ON at `capabilities.py:161`, set-but-unwrapped OFF at `:168`, external-bound ON at `:186`,
plain OFF at `:192`), all `live_reload=True`. Round 2 added
`test_memory_slice_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot`, which
publishes from the `:192` OFF branch and flips to the `:161` ON branch — proving `:192`'s
flag matters, saying nothing about `:161`'s. The judge flipped `:161`'s flag to `False` in
a throwaway checkout; 3095 tests, including that test, stayed green. Closed by adding
`test_memory_slice_budget_on_going_off_live_does_not_stay_latched` (publishes from `:161`,
flips live to off) and its two siblings for `:168` and `:186`.
