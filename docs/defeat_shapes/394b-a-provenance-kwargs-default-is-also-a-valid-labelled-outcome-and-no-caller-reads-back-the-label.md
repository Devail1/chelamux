## 394b. A provenance kwarg's default is also a valid, labelled outcome, and no caller reads back the label

**Assertion form:** a shared helper takes a "who is calling" kwarg with a default
(`inbox.register(wid, source="watch")`), and each non-default caller passes its own
(`source="dashboard"` from the subscribe route, `source="autolaunch"` from `wake()`). The
helper's branch on that kwarg is tested by calling the helper with the value **by hand**
(`inbox.register("@45", source="dashboard")`), so the branch looks covered. The production
callers are never driven with an assertion on the label they produce. A second entry point that
reaches the same announcement through a different function (`watch(wid, by=...)` besides
`register(by)`) had no test at all, because the helper-level tests all go through `register`.

**Mutation that defeats it:** drop the kwarg at a call site (`inbox.register(wid,
source="dashboard")` → `inbox.register(wid)`). What makes this shape nastier than shape 338 is
that the default is **not a no-op**: with the previous holder still live, the default `"watch"`
path *also* announces an `orchestrator.moved`, only labelled `taken_over`. Any test asserting
only "the dashboard takeover still announced a move" stays green, and the Feed tells the operator
that the window claimed the pin itself when it was really the dashboard. `watch(by=)` →
`moved = None` and the Feed's `'orchestrator.moved': 'lifecycle'` → `''` survived the same way
(the second as shape 358). The suite stayed green: 4361 passed, 0 failed.

**Guard form that survives:** drive each production caller (the HTTP route, `wake()` with only
the spawn stubbed, `watch(..., by=...)`) against a store whose previous holder is **still live**,
so that the default arm would announce too. Then assert the exact `(old, new, reason)` tuple
read back from the event log, not "a move exists". The discriminating field is the label, and
the label is the only thing the dropped kwarg changes. For the second entry point, pair it with
a control where the same call from the pin holder itself announces nothing.

**Related:** shape 338 is the single-call-site version of this, with a kwarg whose absence
disables a feature. Shape 07 is the multiple-callers version. Here, absence silently *relabels*
the outcome instead, so presence-only assertions can't see it.

**Found:** CMX-394 (PR #546, judge round 3). The fix is in `tests/test_api_orchestrator.py`,
`tests/test_orchestrator_autolaunch.py`, `tests/test_orchestrator_pin.py` and `tests/feed.test.mjs`.
