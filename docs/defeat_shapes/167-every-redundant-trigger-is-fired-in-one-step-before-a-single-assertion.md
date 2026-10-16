## 167. Every redundant trigger is fired in one step before a single assertion, so no one trigger's wiring is load-bearing

**Assertion form:** a handler is wired to several events that each mean "try now"
(`visibilitychange`, `pageshow`, `online` in the CMX-167 reconnect shim). The test for "hidden:
nothing until the page is visible, then reconnect" makes the page visible and then fires all of
them in a row: `setVis('visible'); dispatch('pageshow'); dispatch('online')`, then asserts
`reloads.length === 1`. The assertion reads as "visibilitychange brings the terminal back", but
any ONE of the three listeners is enough to pass it.

**Mutation that defeats it:** unwire one trigger:
`document.addEventListener('visibilitychange', kick)` →
`document.addEventListener('visibilitychange', function () {})`. `pageshow` and `online`
still fire in the same step and reconnect, so the count is still 1. In production a tab that
comes back from the background (the case that fires only `visibilitychange`, with no bfcache
`pageshow` and no network change) never reconnects, which is the bug the PR exists to fix.

**Why this is distinct from shape 362c:** 362c tests a multi-flag scan through only ONE of its
flags, so the others are dead. Here every trigger IS exercised, all at once, and that is what
hides each one: the redundancy that makes the feature robust also makes each wire unobservable.
The "exactly one" dedupe check in the same test makes this worse, because it needs all the
triggers fired to be meaningful, so the author has a reason to fire them together.

**Guard form that survives:** one test per trigger, each firing ONLY that trigger and asserting
the reconnect happened at that moment (`reloads == [t]`). Change the precondition without any
event (`setVisSilently('visible')`) and assert nothing happens, so the triggers are proven to be
the cause rather than a timer that would have fired anyway. Keep the dedupe check, but fire the
extra triggers AFTER the first assertion (`tests/term_reconnect.test.mjs`: "a socket closed while
hidden waits for visibilitychange", "hidden then visible: pageshow alone reconnects", "becoming
visible with no event at all never reconnects").
