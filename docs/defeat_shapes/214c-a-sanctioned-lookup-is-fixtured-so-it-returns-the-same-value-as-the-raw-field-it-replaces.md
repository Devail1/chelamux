## 214c. A sanctioned lookup is fixtured so it returns the same value as the raw field it replaces

**Assertion form:** code must reach a value through a checked lookup (`inbox.run_wid(run)`,
which drops a window id recorded in an older tmux epoch) instead of reading the raw field
(`run["window_id"]`). The test builds its rows with the check switched off
(`window_epoch: None`), so the real lookup falls straight through to the raw field. Both
paths answer `@1`, and the test asserts `@1`.

**Mutation that defeats it:** replace the lookup with the raw field. CMX-214
(`chela/fleet.py`, `pause_all`): `wid = inbox.run_wid(run)` became
`wid = run.get("window_id")`, and every pause test stayed green, because no row ever made the
two disagree. The same round showed two neighbours of this shape: the recording fakes kept
Escape presses and messages in **separate lists**, so swapping their order was invisible
(see [[214b|shape 214b]]), and the `--hold-status` test asserted `"... ago by" in out`, a
prefix that ends one word before the value, so `by {state['by']}` → `by ?` survived.

**Guard form that survives:** fake the lookup and make it DISAGREE with the raw field in
every fixture by default (rows carry `window_id: "@row1"`, the fake `run_wid` answers `@1`),
so any code that bypasses the lookup targets `@row1` and every test goes red. Record every
side effect in ONE ordered log and assert the whole sequence
(`[("esc", "@1"), ("msg", "@1", PAUSE)]`), and assert a rendered line in full, value included
(`"Fleet PAUSED 2m ago by @42 (--all): agents paused"`). See `tests/test_fleet_pause.py`.

**See also:** [[93|shape 93]] (an override tested where the default already resolves to the
same path) and [[05|shape 05]] (a source constant asserted instead of the rendered value).
