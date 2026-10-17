## 185. A gate wrapped around every registered handler is tested on a hand-picked list of them

**Assertion form:** a security gate is applied at registration time — every
`application.add_handler(...)` call wraps its callback in `_gated(...)` — and the docstring
says "EVERY handler below is behind the gate". The tests pull handlers out of the real
registry by name and drive a stranger's update through *the ones the brief named*: text,
photo, document, the permission-prompt taps, a few commands. Each is checked to do nothing.
The handlers nobody named (the override Approve/Deny button, the `/new` folder browser, the
topic-closed service message) are registered with the same wrapper and no test.

**Mutation that defeats it:** drop `_gated(...)` from one registration the tests don't drive —
`CallbackQueryHandler(_gated(_on_override), …)` → `CallbackQueryHandler(_on_override, …)`.
Suite green, and any member of the chat can now approve a merge override. The obvious repair
has the same hole one level down: a test that walks the registry and asserts every callback
has `__wrapped__` passes for a wrapper that sets `__wrapped__` but never asks the gate, or
calls the callback before it does.

**Guard form that survives:** walk the registry itself (`app.handlers`), not a list of names,
so a handler added later without the wrapper goes red with no new test. For each one, drive a
stranger's update that records every attribute read past the few the gate itself needs, and
assert that NOTHING was read. That checks behaviour, not a marker. Pair it with a positive
control: the operator's identical update must trip the recorder on every handler. Otherwise a
handler that ignores its input would pass vacuously. Give each button handler data it would
act on (its own callback prefix), or it returns early for both users and the control is
empty. Add a floor on the set of handler names found, so the walk can't silently cover zero.
Related: [[28b|shape 28b]], [[329|shape 329]].

**Found:** CMX-185 rework round 1 (2026-10-17), PR #723. A held-out experiment survived, and
the "EVERY handler is behind the sender gate" experiment flipped (SURVIVED, then KILLED).
`_on_override`, `_on_new_cb` and `_on_topic_closed` had no stranger test. Closed in
`tests/test_telegram_senders.py` by
`test_every_registered_handler_reads_nothing_of_a_strangers_update` and
`test_a_strangers_override_approve_tap_decides_nothing_and_is_unanswered`.
