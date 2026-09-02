## 336. An explicit argument with its own fallback chain is proven only by facts true under every link of the chain, never by the value only the explicit link produces

**Assertion form:** `dispatcher.acknowledge_blocked_race(ident, by="", note="")` resolves the
actor to record with a three-link fallback chain — `who = (by or "").strip() or
os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"` — documented as stamping
"WHO... the first of the four things the acknowledgement is documented to stamp." Every test
in the file that reaches this line either passes no `by` at all (proving the env/`"unknown"`
links) or passes `by="liav"` inside a test whose only assertion is that a `blocked_race_ack`
event of the right TYPE fired — never that its `by` field, or the row's `blocked_race_ack_by`
column, or the CLI's returned `result["by"]`, equals the specific string that was passed. Both
kinds of fixture are true regardless of which link of the chain actually produced the stamped
value, so neither one can tell "the explicit argument was recorded" apart from "the explicit
argument was silently discarded and the chain fell through to its own second link instead."

**Mutation that defeats it:** delete the explicit argument from its own first link, so the
chain always resolves through its remaining links:

```diff
-     who = (by or "").strip() or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
+     who = ("" or "").strip() or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
```

Any test that never sets `$USER`/`$USERNAME` to something the explicit `by` would have to
differ from — or never reads back the value that was actually stamped, only that *something*
was stamped — cannot distinguish "the caller's own choice of actor" from "whatever the
ambient environment happened to say." `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3522 passed, 0 failed, 0 error(s)) with this mutation applied to a throwaway checkout of the
PR's head.

**Guard form that survives:** for a value with an `explicit or env or env or default` chain,
add a fixture that (a) supplies an explicit value that is DIFFERENT from whatever the
environment's fallback links would independently produce (set `$USER` to one string, pass
`by=` a distinctly different one), and (b) reads back the specific value from every place the
chain's result is documented to land — the function's own return value, the persisted
column/row, and any event/log payload that also carries it — not just a fact ("an event of
this type fired", "the call succeeded") that would be equally true had the chain fallen
through to its second link instead of honoring the first.

**Found:** `chela/dispatcher.py::acknowledge_blocked_race` (CMX-336, PR #431, rework round 1).
`tests/test_dispatcher_blocked_race_ack.py::test_acknowledge_logs_an_event` passed
`by="liav"` but asserted only that `"blocked_race_ack" in kinds`; no test anywhere in the file
asserted `result["by"]`, `row["blocked_race_ack_by"]`, or the event payload's `by` field
against an explicitly-supplied value. Closed by adding
`test_acknowledge_records_the_explicitly_supplied_by_not_the_env_fallback` (sets `$USER` to
one string, passes a distinctly different `by=`, asserts both the result and the row carry the
explicit one) and `test_acknowledge_event_payload_carries_who_acknowledged_it` (asserts the
event payload's `by` field independently, per [[309|shape 309]] / [[322|shape 322]]'s
"a field/argument is never independently asserted" family — the two are companion gaps on the
same call: one on the VALUE the chain resolves to, one on where that resolved value is
propagated).

**See also:** [[309|shape 309]] and [[322|shape 322]] — this shape's fixtures satisfy "an
event fired" the same way theirs satisfy "a note/log line was emitted", without reading back
the one field/argument that carries the information the guard exists to protect. [[306|shape
306]] — a fallback expression's operands collapsing onto the same identity because a fixture
never supplies values that let them diverge; this shape is the same family but the fixture
never even tries to diverge them — no test ever sets the environment fallback to something
the explicit argument must beat.

**Round 2 — the same gap recurred on the OTHER two stamped fields round 1's fix didn't
reach, plus a sibling gap on the refusal path:** round 1 closed the `by` field specifically.
`acknowledge_blocked_race` stamps four things (`by`, `at`, `note`, `sha`); round 1's own
"Found" section already flagged `note`/`sha` as the un-independently-asserted companions but
the round only fixed `by`. Round 2's judge found exactly that gap live, on both remaining
fields, plus a third, unrelated gap on the same PR:

1. `chela/main.py::cmd_judge_ack_blocked_race` passes `note=args.note` straight through to
   the dispatcher — the only CLI test invoked the subcommand with no `--note` flag at all and
   asserted `note=""`, which is true whether `args.note` was threaded through or the call site
   hardcoded `note=""` regardless of the flag. Judge mutation:
   `note=args.note` → `note=""`; suite stayed green (3526 passed) because no CLI test ever
   supplied `--note` with a real value and asserted it reached the mocked dispatcher call.
   Closed by adding `--note "already shipped, safe to ack"` to
   `test_cmd_judge_ack_blocked_race_cli_reaches_the_dispatcher`'s argv and asserting the mock
   was called with that exact string.
2. The `blocked_race_ack` event payload's `sha` field — the exact same "field never
   independently asserted" gap as `by`, just on a different field of the same payload dict.
   Judge mutation: `"sha": sha` → `"sha": ""`; suite stayed green because the existing payload
   test (round 1's own fix) asserted only `payload["by"]`, never `payload["sha"]`. Closed by
   `test_acknowledge_event_payload_carries_the_acknowledged_sha`, mirroring the `by` test's
   shape for `sha`.
3. A structurally different, sibling gap on the same PR: `cmd_judge_ack_blocked_race`'s
   refusal branch (`sys.exit(1)` when `result["ok"]` is falsy) had no test at all — every CLI
   test for this subcommand only drove the success path. Judge mutation: `sys.exit(1)` →
   `sys.exit(0)`; suite stayed green because no test asserted the exit code on a refused
   acknowledgement. This is not the same shape as the field-fallback gap above — it's an
   entire *branch* with zero coverage, not a value silently replaced within a covered branch —
   but it was found by the same round on the same function's CLI wrapper, so it's recorded
   here rather than opening a fourth catalog entry for "an operator CLI's refusal path has no
   test at all," a shape general enough it likely already recurs elsewhere uncatalogued.
   Closed by `test_cmd_judge_ack_blocked_race_cli_exits_nonzero_on_refusal`.
4. A fourth, ALSO structurally different gap on the same function's write itself: the
   docstring above `acknowledge_blocked_race` claims "CAS on `judge_state`/`judge_sha`
   together" over a single SQL statement, `WHERE task_id=? AND judge_state=? AND judge_sha IS
   ?` — both columns are meant to be independent discriminators, so a concurrent judge re-run
   that changes EITHER one between the read and the write must refuse the acknowledgement. The
   only concurrency test in the file (`test_acknowledge_is_scoped_to_the_current_judge_sha`)
   constructs a stale read that disagrees with the real row on `judge_sha` alone — its
   `judge_state` is identical throughout — so it cannot tell a `WHERE` clause that checks both
   columns apart from one that checks `judge_sha` alone and never touches `judge_state` at
   all. Judge mutation: widen the state clause into a tautology while leaving the sha clause
   untouched — `AND judge_state=?` → `AND (judge_state=? OR 1)`; the existing sha-only fixture
   still refuses under this mutation (its sha still disagrees), so the suite stayed green
   (3526 passed) with `judge_state` doing nothing. This is a THIRD distinct shape from the
   two above: not a value silently replaced (items 1-2) and not an untested branch (item 3),
   but a multi-column CAS predicate where every fixture happens to move the same one column,
   so the OTHER column is never the sole point of disagreement between the stale read and the
   real row. Closed by `test_acknowledge_is_scoped_to_the_current_judge_state_not_only_sha`,
   which holds `judge_sha` IDENTICAL between the stale read and the real row and varies only
   `judge_state` (a fresh judge re-run resolving the SAME commit to a DIFFERENT verdict — the
   exact concurrency window the docstring describes). **Guard form that generalizes:** for an
   N-column CAS/optimistic-concurrency `WHERE` clause, one fixture per column is not enough if
   every fixture also lets a different column disagree at the same time — each column needs
   its own fixture where it is the ONLY column that disagrees, every other column held
   identical between the stale read and the real row.

**Round 2 lesson:** when a round's own "Found" section explicitly names sibling fields that
share the exact shape being fixed (see round 1's "no test anywhere in the file asserted
`result["by"]`, `row["blocked_race_ack_by"]`, or the event payload's `by` field" — that
sentence already named `note` and `sha` as the same family, just not yet exercised), fix all
of them in the same round instead of the one the judge's specific mutation happened to name —
the next round will find the others regardless, at the cost of another full rework cycle. Item
4 above is the reminder that "the same family" doesn't cover everything on a function this
small: a single write can carry a field-fallback gap, an untested branch, AND an
under-exercised compound predicate all at once, each needing its own fixture shape.
