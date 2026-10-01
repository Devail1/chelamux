## 421. A "leaving state X" teardown is tested on only one exit edge

**Assertion form:** a state machine tears something down whenever it LEAVES a state, whatever
the target — `if old == MODE_UNSANDBOXED: self.revoke_unsandboxed(...)` in
`CollabStreamBridge.set_mode` (CMX-421). The test drives exactly one exit edge, the one the
ticket talked about (UNSANDBOXED → view), and asserts the teardown fired. The other exit edges
(UNSANDBOXED → typing) are never taken, so "leaving X tears down" is only proven as "X → view
tears down".

**Mutation that defeats it:** narrow the exit condition to the one tested edge —
`if old == MODE_UNSANDBOXED and new == MODE_VIEW:`. The tested edge still tears down, so the
suite stays green, while every other exit now silently keeps the thing alive (here: the bound
guest keeps a real shell on a non-sandboxed window while the sheet says "Allow typing").

**Guard form that survives:** drive EVERY exit edge out of the state — at minimum the one
whose target is NOT the obvious "safe" one — and assert the torn-down state from the outside
(the mode reads back as the target, the expiry is gone, the next privileged action is refused,
the revoke event fired with that edge's reason). When the edges differ in safety, the
non-obvious edge is the one that matters: dropping to view would be refused anyway by the
view gate, so only the → typing edge proves the override itself was ended.

**Related:** [[66|entry 66]] (one axis of a predicate pinned), [[21|entry 21]] (the sibling that
motivated the ticket is the only one tested).
