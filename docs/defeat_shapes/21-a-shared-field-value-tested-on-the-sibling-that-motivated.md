## 21. A shared field value tested on the sibling that motivated it, not on the one that already had it

**Assertion form:** two (or more) declarations set the same field to the same value —
`live_reload=True` on both `worktree_disk_budget` and `memory_slice_budget`. A PR adds the
field and a test that exercises what the field actually *does* (here, `capabilities.live()`
reconciling a `live_reload` capability against fresh config instead of a stale boot snapshot)
— but only for the capability the PR's own ticket was about. The sibling declaration, present
before the PR and unchanged by it, reads as "already covered" because a plain `effective()`
test already asserts its `on`/`detail` values — except `effective()` always reads config
fresh regardless of the flag, so that test cannot distinguish `live_reload=True` from
`live_reload=False`. Nothing in the suite ever drives the sibling through `capabilities.live()`.

**Mutation that defeats it:** flip the sibling's `live_reload` to the opposite of what its own
comment says it must be. Every existing test for that capability — the `effective()` tests
and the dispatcher-gate tests — reads config directly and never goes through `live()`, so none
of them notice.

**Guard form that survives:** when a field is declared identically on N siblings and a new
test proves what the field does for sibling K, ask whether siblings 1..N-1 already have an
equivalent test — not just a test with the same *name shape* (`test_capability_reports_*`),
but one that actually exercises the code path the field controls. If they don't, they are
untested for that field, no matter how long they've been in the suite.

**Found:** CMX-280 rework round 1 (2026-08-14), PR #351. `_memory_slice_capability` was new
in this PR and got `test_memory_slice_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot`
driving it through `capabilities.live()`. `_worktree_disk_budget_capability` had carried
`live_reload=True` since CMX-164, predating this PR, with only `effective()`-level tests
(`test_capability_reports_off_by_default`, `test_capability_reports_on_with_the_human_size`)
— both blind to the flag. The judge flipped `worktree_disk_budget`'s `live_reload` to `False`
in a throwaway checkout; 3083 tests, including every `worktree_disk_budget` test, stayed
green. Closed by adding
`test_worktree_disk_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot`, mirroring
the memory-slice test: publish a boot snapshot with the budget off, then set the env var with
no restart and assert `capabilities.live_capability("worktree_disk_budget")` picks it up.
