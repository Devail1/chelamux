## 323. A negative control written for one branch of an if/elif/else chain is never reapplied when a later ticket adds a new sibling branch

**Assertion form:** `chela restore`'s summary print is a three-way `if apply_flag: ... elif
retire_flag: ... else: ...`. When CMX-195 shipped as read-only, the `else` branch's "report
only — chela restore never writes to a store" line was simply true. CMX-196 added the write
half (`--apply`) and, because the `else` line was now a lie whenever `--apply` had just
written, added a round-4 negative control pinning exactly that: `"never writes to a store"
not in out` after `--apply`. CMX-323, a separate ticket landing weeks later, added a THIRD
branch (`--retire-empty`) to the same chain — structurally identical to `--apply` in the one
respect that matters here: it also writes, so it also must not fall through to the `else`
branch's read-only claim. Nobody went back and asked "does the round-4 negative control need
a copy for the new branch" — the ticket that owns the mirror (CMX-196, closed) and the ticket
that adds the thing needing mirroring (CMX-323) are different tickets, so there is no single
diff where "add a branch" and "extend the guard that already exists for its siblings" show up
together for a reviewer to notice. The same PR also added a new sentence to the untouched
`else` branch itself — a pointer telling the operator that `--retire-empty` exists and what it
is for (the discoverability half of the fix; before it, the only documented way to clear a
no-op MANUAL row was hand-editing a store) — and that brand-new sentence had no assertion of
its own anywhere, because it is prose nobody had reason to write a test for until this ticket
existed, and this ticket's own tests exercised `--retire-empty`'s effects, not the OTHER
branch's mention of it.

**Mutation that defeats it:** neutralise the new branch's condition so it falls through to the
next one, and delete the new branch's own contribution to a sibling branch's message:

```diff
- elif retire_flag:
+ elif False and retire_flag:
```

```diff
-               "chela watch/register for a REVIVABLE row, re-dispatch, `chela restore "
-               "--retire-empty` for a MANUAL row with nothing on record, or clear a row "
+               "chela watch/register for a REVIVABLE row, re-dispatch, or clear a row "
```

The first mutation makes `--retire-empty` print "report only — chela restore never writes to
a store" immediately after it has archived and removed a row — the exact contradiction CMX-196
round 4 exists to catch, just on a branch that didn't exist yet when round 4 was written. The
second mutation deletes the one sentence in the entire codebase that tells an operator
`--retire-empty` is how to clear a no-op MANUAL row — silently regressing the ticket's own
stated purpose. Both mutations, applied by the judge to a throwaway checkout of the PR's head,
left `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3424 passed, 0 failed, 0 error(s)):
no test drove `--retire-empty` and checked the read-only claim was absent, and no test named
the `--retire-empty` pointer sentence at all.

**Why the sibling's coverage doesn't transfer:** a negative control proves silence for the
specific flag combination it drives. CMX-196 round 4 ran `_drive(["restore", "--apply"])` and
asserted the claim was absent from *that* output — it says nothing about what
`_drive(["restore", "--retire-empty"])` prints, even though both flags reach the same `if
apply_flag: ... elif retire_flag: ... else:` statement and share the identical failure mode.
Structural closeness (same function, same chain, same contradiction) reads as "this is already
guarded" to a reviewer who remembers the round-4 fix landed; it is only proof that the
*pattern* for guarding it is known, not that every branch matching the pattern has its own
test exercising it.

**Guard form that survives:** when a PR adds a new branch to an if/elif/else chain (or a new
case to a switch, a new subclass implementing a shared method, a new value on an existing
enum-like dispatch), `git grep` the chain's sibling branches for negative controls that assert
something is ABSENT under a sibling flag/value, and write the same shape of test for the new
branch, driving it through the real CLI/entry point rather than asserting on the source text.
Separately, any new sentence added to an EXISTING branch's message as part of the same change
(not just the new branch's own output) needs its own literal-substring assertion — "the tests
for this ticket cover the new branch" is not the same claim as "every new sentence anywhere in
the diff is asserted," and a new pointer sentence living inside an old branch's print block is
easy to miss precisely because the diff around it looks like it belongs to a different,
already-tested code path.

**Found:** `chela/main.py`'s `_cmd_restore` summary print (CMX-323, PR #410, rework round 1).
`tests/test_restore_cli.py::test_apply_must_not_repeat_the_READ_ONLY_claim_it_just_broke`
(CMX-196 round 4) drove `--apply` and asserted the read-only claim was gone; no equivalent
existed for `--retire-empty`, and no test asserted the `` `chela restore --retire-empty` for a
MANUAL row with nothing on record `` pointer sentence in the plain dry-run's read-only
message. Closed by adding
`test_retire_empty_must_not_repeat_the_READ_ONLY_claim_it_just_broke` (mirroring round 4 onto
the new branch) and
`test_a_dry_run_points_the_operator_at_retire_empty_for_a_no_op_MANUAL_row` (pinning the new
pointer sentence verbatim).

**See also:** [[311|shape 311]] — the same "a negative control proven for one implementation
doesn't transfer to a structurally identical sibling" gap, but 311's siblings are two separate
classes each implementing the same method; this shape's siblings are branches of one function's
own dispatch, added to that function by two different tickets months apart.

---

### Also found on this task (round 3, same file): an order invariant proven at a helper's boundary is unguarded at the positional consumer one layer up

**Assertion form:** `restore.retire_empty`'s own docstring promises "one ApplyResult per input
verdict, same order... callers that zip verdicts against results do not need to know which
write path produced them," and round 2's rework closed exactly that at the helper's own
boundary: `test_retire_empty_preserves_order_one_result_per_verdict_mixed_batch` drives a
multi-target batch and asserts `results[2]`/`results[3]` individually, so reversing the list
*inside* `retire_empty` now goes red. But the helper's only consumer, `cmd_restore` in
`chela/main.py`, reads that contract positionally one layer up —
`for v, r in zip(verdicts, results ...)`, appending `=> {r.action}` to row `v`'s printed line.
Nothing independently proved that zip is fed results in the order it expects. The one
end-to-end test that exercises the CLI seam
(`test_chela_restore_retire_empty_clears_ONLY_the_row_with_nothing_on_record`) asserted three
outcome words as bare substrings of the whole report — `'=> archived' in out and '=> kept' in
out and '=> left-to-daemon' in out` — never which LINE each word landed on.

**Mutation that defeats it:** reverse the list at the CALL SITE, one layer above the helper
whose own order guard round 2 just closed:

```diff
-         results = restore.retire_empty(verdicts)
+         results = restore.retire_empty(verdicts[::-1])
```

`retire_empty`'s own contract is untouched — it is never invoked with a reversed argument by
*its* unit test, so that guard stays green — and every store/archive/byte-level assertion in
the e2e is unaffected, because `apply()`'s writes act on the `Verdict` objects themselves, not
on list position or on `results`. Only the OPERATOR-VISIBLE report changes: in the e2e's
two-target fixture the REVIVABLE row's line now reads `=> archived` and the retired row's line
reads `=> kept`. The multiset of outcome words printed is identical, only which row each word
sits beside is wrong — a bare `'=> archived' in out` substring check cannot see a permutation.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3430 passed) with this mutation
applied to a throwaway checkout of the PR's head.

**Why the helper's own guard doesn't transfer:** proving "f(x) preserves order" and proving
"the caller of f(x) preserves order too" are two different facts. A mutation that never
touches `f` at all — it edits only the one line that calls it — leaves every test pinned to
`f`'s internals green, because none of them execute the caller's code. The bug is a wiring
fact about the SEAM (does the call site pass what it received, in the order it received it, to
the thing that consumes it positionally), not a logic fact about the helper — the same
distinction [[60|shape 60]] draws for a shared-helper contract, except here there is only one
call site and the miss is *above* it rather than at a sibling that bypasses it.

**Guard form that survives:** when a helper's contract is "N in, N out, same order" AND a
caller consumes the result positionally (zip, index, unzip), the caller's own test needs a
fixture with two or more items that produce genuinely DIFFERENT, distinguishable outcomes (a
permutation of two items sharing the same outcome word is just as invisible to a substring
check), and the assertion must be scoped to one line per item — e.g. this file's own
`_line_with(out, "[store]", "@id")` helper, checking that specific line's suffix — never a bare
`phrase in whole_output` check when POSITION is what is actually being proven.

**Found:** `chela/main.py`'s `cmd_restore`, the `elif retire_flag and verdicts:` branch
(CMX-323 rework round 3, PR #410). Closed by rewriting
`test_chela_restore_retire_empty_clears_ONLY_the_row_with_nothing_on_record` to assert each
row's outcome on its own `_line_with`-scoped line (`[session-ids] @8` → `=> archived`,
`[telegram.bindings] @2` → `=> left-to-daemon`, `[session-ids] @5` → `=> kept`,
`[session-ids] @7` → `=> kept`, `[inbox.orchestrator] @1` → `=> kept`) instead of the three
bare substring checks.

**See also:** [[60|shape 60]] — the same "a boundary guard doesn't cover the seam that calls
it" family, but shape 60 is about a sibling call site skipping a shared helper entirely; this
is a single call site consuming an already-guarded helper's output out of order. [[15|shape
15]] and [[306|shape 306]] — order/identity collapsing when a fixture cannot distinguish
positions, the same root cause this shape's fix (a two-DIFFERENT-outcome, per-line fixture)
addresses directly.

---

### Also found on this task (round 4, same file): a mirrored negative control copied only the simplest clause, not the per-clause loop it was modeled on

**Assertion form:** `--apply`'s and `--retire-empty`'s post-write summaries are structurally
identical multi-clause reports. `--apply`'s own negative control
(`test_apply_must_not_repeat_the_READ_ONLY_claim_it_just_broke`, CMX-196 round 4) does the job
right: after checking the read-only claim is gone, it loops over all THREE of `--apply`'s
dispositions ("were re-stamped at their new address", "archived to roster-archive.json, then
removed", "left for chela-telegram") with an explicit comment that the summary is the
operator's only record of what happened and dropping any one clause "leaves rows whose fate is
unstated." Round 1's mirror onto `--retire-empty` (the shape documented above) correctly
ported the read-only-claim check, but instead of porting the per-clause loop, it added ONE
bare `assert "..." in out` for the single easiest clause to describe (the retired-row
disposition). The KEPT disposition — the fate of the REVIVABLE and still-actionable-MANUAL
rows, i.e. the majority of rows on a narrower flag — had no assertion of its own anywhere in
that test or the e2e (which only proves KEPT rows keep their bytes, never that the summary
*states* they were left alone). The two tests read as "the same guard, twice" because one was
visibly modeled on the other and shares its docstring's framing; only the modeled-from test
actually enforced the property its own comment describes.

**Mutation that defeats it:** delete the KEPT-disposition sentence from `--retire-empty`'s
summary entirely, leaving the retired-row clause (the one thing under test) untouched:

```diff
-               "Every REVIVABLE row and every MANUAL row that still carries a relaunch "
-               "command was left untouched — see each row's outcome above. Act on those by "
+               "See each row's outcome above. Act on those by "
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3431 passed) with the corruption in
place: the mutated sentence sits entirely outside the one substring the test happened to
check, and every other guard on `--retire-empty` (the mutual-exclusion test, the per-row
`=> kept` / `=> archived` line assertions from round 3, the byte-identity checks on untouched
stores) proves the *rows* were left alone — none of them reads the *summary text* the operator
actually sees.

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
loops over all three of `--apply`'s clauses. Closed by extending the existing test into the
same per-clause `for clause in (...)` loop the `--apply` sibling uses, covering both of
`--retire-empty`'s own clauses (the retired disposition and the KEPT disposition) plus the
`--apply` pointer sentence.

**See also:** [[311|shape 311]] — also a sibling-coverage gap between two structurally similar
tests, but shape 311 is a control missing *entirely* from one sibling; this shape is a control
present on both siblings that was only partially re-derived, so it looks fully mirrored at a
glance. [[310|shape 310]] — a changed clause with no assertion anywhere, the same underlying
gap but arising from a doc-string edit rather than a partially-copied test.
