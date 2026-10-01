## 421c. A template and its live updater write the same field, and only the updater is observed

**Assertion form:** a piece of UI state is written in two places — the HTML template that
first renders the element (`_shareBtnHTML` puts the share's mode glyph in the pane's Share
row) and an updater that patches it in place on every change (`_updateShareBtns` rewrites the
same glyph, title and `hidden`). Every test reaches the element through a flow that runs the
updater before it reads anything: open the sheet (the reconcile calls the updater), change the
mode (the change handler calls it), stop the share. The assertions are correct, but the value
they read was always the updater's.

**Mutation that defeats it:** blank the template's copy
(`${mode ? SHARE_MODE_GLYPH[mode] : ''}` → `${''}`). The updater overwrites the blank before
any assertion looks, so the suite stays green. Live, a pane rendered after the share already
exists (a page reload, a wall rebuild, a pane re-added) shows no mode until something happens
to call the updater, and the guest-can-type warning is missing exactly when nothing changed.

**Why this is distinct from [[79|entry 79]]:** in 79 the initial text is replaced by
*different* content that a later test step loads. Here the template and the updater write the
*same* value, so a test cannot tell them apart by reading the result after any flow that calls
the updater. You can only see the template's copy before the updater runs.

**Guard form that survives:** render the template alone, with the state already set and no
updater call, and assert every field it writes, for every value of the state (each mode, and
unshared). Then run the updater on that same element and assert the snapshot is unchanged.
That pins "first render == after update" as one invariant, so a drift in either writer goes
red: a field the template forgets, or one the updater writes differently.

**Found:** CMX-421 rework round 3 (2026-10-01), PR #571 — `tests/share_mode_sheet.test.mjs`
read the pane pill's glyph only after flows that call `_updateShareBtns`. Closed by
`the pane pill carries the mode from its FIRST render, identical to after an update`, which
renders `_shareBtnHTML` directly per mode and compares it with the updater's result.

**Related:** [[421|entry 421]], [[421b|entry 421b]] (same PR).
