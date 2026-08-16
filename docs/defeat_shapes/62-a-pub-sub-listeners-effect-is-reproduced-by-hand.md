## 62. A pub/sub listener's effect is reproduced by hand instead of the listener producing it

**Assertion form:** a module exposes a subscribe/publish pair — `onXChange(fn)` registers a
listener, some async action (`_apply(data)`) fires every registered listener when state
changes. A consumer module registers a real listener at load time whose body calls a render
function. The test drives the real async action (`await doTheThing()`) to change state, then
calls the SAME render function itself, by name, with fresh arguments, and asserts on the
result.

**Mutation that defeats it:** dead-code the listener body (`if (false) render(...)`). The
async action still resolves, still updates the shared state, still calls every registered
listener — the listener call happens, its body just does nothing. The test's own direct call
to the render function runs on completely unaffected code and paints the correct result
regardless, so the suite stays green while a real user's click — which has no second,
independent call to the render function standing behind it — updates nothing.

**Why this is distinct from shape 50 ("a renderer is proven against hand-called arguments,
the onclick attribute a real click compiles is never run"):** shape 50's joint is a DOM
`onclick=` attribute string that has to be *read back and compiled* to prove a real click
reaches the handler at all — the render half and the click-compilation half are two separate
things a test can each prove without the other. This shape has no DOM attribute in the middle
at all: the "wiring" IS the subscribe call itself (`onXChange(fn)`, evaluated once at module
load), and the async action already invokes the registered `fn` directly, no attribute
compilation needed. The gap here isn't "did a click reach the handler" — it's "did the test
let the REGISTERED callback be the thing that produces the effect, or did it silently
substitute its own hand-called invocation of the same function for the callback's". A test
can close shape 50 correctly and still fall into this one on a plain listener.

**Guard form that survives:** render the initial (pre-change) state ONCE, before triggering
the state change. Then trigger the real async action that is supposed to notify listeners,
with **no further call to the render function anywhere in the test** — not before the
assertion, not disguised as a "let me just make sure" cleanup call either. Assert the
rendered DOM reflects the new state purely off whatever the real listener did. If the
listener body is dead-coded, the DOM still shows the pre-change render and the assertion
fails; if it is wired correctly, the DOM reflects the change with no help from the test.

**Found:** `chela/dashboard/static/js/nav.js`'s `onOrchestratorChange(() => {
renderSidebarAgents(...); if (_detailAgent) renderAgentDetail(); })` (CMX-300, PR #374, judge
round 1) — `tests/sidebar.test.mjs`'s three new role-badge tests each called `await
orchestrator.orchestratorSubscribe(wid)` (which internally invokes every registered listener,
including this one) and then immediately called `nav.renderSidebarAgents(freshRows)` itself,
so the listener's own dead-coded body was never the thing that painted the badge the
assertions read. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3157 passed) with
`if (false) renderSidebarAgents(...); if (false && _detailAgent) renderAgentDetail();`
substituted in. Closed by rendering the row ONCE before subscribing, then asserting the badge
appears (and, on release, disappears) with zero further `renderSidebarAgents` calls in
between — plus a companion file
(`tests/sidebar_agent_detail_orchestrator_wiring.test.mjs`) that closes the listener's SECOND
effect (`renderAgentDetail()` on an already-open detail panel), which needed its own
terminals-OFF boot: with terminals on, `selectAgent` on any window-id'd agent always routes
to the wall (`terminals.js`'s `focusPaneByWid`) and never opens the detail panel at all, so
the two effects of one listener body needed two different boot configurations to observe.
