## 51. An index-based lookup is tested with a fixture of exactly one item, so the correct index and index 0 are the same number

**Assertion form:** a guard proves a click resolves through an index — the clicked element
carries `data-kidx="N"`, a handler reads that `N` off `el.dataset`, and uses it to index back
into a parallel array built at render time (kanban.js's `openTaskModalFromCard`: `const idx =
Number(el.dataset.kidx); const card = _kanbanCardIndex[idx];`). The test renders exactly one
card, clicks it, and asserts the modal shows that card's own content. The assertion is real,
the click is real (not a hand-called function — see shape 50, which this guard was written to
close), and it passes.

**Mutation that defeats it:** replace the lookup's variable index with the literal `0`
(`_kanbanCardIndex[idx]` → `_kanbanCardIndex[0]`). With a single-card fixture, the clicked
card's real index is *always* 0 — there is no card at any other position to reveal that the
lookup stopped reading `el.dataset.kidx` at all. The mutated code returns the exact same
object the correct code would have, for the one input the fixture ever supplies. `chela judge`
found this live on CMX-290: `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3132
passed) with the mutation applied.

**Why this is distinct from shape 50:** shape 50 is about the *chain* being driven at all —
attribute → lookup → handler → visible effect — versus each half being proven only in
isolation. This shape assumes the chain IS driven (a real click, through the real attribute)
and shows that driving the chain is still not enough if the *value carried through it* can't
distinguish "the lookup ran" from "the lookup was skipped and a constant stood in for it".
Closing shape 50 is necessary for this guard to exist; it is not sufficient for this guard to
be unbeatable.

**Guard form that survives:** render at least two items before the one under test, so the
item under test's real index is provably non-zero, and assert that non-zero-ness as a setup
precondition (`assert.notEqual(card.dataset.kidx, '0', ...)`) so a future refactor that
changes render order can't silently reintroduce the same gap. Any lookup keyed by a
render-order position (an array index, an incrementing counter, an enumerate() offset) needs
this — a fixture of one is not a fixture, it's a constant with extra steps.

**Found:** `tests/kanban_task_modal_wiring.test.mjs`'s wiring guard (CMX-290) rendered a single
`recent_runs` card, so `_kanbanCardIndex[idx]` and `_kanbanCardIndex[0]` were indistinguishable
for the only click the test ever made. Closed by rendering a decoy `open_tasks` card first
(Open lane renders before Done in `_KANBAN_BUCKET_ORDER`, claiming `data-kidx="0"`) and
asserting the real card's title — not the decoy's — appears in the modal.

**Found again, on the sibling call site the first fix never touched (CMX-290 round 4):** the
fix above closed the shape for the run-backed branch of `_kCard` only. `_kCard`'s OTHER
branch — the backlog card, `kanban.js:170`, `class="kanban-card kanban-card-backlog"
data-kidx="${kidx}"` — emits its own `data-kidx` from textually independent source (this is
shape 7, two callers, composed with this shape). The wiring test added for the backlog
branch in round 3 rendered exactly ONE backlog card, so its real index was again always 0:
`chela judge` mutated the backlog branch's `data-kidx="${kidx}"` to the literal
`data-kidx="0"`, and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3133 passed).
Fixing shape 51 once, on one emitter of a duplicated value, does not fix it on the other
emitter — each independent `data-kidx="${...}"` in the source needs its OWN two-item fixture,
not just the lookup that reads them back. Closed by rendering two backlog cards in one render
and clicking both (same differential-click design as shape 53), so a hardcoded `0` can match
at most one of the two.
