## 324. A mirrored negative control copies only the simplest clause, not the per-clause loop it was modeled on

**Assertion form:** two CLI flags print structurally identical multi-clause summaries after
writing — `--apply`'s and `--retire-empty`'s post-write reports in `chela restore`, added one
ticket apart. `--apply`'s summary test
(`test_apply_must_not_repeat_the_READ_ONLY_claim_it_just_broke`) does the job right: after
checking the read-only claim is gone, it loops over all THREE of `--apply`'s dispositions
("were re-stamped at their new address", "archived to roster-archive.json, then removed",
"left for chela-telegram") and asserts each is present, with an explicit comment that the
summary is the operator's only record of what happened and dropping any one clause "leaves
rows whose fate is unstated." When the next ticket added `--retire-empty`'s own version of the
same test, it correctly copied the read-only-claim check, but instead of copying the loop, it
added ONE bare `assert "..." in out` for the single easiest clause to describe (the retired-row
disposition). The KEPT disposition — the fate of the REVIVABLE and still-actionable-MANUAL
rows, i.e. the majority of rows on a narrower flag — has no assertion of its own anywhere in
that test or the e2e (which only proves KEPT rows keep their bytes, never that the summary
*states* they were left alone). The two tests read as "the same guard, twice" because one was
visibly modeled on the other and shares its docstring's framing; only the modeled-from test
actually enforces the property its own comment describes.

**Mutation that defeats it:** delete the KEPT-disposition sentence from `--retire-empty`'s
summary entirely, leaving the retired-row clause (the one thing under test) untouched:

```diff
-               "Every REVIVABLE row and every MANUAL row that still carries a relaunch "
-               "command was left untouched — see each row's outcome above. Act on those by "
+               "See each row's outcome above. Act on those by "
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stays green: the mutated sentence sits entirely
outside the one substring the test happens to check, and every other guard on `--retire-empty`
(the mutual-exclusion test, the per-row `=> kept` / `=> archived` line assertions, the
byte-identity checks on untouched stores) proves the *rows* were left alone — none of them
reads the *summary text* the operator actually sees.

**Why copying the read-only check but not the loop hides this:** the two tests share a
docstring lineage ("CMX-196 round 4 guarded exactly this... mirrored onto `--retire-empty`"),
which reads as evidence the whole test was ported. But "mirrored" only ever covered the
contradiction check (read-only claim must be gone); the per-clause enumeration — the part of
the sibling that actually forces every disposition sentence to survive — was never re-derived
for the new flag's own (different) set of clauses. A reviewer skimming both tests side by side
sees the same shape (drive the flag, assert the old claim is gone, assert the new claim is
there) and reasonably assumes the coverage is equivalent; it is only equivalent for the one
clause that happened to get copied.

**Guard form that survives:** when a test is explicitly modeled on a sibling ("guarded exactly
this... but the guard was never mirrored"), treat the sibling's assertion *shape* — not just
its target string — as the thing to port. If the sibling loops over N clauses because the
summary makes N distinct claims, enumerate this summary's own N clauses (they will usually
differ in wording and count from the sibling's) and assert each one individually, with the same
"any one clause missing leaves a disposition unstated" framing. A single bare substring check
is a signal the port was only partial.

**Found:** `chela/main.py`'s `cmd_restore` (CMX-323 rework round 4, PR #410).
`tests/test_restore_cli.py::test_retire_empty_must_not_repeat_the_READ_ONLY_claim_it_just_broke`
pinned only the "only the MANUAL rows with nothing on record" clause; its sibling
`test_apply_must_not_repeat_the_READ_ONLY_claim_it_just_broke` (same file, ~200 lines above)
loops over all three of `--apply`'s clauses. `chela judge` deleted the KEPT-disposition
sentence from `--retire-empty`'s summary in a throwaway checkout;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3431 passed) with the corruption in
place. Closed by extending the existing test into the same per-clause `for clause in (...)`
loop the `--apply` sibling uses, covering both of `--retire-empty`'s own clauses (the retired
disposition and the KEPT disposition) plus the `--apply` pointer sentence.

**See also:** [[311|shape 311]] — also a sibling-coverage gap between two structurally similar
tests, but shape 311 is a control missing *entirely* from one sibling; this shape is a control
present on both siblings that was only partially re-derived, so it looks fully mirrored at a
glance. [[310|shape 310]] — a changed clause with no assertion anywhere, the same underlying
gap but arising from a doc-string edit rather than a partially-copied test.
