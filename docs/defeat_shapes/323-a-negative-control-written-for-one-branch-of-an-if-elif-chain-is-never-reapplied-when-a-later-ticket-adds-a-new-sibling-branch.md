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
