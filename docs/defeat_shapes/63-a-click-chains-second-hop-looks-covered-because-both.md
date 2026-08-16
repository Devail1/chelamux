## 63. A click chain's second hop looks covered because both its ends are unit-tested, but nothing dispatches the click between them

**Assertion form:** a multi-hop UI flow — chip click opens a modal, modal renders a list,
clicking a list row drills into a detail view — gets a wiring test for the FIRST hop (chip
click → modal `.active`, closing [[62|shape 62]]) plus thorough pure-function unit tests for
the rendering helpers on both ends of the SECOND hop: the list-row renderer (a status label, a
CSS class) and the detail-view renderer (a line-classifier, five tests pinning its ordering).
Both ends read as "well covered." Nothing in between — a real click on a rendered row,
delegated through the SAME overlay listener the first hop's guard already proved exists — is
ever dispatched.

**Mutation that defeats it:** independently corrupt any link the second hop actually depends on
and the suite stays green:
- comment out the delegated listener registration for the row click (the modal still opens
  fine — hop one's guard is unaffected — but every row is now inert);
- reroute the "close" call to a dead element id, so the one thing that clears the flight-guard
  state the second hop's async load depends on silently stops firing;
- hardcode the per-row render helper's argument instead of passing the row's own field through
  (`statusMeta(f.status)` → `statusMeta('modified')`) — a fixture with only one row, whose real
  status happens to equal the hardcoded value, cannot tell the difference;
- blank a derived-summary call's output (`summaryLabel(state)` → `''`) — the summary function's
  own unit tests all still pass, since none of them render it into the DOM.

Four structurally different failure modes, and a suite whose only click-chain coverage stops at
hop one cannot distinguish "the whole flow works" from any of them — closing hop one raises
confidence about the *entry point*, not about what a user reaches by continuing to interact
past it.

**Why this is distinct from shape 62:** shape 62 is the zero-guard case — no click anywhere in
the chain is simulated. This shape is what a partial fix leaves behind: hop one gets simulated
and closed, which is real progress, but a reviewer (or the fixing agent itself) can mistake
"the chain now has a wiring test" for "the chain is wired," when the test's own assertions never
walk past the first `.active` check. The second hop's two ends being pure-unit-tested elsewhere
makes this easier to miss than a totally uncovered feature would be — both halves individually
look done.

**Guard form that survives:** when a click chain has more than one hop, extend the SAME
wiring test past the first `.active`/state assertion — dispatch a real, bubbling
`click`/`MouseEvent` on the artifact the first hop just rendered (not a hand-called function),
await whatever async load it triggers, and assert the SECOND hop's real DOM output (not a
return value, not a mocked call). Vary the fixture data across rows/branches so a hardcoded
argument at any render call site produces a visibly different (and therefore wrong) result
instead of accidentally matching by coincidence.

**Found:** CMX-299 rework round 2 (2026-08-16), PR #373. Round 1 closed the chip → modal-open
hop (shape 62) with `tests/diff_modal_wiring.test.mjs`. Round 2's judge corrupted: the row
click's delegated listener (`overlay.addEventListener('click', _diffModalClick)` commented
out), `closeDiffModal`'s target id, `_fileListHtml`'s per-row `statusMeta(f.status)` call
(hardcoded to `'modified'`), and `_render`'s `summaryLabel(state)` interpolation (blanked) — four
mutations across the second hop and the modal's own close path — and
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3177 tests) stayed green under each, because
nothing in round 1's test ever clicked a rendered `.diff-file-row` or compared two rows with
different statuses. Closed by extending the same test: a second fixture file with a different
status, a real bubbling click on the first row, and assertions on the resulting
`#diff-patch-view` content and the modal's `.active` state after `closeDiffModal()`.
