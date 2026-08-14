## 47. An early-return gate's controlling value is only ever driven with the one input that passes it, so dead-coding the gate is indistinguishable from it running

**Assertion form:** a handler starts with an early-return filter on some property of its input
(`if (e.key !== 'Escape') return;`), followed by the logic the filter exists to protect. Every
test that reaches this handler constructs its input with that property already set to the one
value the filter accepts — here, every `KeyboardEvent` dispatched anywhere in the suite that
reaches this listener uses `key: 'Escape'`, because the tests exist to exercise the Escape
behavior. The filter's *other* branch — an input where the property takes any other value — is
never constructed at all.

**Mutation that defeats it:** dead-code the filter's condition (`if (false && e.key !== 'Escape')
return;`) so it never returns early regardless of what the input's property actually is. Every
existing test still only ever sends `key: 'Escape'`, so the mutated line and the original line
produce byte-identical control flow for every input the suite is capable of constructing — the
suite has no way to observe that the filter is gone, because it never sent an input that would
disagree between "filter checks the key" and "filter always lets everything through."

**Why this is distinct from [[35|shape 35]] and [[46|shape 46]]:** shape 35 is a bound clause in
a compound AND whose downstream call *happens* to resolve the same way whether reached or not —
the coincidence lives in a mock's return value. Shape 46 is a gated action that is *structurally*
idempotent against the one state variable the test reads, for any input. This shape has neither
property: the gated logic here (`preventDefault()` + close the modal) is *not* idempotent for a
non-Escape key with the modal open — it would visibly do the wrong thing. The gap is purely that
the suite's *inputs* never varied along the one axis (`e.key`) the filter switches on; nothing
about the downstream logic's coincidental behavior is involved at all.

**Guard form that survives:** for any early-return filter on an input property, add a test that
constructs an input with that property set to a value **outside** the accepted set — here, a
`KeyboardEvent` with `key: 'a'` instead of `'Escape'` — while placing the system in the state
where the *downstream* logic, if reached, would produce an observably different (and wrong)
result (the modal open, so an un-gated fallthrough would close it and call `preventDefault()`).
Assert the downstream effect did **not** happen. A suite that only ever drives the accepted
value can never distinguish "the filter ran and matched" from "the filter doesn't exist and
every input happens to be the one value that would have matched anyway."

**Found:** CMX-288 rework round 5 (2026-08-14), PR #359. `chela/dashboard/static/js/decisions.js`'s
module-scope `document` keydown listener (no removal path — it sees every keystroke in the
dashboard forever, including ones typed into `#decisions-search`, which `openDecisionsMenu()`
itself autofocuses) opens with `if (e.key !== 'Escape') return;`. Round 4 closed a sibling gate
one line below it (shape 46, the `.classList.contains('open')` check) but every KeyboardEvent in
the entire suite — across `decisions.test.mjs` and the three other files that import
`decisions.js` — dispatched `key: 'Escape'`, zero exceptions. The judge dead-coded the filter to
`if (false && e.key !== 'Escape') return;` and all 3132 tests stayed green: dead-coding it makes
any keydown close the modal and swallow the keystroke via `preventDefault()` — the first
character typed into the autofocused search box would shut the inbox. Closed by a new test that
dispatches `key: 'a'` with the modal open and asserts the modal stays open and
`dispatchEvent`'s return value stays `true` (not prevented).
