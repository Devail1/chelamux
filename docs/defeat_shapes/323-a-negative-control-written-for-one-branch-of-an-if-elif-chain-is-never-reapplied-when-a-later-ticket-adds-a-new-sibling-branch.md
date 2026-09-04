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

---

### Also found on this task (round 5, same file): fixing a partial per-clause loop by re-checking the wrong sibling's clause list, and a promise mirrored onto one surface (the summary) but not the other (the help) it also appears on

**Assertion form:** round 4 (above) diagnosed the per-clause loop as "only partially
re-derived" and fixed it by porting `--apply`'s per-clause loop shape onto `--retire-empty`
— but the clause LIST it wrote was still wrong. `--apply`'s three clauses are "were
re-stamped at their new address", "archived to roster-archive.json, then removed", and "left
for chela-telegram"; the round-4 fix enumerated `--retire-empty`'s two *narrowness* clauses
("only the MANUAL rows with nothing on record", "was left untouched",
"re-run with --apply...") but dropped the one clause that actually carries over verbatim
between the two summaries — "archived to roster-archive.json, then removed", the only clause
on either summary that tells an operator a retired row is RECOVERABLE. The fix ported the
sibling's *loop shape* faithfully while silently re-deriving a different clause list instead
of diffing the two summaries' prose against each other clause-by-clause.

Separately, and independently: the SAME narrowness promise ("every REVIVABLE row and every
MANUAL row that still carries a cwd/session is left untouched") appears on TWO operator-facing
surfaces of `--retire-empty` — the post-write summary and the pre-flight `--help` text — because
the feature's safety property is one an operator needs to know both before running the flag
(help) and after (summary). Round 4 put a per-clause guard on the summary's copy. Nobody asked
whether the identical promise, living in a *different* string literal for a *different* surface,
also needed its own guard — it is not a branch of the same if/elif/else chain this shape's
title describes, so `git grep`-ing the chain's siblings (round 4's own guard-form advice) does
not surface it; the two copies of the promise are two separate string literals in two
unconnected functions (`cmd_restore`'s print vs. `argparse.add_argument(help=...)`) that happen
to say almost the same sentence.

**Mutation that defeats it:**

```diff
-               "(no cwd, no session) were archived to roster-archive.json, then removed. "
+               "(no cwd, no session) were handled. "
```

```diff
-              "Every REVIVABLE row and every MANUAL row that still carries a cwd/session "
-              "is left untouched. telegram-bindings.json is still never written.",
+              "telegram-bindings.json is still never written.",
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3431 passed) with either corruption
in place: the first because round 4's loop never listed the archive-then-removed clause; the
second because neither test reading `--retire-empty`'s help
(`test_restores_help_documents_retire_empty`, `test_retire_emptys_help_states_the_permanent_bindings_exclusion`)
asserted the narrowness sentence — the latter test asserted only the bindings-exclusion
sentence that sits right next to it in the same help string.

**Why round 4's own fix didn't close this:** a per-clause loop is only as complete as the list
of clauses fed into it. Modeling the LOOP SHAPE on a sibling test (as round 4's guard-form
advice recommends) proves the mechanism is right, but the clause list itself has to be
re-derived from THIS summary's own prose, not assumed to be "the other two clauses this
summary makes that aren't the read-only contradiction." A reviewer who sees a three-item
`for clause in (...)` loop that visibly mirrors a sibling's three-item loop reasonably reads it
as complete; nothing about the shape signals that one specific clause — the one that happens to
be prose-identical to a clause already guarded on the sibling — was dropped from the list. And
for the help/summary duplication: two string literals expressing the same safety promise, on
two surfaces read at two different moments (before the write vs. after it), are not linked by
any shared code path a `git grep` on "the chain's siblings" would find — the only way to catch
the second copy going stale is to ask, for every promise a guard newly pins, "does this exact
sentence, or a close paraphrase of it, appear anywhere else in the diff," not just "is this
branch's sibling in the same dispatch already guarded."

**Guard form that survives:** when porting a per-clause loop from a sibling test, diff the two
summaries' PROSE side by side first and list every clause that is either (a) present in both,
verbatim or near-verbatim, or (b) unique to the one under test — don't reconstruct the list from
memory of "what this summary is roughly about." Separately, when a PR states a safety/narrowness
promise, `grep` the whole diff for that promise's key phrase (not just its home function) to find
every surface it was written onto, and confirm each surface has its own assertion — a promise
appearing in both `--help` and a post-write summary needs a guard on each, because an operator
can read either one without the other (a MANUAL row's outcome is decided by the help before the
flag is ever run, not just by the summary after).

**Found:** `chela/main.py`'s `cmd_restore` print block and `--retire-empty`'s
`add_argument(help=...)` (CMX-323 rework round 5, PR #410). Closed by adding the missing
"archived to roster-archive.json, then removed" clause to
`test_retire_empty_must_not_repeat_the_READ_ONLY_claim_it_just_broke`'s existing loop, and by
extending `test_retire_emptys_help_states_the_permanent_bindings_exclusion` to also assert the
narrowness-promise sentence within the `--retire-empty`-scoped help block (which itself was
re-scoped from `out.split("--retire-empty", 1)[1]`, which resolves to the USAGE line's first
occurrence and therefore also contains `--apply`'s help text, to `out.rsplit("--retire-empty",
1)[1]`, the text after the option's own — and last — marker).

**See also:** [[311|shape 311]] and the round-1/round-4 entries above — the same "a control
written for one sibling is never mirrored onto a structurally identical second" family, applied
here one level down (a clause within an already-mirrored loop, and a promise duplicated across
two unrelated functions rather than across branches of one dispatch).
