## 62. A fixture mounts the exact branch (or order) an invariant claims, but the test asserts an adjacent property instead of the invariant itself

**Assertion form:** a test drives the fixture into precisely the state a comment claims
matters — the fallback branch of a conditional render, or a within-collection order a
constant is supposed to encode — but then asserts something that sits next to that state
rather than the state itself. The fixture is right; the assertion is aimed one property
over. Two independent instances landed in the same PR:

1. **Fallback branch mounted, its own output unread.** `kanban.js`'s `_kCard` renders a
   parked card's reason chip with a stated fallback: `card.reason ? ... : '<span
   class="kanban-parked-reason">🔒 parked</span>'`. `tests/kanban_flatten.test.mjs`'s test 6
   already mounts the fallback (`reason: null`) — but its only assertion is that the card
   has **no Promote button**, a fact true of every parked card regardless of the reason
   chip's content. The chip that branch exists to render was never read back.
2. **A comment-only order, never asserted.** `kanban.js`'s `_KANBAN_BUCKET_ORDER` states in
   its own header comment that it fixes the within-lane concat order — "`'backlog'` before
   `'parked'`" — but every existing test only checks bucket **membership** (which lane a
   card lands in, via `laneOf()`'s completeness guard and per-class `querySelector` checks).
   Membership survives the two bucket keys being swapped; only relative DOM position would
   catch it, and nothing read that.

**Mutation that defeats it:** (1) empty the fallback span's text
(`'<span class="kanban-parked-reason">🔒 parked</span>'` -> `'<span
class="kanban-parked-reason"></span>'`); (2) swap two adjacent entries in the order array
(`['backlog', 'parked', ...]` -> `['parked', 'backlog', ...]`). Both mutations still parse,
still route every card into its correct lane and class, and leave every existing assertion
— Promote-button absence, per-lane membership, per-card class — passing unchanged. `chela
judge` found both live on CMX-298: `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3169 passed) with either mutation applied in isolation.

**Why "the fixture is already correct" isn't the same as "the invariant is guarded":**
getting the fixture into the right state is necessary but not sufficient — a reviewer
skimming the test sees `reason: null` and assumes the fallback is "covered" because it's
*reached*, and sees a bucket-order comment next to a test that renders parked cards and
assumes the order is "covered" because both bucket keys are *exercised*. Reached and
exercised are not asserted. Every other assertion in the same test file (test 5's
`reason: 'waiting on fixtures'` case, the per-lane membership checks) is computed from a
*different* input or property than the one the fallback/order claims, so nothing forces the
literal claimed behavior to be checked.

**Guard form that survives:** for a stated fallback, read back the exact element/text the
fallback branch is supposed to produce — not just an unrelated fact that happens to hold
for every card in that branch. For a stated order, render two-or-more items that land in the
*same* lane from *different* buckets and assert their relative DOM position
(`[...container.children].findIndex(...)` for each, then compare indices) — membership
checks alone can never distinguish `[A, B]` from `[B, A]`.

**Found:** CMX-298 rework round 1 (2026-08-16), PR #372 — closed by extending
`tests/kanban_flatten.test.mjs` test 6 to assert the reason-less parked card's own
`.kanban-parked-reason` textContent matches `/🔒\s*parked/`, and adding a new test 7 that
renders one backlog card and one parked card in the same payload and asserts the backlog
card's DOM index precedes the parked card's within `.kanban-col-backlog .kanban-cards`.
