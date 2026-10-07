## 23c. A scope filter is only rendered with one scope on screen and the filter on "all"

**Assertion form:** a view filters what it renders by a scope the user picks (the Work board's
workflow chip), and the decisions downstream of that filter depend on WHICH scopes are left
(here: every workflow on screen has Linear states ⇒ Linear's columns, else chela's lanes; and
only the picked workflow's cards are shown). The render tests boot the real DOM and assert
the right columns and the right badge on the right card. But every fixture holds ONE
workflow, and the filter stays on its default `all`. Filtering one scope out of one scope is
the identity, so the filter is never exercised.

**Mutation that defeats it:** drop the filter from any one of its call sites — the list of
workflows the column choice reads (`shown = data.workflows.filter(...)` → `data.workflows`),
or the cards put into the columns (`apply(cards)` → `cards`). The single-workflow fixture
renders the same board either way, so the suite stays green. In the field, picking a markdown
workflow next to a Linear one shows Linear's columns (or the reverse), and a filtered board
shows every workflow's cards.

**Guard form that survives:** render with TWO scopes that would decide differently (one
Linear workflow, one markdown) and a card in each, then set the filter to each scope in turn
and assert both the decision (the column set) and the membership (exactly that scope's card
ids). Keep `all` as the control: it must render the mixed outcome. Reset the filter in a
`finally`, since it is module state that outlives the test.

**Found:** CMX-23 rework round 2 (2026-10-07), PR #607 — a held-out experiment survived, and a
self-run probe of the new code paths showed the filter call sites in `renderKanban` were
among those no test could see. Fixed by `the workflow filter picks the board…` in
`tests/kanban_tracker_render.test.mjs`. Same round, same cause (every fixture on the defaults —
[[02|shape 2]]): the Linear team in every test had the default state names, so the
type-based fallback (`Doing` / `Reviewing` / `Killed`) and a configured `states:` name were
never reached; now pinned in `tests/test_linear_workflow_states.py`.
