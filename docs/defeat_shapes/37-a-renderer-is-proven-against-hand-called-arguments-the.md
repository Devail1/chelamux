## 37. A renderer is proven against hand-called arguments; the onclick attribute a real click compiles is never run

**Assertion form:** a dashboard surface has two halves — a render function that paints a
control with an `onclick="chela.someHandler(this)"` attribute, and the handler itself, which
does something a user is meant to see (open a modal, toggle a class, navigate). The test
suite proves each half in isolation: one test calls the render function and reads the
rendered markup/attribute string back (`assert.match(el.getAttribute('onclick'),
/chela\.someHandler/)` or just inspects the HTML the renderer produced); a separate test
calls the handler function directly with a hand-picked argument and reads ITS output back.
Both are real DOM tests, not source greps, and both pass. Neither ever actually clicks the
control and lets the two halves run as one chain.

**Mutation that defeats it:** break the joint between them — the lookup that turns a click's
`this` into the handler's real argument (kanban.js's `openTaskModalFromCard`: `const idx =
Number(el.dataset.kidx); const card = _kanbanCardIndex[idx]; if (card)
openTaskModal(card);`), or the effect that makes the result actually visible (util.js's
`showModal`: `el.classList.add('active')`). The renderer-half test still finds the onclick
attribute string intact (its literal text never changed) and still passes. The handler-half
test still calls `openTaskModal(item)` directly with its own hand-built `item` and still
renders the right title (it never went through the lookup that just broke). A user clicking
the actual card gets nothing — or the wrong task's data — and nothing in the suite notices.

**Why this is distinct from shape 7 ("two callers, one guarded"):** shape 7 is the same
function reached from two call sites, one exercised and one not. This shape is a single
click's ENTIRE chain — attribute, lookup, handler, and visible effect — with a test on each
end and nothing spanning the middle. Fixing shape 7 (add a test at the second call site)
does not close this: both ends can already be "covered" and the joint between them still be
silent.

**Guard form that survives:** drive the actual click. Read the rendered control's `onclick`
attribute off the REAL DOM and execute it — `new Function('chela',
el.getAttribute('onclick')).call(el, window.chela)` (jsdom does not run inline `onclick=` on
a dispatched click event without `runScripts:"dangerously"`, so a literal
`el.dispatchEvent(new MouseEvent('click'))` silently proves nothing here — see
tests/decisions.test.mjs's WIRING GUARD note) — then assert the effect a user would actually
see: a visibility class flipped, the right content rendered, not just that the handler
function was reachable. `tests/js_helpers/dashboard_dom.mjs`'s `clickOnclick()` is the one
copy of this idiom now; every new click-chain guard should use it instead of hand-rolling the
`new Function(...)` call site again.

**A second, related gap the same PR closed:** the jsdom bootstrap needed to drive any of
this (JSDOM construction, the `globalThis` property-define loop — `navigator` is
getter-only from node 21, so plain assignment throws — matchMedia/canvas/fetch stubs, the
browser-faithful `main.js`-first import order) had been hand-copied into 8+ nearly-but-not-
quite-identical `before()` blocks across `tests/*.test.mjs` before this file existed
(`grep -rc 'new JSDOM(' tests/` — 29 files, 32 occurrences). Each copy was a chance to get
one line subtly wrong (a missing `TERMINALS_ENABLED`, a `fetch` stub that doesn't special-
case `/api/agents/context`) and silently fall back to testing component internals instead of
the real boundary — which is exactly how this shape recurs: writing a NEW click-chain guard
from scratch is more work than reaching for a shared, correct-by-construction bootstrap, so
the narrower internals-only test is what gets written instead. `bootDashboardDom()` (same
file) is that shared bootstrap; several of the 8+ copies were migrated onto it in the same PR
as a demonstration, with zero behavior change (every migrated file's own pre-existing
assertions still pass).

**Found:** the kanban-card -> task-modal click chain (`chela/dashboard/static/js/kanban.js`'s
`openTaskModalFromCard` -> `taskmodal.js`'s `openTaskModal` -> `util.js`'s `showModal`) had a
renderer-half guard (`tests/kanban_flatten.test.mjs`, reads the card's rendered HTML/onclick
attribute) and a handler-half guard (`tests/taskmodal_render.test.mjs`, calls
`openTaskModal(item)` directly) — CMX-290, measured against three same-day PRs (CMX-279,
CMX-287, CMX-288) that each shipped a new dashboard surface (nav default view, a settings
modal, a decisions modal) with the internals thoroughly proven and the click that reaches
them from a real user's mouse never driven. `tests/kanban_task_modal_wiring.test.mjs` closes
it for this one chain, using `templates/index.html`'s real `#work-board`/`#modal-task`
markup (`sliceTemplate()`, not a hand-typed fixture) so a future template/JS id drift shows
up here too.
