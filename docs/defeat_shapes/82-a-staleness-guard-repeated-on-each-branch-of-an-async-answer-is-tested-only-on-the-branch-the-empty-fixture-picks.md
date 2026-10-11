## 82. A staleness guard repeated on each branch of an async answer is tested only on the branch the empty fixture picks

**Assertion form:** CMX-82's `refreshLayout()` (`chela/dashboard/static/js/nav.js`) reads
`/api/sidebar/layout` and applies the answer only `if (fresh())`, meaning no local write happened
while the read was in flight. The answer can take one of three branches: a SAVED layout
(`if (st.layout)`), the one-time import (`_migrateLayout`), or "nothing saved". Each branch
carries its own copy of the `fresh()` check. The test for "a slow read that started before a
write never undoes it" ran right after the suite's `beforeEach`, which resets the fake server
to `layout: null`. So the slow read always came back with "nothing saved" and went through the
LAST branch. The test was real and correct for that branch, but it never entered the first.

**Mutation that defeats it:** `if (fresh()) _setLayout(st.layout);` →
`if (true || fresh()) _setLayout(st.layout);` in the saved-layout branch. The full suite stayed
green (6708 tests). No test held a read in flight while the server already held a layout. This
case is [[22|shape 22]] (the same thing on every branch, tested through one), and here the
branch is chosen by the fixture's resting state, as in [[2|shape 2]], not by the input.

**Guard form that survives:** when an async answer splits into branches by what the SERVER
holds, and each branch repeats the guard, write one stale-read test per branch. Set up the
server state that branch needs BEFORE the slow read starts. For the saved-layout branch: write
once so a layout exists, hold the next GET, write again, release the GET, and assert that the
second write is still what renders.

**Found:** CMX-82 judge round 1, PR #649. Guard:
`tests/sidebar_layout_shared.test.mjs` — "once loaded, a slow read of a SAVED layout that
started before a write never undoes it" (red under the mutation above).
