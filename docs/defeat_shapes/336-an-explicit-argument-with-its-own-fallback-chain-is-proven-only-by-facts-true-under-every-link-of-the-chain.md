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

**Round 3 — the round 2 lesson recurred verbatim, plus a genuinely new shape on the same
CAS clause:**

1. Round 2 fixed the payload's `sha` field but left `note` and `at` — the other two members
   of the same four-field family (`by`/`at`/`note`/`sha`) round 1's own "Found" section had
   already named — un-independently-asserted. Judge mutations: `"note": clean_note` →
   `"note": ""` and `"at": when` → `"at": ""`; both survived (3529 passed) because no test
   read either field back from the payload, only from the DB column. Closed by
   `test_acknowledge_event_payload_carries_the_note` and
   `test_acknowledge_event_payload_carries_the_ack_timestamp`, mirroring the `by`/`sha`
   tests' shape. This is the round 2 lesson repeating exactly: fixing the one field the
   judge happened to name and leaving its already-identified siblings costs another full
   round each time.

2. A structurally distinct shape on the SAME `WHERE` clause round 2 already hardened for
   `judge_state`: `"WHERE task_id=? AND judge_state=? AND judge_sha IS ?"` uses `IS`
   deliberately — SQLite's `= ?` never matches a bound `NULL`, and a `J_BLOCKED_RACE` row
   with no recorded `judge_sha` at all is the single stuck-est row this feature exists for
   (`_blocked_race_resolved`'s `sha and head and sha != head` also can't fire without a sha,
   so acknowledgement is its only exit). Every fixture in the file set a non-NULL
   `judge_sha`, so `IS` and `= ` were indistinguishable to the whole suite. Judge mutation:
   `judge_sha IS ?` → `judge_sha = ?`; suite stayed green (3529 passed) because nothing ever
   bound `NULL` through that parameter. This is not the field-fallback shape above (no value
   is silently dropped once the row IS matched) and not the compound-CAS shape from round 2
   (both columns are still checked) — it is a comparison OPERATOR whose semantic difference
   from its sibling is invisible on every non-NULL value, so a suite that never happens to
   supply the one input class where they diverge can't tell them apart. Closed by
   `test_acknowledge_matches_a_row_whose_judge_sha_is_null`, which seeds a row with
   `judge_sha=None` and asserts the acknowledgement still succeeds and still stamps the ack
   columns. **Guard form that generalizes:** for any `col IS ?` used deliberately over
   `col = ?` for NULL-safety, at least one fixture must bind `NULL` through that parameter —
   every other value class leaves the two operators behaviorally identical, so a suite built
   entirely from populated fixtures can never distinguish "chosen for a reason" from
   "could have been either."

**Round 3 lesson:** items round 1 explicitly named as siblings-still-to-fix (here: `note`
and `at`, named back in round 1's own "Found" section) do not stop being live findings just
because a later round's judge happened to mutate a different field first — read this file's
own "Found"/lesson sections for the CURRENT task before declaring a round complete, not just
the specific diff the round's verdict quoted.

**Round 4 — a writer/reader pair, each independently guarded, was never chained end-to-end:**
a structurally different shape from rounds 1-3 (no field is silently dropped, no CAS column
goes unchecked) — a value class crosses a module boundary between a WRITER and a READER, each
tested against its own module's fixtures, and the two test files independently happen to pick
different defaults for the same field, so the interaction is never exercised by either.

1. `dispatcher.acknowledge_blocked_race` (the writer) deliberately supports acknowledging a
   row whose `judge_sha` is `NULL` — round 3 closed that with
   `test_acknowledge_matches_a_row_whose_judge_sha_is_null`, which asserts the CAS matches and
   the ack columns get stamped (`blocked_race_ack_sha=None`). `_blocked_race_resolved` (the
   reader, `chela/runtime_truth.py`) is what has to actually honor that stamp — with
   `judge_sha` NULL, `row.get("blocked_race_ack_sha") == sha` reduces to `None == None`.
   Every fixture in `tests/test_runtime_truth.py` that reaches this line (`_blocked_race_row`)
   defaults `judge_sha` to `"deadbeef"`, a non-NULL value chosen independently of round 3's
   writer-side fixture — so the writer's NULL-sha test and the reader's suite never overlap on
   the one value that matters. Judge mutation: `row.get("blocked_race_ack_sha") == sha` →
   `row.get("blocked_race_ack_sha") == (sha or "")`; suite stayed green (3532 passed) because
   no test drove a NULL-sha row through the READER at all, only through the writer in
   isolation. Closed by
   `test_acknowledging_a_null_judge_sha_row_actually_clears_it_from_the_scan`
   (`tests/test_dispatcher_blocked_race_ack.py`), an end-to-end fixture that calls the REAL
   `acknowledge_blocked_race` on a `judge_sha=None` row and then asserts the REAL
   `_blocked_race_scan` returns `{}` — chaining writer and reader through the actual DB row
   instead of proving either one against a fixture the other side never sees.

**Round 4 lesson — and the general shape:** when one invariant spans a writer and a reader in
different files, a test suite can look complete (each side has a fixture for the same value
class — here, NULL) while the interaction between them is never checked, because each file's
fixture defaults are chosen independently and nothing forces them to agree on which value
class they exercise. **Guard form that generalizes:** for any invariant enforced by matching a
value written in one function against a value read by another, at least one fixture must go
through BOTH real functions in sequence (write, then read) rather than asserting each one's
own output shape against a hand-built row — a hand-built "already acknowledged" row is not
proof the writer would ever produce that row for the reader to consume.
