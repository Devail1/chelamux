## 299b. A keep-one-value filter over a many-valued enum is fixtured only with the one rejected value its rewrite names

**Assertion form:** a filter keeps exactly one value of an enum of four
(`[c for c in commits if c["tier"] == STANDARD]`, out of STANDARD / CORE / HUMAN / PROSE). The
test feeds it a STANDARD row (kept) and a CORE row (dropped), plus a negative control that
relabels the CORE row STANDARD and watches it come back. A sibling: a file filter
`_sweepable(f) or _unsupported(f)` fixtured only with files the first disjunct already keeps.

**Why that's a gap:** with one rejected value in the fixture, `== STANDARD` and `!= CORE` are
the same predicate. The test names the value the requirement was written about ("a core PR is
not swept") and stops, so every OTHER rejected value — the HUMAN tier, which the merge gate
also judged, and PROSE — is unfed. Likewise a disjunct whose only fixtures are already kept by
the other one is dead to the suite.

**Mutation that defeats it:** `== risktier.STANDARD` → `!= risktier.CORE`. Suite green — while
in production every always-human PR (WORKFLOW.md, plugin/**) is swept again and filed against.
And `_sweepable(f) or _unsupported(f)` → `_sweepable(f) or False`: a standard PR's `.go` file
silently drops out and the night reads "clean", never "UNKNOWN".

**Guard form that survives:** feed a keep-one filter one row of EVERY value the enum has (a
parametrized "any tier but standard is not swept" plus one mixed run asserting only the
standard row is swept), so `!=` any single value goes red. Feed each disjunct a row only it
keeps, and assert what that row produces downstream (here: the `UNKNOWN` count in the notice),
plus the other side (an unsupported file from a non-standard PR is NOT reported).
Related: [[299|shape 299]], [[68c|shape 68c]], [[6b|shape 6b]].

**Found:** CMX-299 rework round 2 (2026-10-21), PR #819. Closed in
`tests/test_sweep_nightly.py` by `test_a_merged_pr_of_any_tier_but_standard_is_not_swept`,
`test_with_every_tier_merged_only_the_standard_pr_is_swept`,
`test_an_unsupported_file_a_standard_pr_merged_is_reported_unknown` and
`test_unsupported_is_only_what_the_standard_prs_merged`.
