## 374b. A carried record is kept in a fixture where its stored and current position coincide

**Assertion form:** `assert read_owed(wf) == [e]` after `_settle_owed(..., [(e, 2)], ...,
keep_survivors=True)`, with `e["line"] == 2` (here: `sweep_nightly._settle_owed`, keeping an
owed survivor whose filing failed). The code keeps it as `{**e, "line": ln}`, the line the
entry was LOCATED on tonight, because later merges shift it. It reads as "the kept entry is
written back correctly".

**Why that's a gap:** the fixture records the entry at the very line it is located on, so
"re-anchored to tonight's line" and "written back unchanged" produce the same dict. Keep `e`
as it was stored and the test holds; on a real night the entry is persisted at a stale line
and the next night's search starts from the wrong place. A sibling branch of the same
function (the plain still-UNRUN keep) was tested with a moved line, so the gap hides behind
a neighbour that looks covered. The integration tests had the same blind spot: every owed
entry was seeded at the line it already sat on.

**Mutation that defeats it:** `keep.append({**e, "line": ln})` → `keep.append(e)` in the
survivor branch.

**Guard form that survives:** for every branch that carries a record forward, seed it at a
position that DIFFERS from where it is found (recorded at 7 or 9, located at 2) and assert
the whole written record (`{**e, "line": 2}`, other fields preserved), not just its identity
tuple. Pair it with one test per way the carry can be triggered (here: the filer raising, the
filed-key store refusing, the category's sites not being listable), all asserting the same
full outcome.

**Found:** CMX-374 rework round 2 (2026-11-12), PR #923, a held-out survivor probing the
owed-survivor invariant "through its wiring". Fixed by
`test_settle_keeps_a_survivor_while_its_filing_failed` (moved line) and
`test_an_owed_survivor_that_was_not_filed_stays_owed_whatever_stopped_it`.
