## 334d. A gate operand is pinned only at the caller that already withholds its falsy value

**Assertion form:** `if a and b: work(b)` inside a helper (here: `if overlap and experiments`
in `handinmove.reconcile`, `if sync.get("since")` in `cmd_task_finished`). The test drives the
CALLER and asserts it passes `b=None` on the path that must skip the work (the standard tier
hands `reconcile_base_move` no experiments file). It reads as "no experiments file ⇒ no re-run".

**Why that's a gap:** the caller test proves what the caller passes, never what the helper does
with it. Drop `and b` from the helper and the caller test holds, because the helper is mocked
there; every helper test that reaches `a` true happens to supply `b` too. The skip the operand
exists for is never fed where the operand lives. Its siblings in the same round: a fail-closed
`rc != 0 or not stdout` whose only fixture had `rc != 0` (the empty-stdout disjunct alone never
fed), an early return `x == y or not z` where every `x == y` fixture also had `not z`, and a
`render` that joins two lines whose fixtures only ever produced one of them.

**Mutation that defeats it:** drop the operand (`if overlap:`); drop the disjunct; join with
`"\n"` instead of `" "`.

**Guard form that survives:** for every operand of a gate, one fixture at the gate's own level
in which ONLY that operand decides the outcome: `a` true and `b` falsy ⇒ assert the work was not
attempted (its first call is not made, not merely "the result is ok"). For a joined render, one
fixture where every part is present, compared whole. Related: [[62b|shape 62b]],
[[289|shape 289]], [[411|shape 411]], [[334c|shape 334c]].

**Found:** CMX-334 rework round 2 (2026-10-26), PR #883 — a held-out survivor among
`basesync.base_sha`/`moved`/`render`, `handinmove.reconcile`/`related_tests`/`_rerun_overlap`,
`dispatcher.reconcile_base_move` and `cmd_task_finished`; each operand now has its own fixture.
