## 102. A handler with several trigger listeners is called by hand, so no listener is ever needed

**Assertion form:** one handler (`_kbPin`) is wired to N independent triggers — two
`visualViewport` events, `window` `resize`, a scroller's `scroll`, a `ResizeObserver`, and a
direct call at the end of a render function. The suite's `beforeEach` calls the handler
directly to reset state, and most tests change state and then call the handler again
themselves before asserting. One test fires one real event (`visualViewport` `resize`); the
others never fire theirs.

**Mutation that defeats it:** dead-code any trigger except the one the suite fires
(`if (false) window.addEventListener('resize', _kbPin)`, the same for the scroller's
`scroll` listener, or for the render function's `_kbPin()` call). The handler's math is
untouched, and every test that calls it by hand still sees the right output, so the suite
stays green. On the phone, rotating, scrolling or re-rendering leaves a stale pane height.
Measured: CMX-102 round 1, three wiring mutations plus one held-out survived a 6882-test suite.

**Why this is distinct from shape 69:** shape 69 is ONE listener whose effect the test
reproduces by hand. Here the handler is a single function behind N registrations. Driving one
of them for real proves that one registration and nothing about the rest. And the `beforeEach`
call also hides a missing call site (the render-time `_kbPin()`): state is already correct
before the code under test runs.

**Guard form that survives:** one test per trigger. Each test changes the state the handler
reads (keyboard height, `visualViewport.offsetTop`, the pane's top, the keybar's height,
the agent set), then fires only that trigger's real event (or runs the real render), with no
hand call to the handler afterwards. Assert the output the new state implies, which must
differ from the output before the change. A dead-coded trigger leaves the old output in place
and the test goes RED. For a render-time call site, change the state silently (no event)
before the render, so the render is the only thing that can update it.

**Found:** `tests/keybar_pane_fit.test.mjs` (CMX-102, PR #666, judge round 1).
