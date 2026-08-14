## 46. A guarded branch's only-checked effect is idempotent on the branch's own gate, so the assertion can't tell "gate skipped" from "gate ran and did nothing new"

**Assertion form:** a conditional gates a side effect (`if (cond) { doA(); doB(); }`), and the
test meant to prove the gate exists drives the case where `cond` is false, then asserts (1) the
call doesn't throw and (2) some state stays at the value it already had. Both of those
assertions are also exactly what happens if the gate is *deleted* and `doA()`/`doB()` run
unconditionally — as long as `doA()`/`doB()` are themselves idempotent when the state they'd
change is already at rest (removing a class that isn't present, closing something already
closed). The state-based assertion is satisfied identically whether the gate ran or was never
there, because nothing about the *gated* action distinguishes "skipped" from "ran and was a
no-op."

**Mutation that defeats it:** delete the gate's condition, leaving the action(s) unconditional.
Every assertion the test makes — no throw, state unchanged — still holds, because the actions
were already no-ops in this fixture's starting state. The gate's *actual* reason to exist (here:
an `Escape` keydown listener registered at module scope on `document`, with no removal path,
sees every Escape in the dashboard forever — the gate is what stops it from reacting when this
particular feature isn't even open) has an effect nothing in the test reads: `e.preventDefault()`
was called on an event whose default action belonged to whatever else was actually focused, and
the test never checks `event.defaultPrevented` or the boolean `dispatchEvent(...)` itself
returns for a cancelable event.

**Why this is distinct from [[35|shape 35]]:** shape 35 is a downstream *call* whose return
value coincidentally agrees whether reached or not (the fixture just happens to produce the
same answer either way — a property of the test data). This shape doesn't depend on any
coincidence in the fixture at all — the gated action is *structurally* idempotent against the
one state variable the test happens to read, for any input, because "remove a class that isn't
there" is a no-op by definition. The fix isn't "pick fixture data that disagrees between the
branches" (there's no such data here); it's "read a different, side-channel signal that the
gated action *does* have — one nothing in the test previously looked at."

**Guard form that survives:** identify what the gated action affects *beyond* the state the
happy-path test already checks, and assert on that instead of (or in addition to) the shared
state. Here: dispatch the keydown as `cancelable: true` and assert on `dispatchEvent`'s own
return value (`false` iff some handler called `preventDefault()`) — this is `true` only when the
gate actually stopped `preventDefault()` from running, and goes `false` the instant the gate is
dropped, regardless of what the modal's own open/closed state does.

**Found:** CMX-288 rework round 4 (2026-08-14), PR #359. `Esc is a no-op when the decisions
modal is already closed` asserted `assert.doesNotThrow(...)` plus `isOpen() === false` after
dispatching Escape on a closed modal. The judge dropped the handler's
`m.classList.contains('open')` check (`if (m && m.classList.contains('open'))` →
`if (m)`), so `hideDecisionsMenu()` now runs unconditionally on every Escape — but removing an
`'open'` class that was never present is itself a no-op, so `isOpen()` still read `false` and
nothing threw; all 3127 tests stayed green. Closed by dispatching the event as `cancelable:
true` and asserting the return value (`true` = not prevented) instead of the modal's own state,
which reddens the instant the gate stops discriminating "closed, skip" from "closed, ran
anyway."
