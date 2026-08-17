## 74. A delegated click guard is dispatched on the delegation root itself, so `closest()` and a self-only `matches()` resolve identically

**Assertion form:** a click handler is registered on a container (event delegation) and
resolves the actual target element by walking UP from `e.target` with `closest('.the-row')` —
because in production the container's visible area is entirely covered by child elements
(icons, labels, stat text), so a real click's `e.target` is always some descendant of the row,
never the row itself. The wiring test that proves the handler works dispatches its synthetic
`MouseEvent` directly on the row element (`row.dispatchEvent(...)`), because that's the element
the test already has a reference to.

**Mutation that defeats it:** narrow `e.target.closest('.the-row')` to
`e.target.matches('.the-row') ? e.target : null`. In production this drops every real click
(they all land on a child), but the test dispatches its event on the row itself, so
`e.target === row`, and `matches()` returns true for that one case — the one case a self-only
match still handles is exactly the one case the test happens to construct. Every other
assertion in the suite still passes because nothing else exercises the click path with the
event target on a *child* of the row.

**Guard form that survives:** dispatch the synthetic event on a CHILD element of the row —
whichever one covers most of its visible area, or any one of the rendered leaf spans/icons —
not on the row itself. `closest()` still resolves the row correctly from a descendant;
`matches()` on a non-matching descendant returns `null` and the handler silently no-ops. This
mirrors how a real pointer click actually lands: on the rendered pixel (a child), never on the
row element's own (usually zero-height, flex-container) box.

**Found:** CMX-299 rework round 5 (2026-08-16), judge round 4 of PR #373.
`chela/dashboard/static/js/diffpanel.js`'s `_diffModalClick` resolves a clicked file row with
`e.target.closest('.diff-file-row')` because the row is `display:flex` and every visible pixel
belongs to one of its children (`.diff-status-chip` / `.diff-file-path` / `.diff-file-stat`).
`tests/diff_modal_wiring.test.mjs` dispatched its `MouseEvent` directly on the `<li>` row
itself, so `e.target.matches('.diff-file-row') ? e.target : null` (the judge's mutation) still
resolved the row and stayed green — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3185 tests)
passed with the mutation in place. Closed by dispatching the click on
`row.querySelector('.diff-file-path')` instead of the row, in both places the test clicked a
row.
