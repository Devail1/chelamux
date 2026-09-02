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
