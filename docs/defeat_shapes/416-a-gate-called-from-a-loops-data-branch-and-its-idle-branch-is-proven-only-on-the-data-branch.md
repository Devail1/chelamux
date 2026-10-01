## 416. A gate called from a loop's data branch and its idle branch is proven only on the data branch

**Assertion form:** a pump loop reads from a socket with a timeout. One gate call sits on the
branch that handles a received frame ("may this frame go out?"). A second call to the same
gate sits on the idle-timeout branch, so a bad state is caught even when nothing arrives
("re-verify while idle, end before the next frame"). The guards drive the real loop with a
scripted socket, but every script is a run of frames: flip the state, deliver a frame, assert
the frame was dropped and the session ended.

**Mutation that defeats it:** make the idle-branch call a no-op (`why = self._output_refusal()`
becomes `why = None`). The guards stay green, because the very next scripted frame reaches the
data-branch call, which refuses it and ends the session. The end result looks identical. What
the mutation removed is *when* the session ends: it now waits for the next frame instead of
ending on the idle tick, which is the exact leak window the idle check was written to close.
(CMX-416, PR #567.)

**Guard form that survives:** script an idle tick (`receive` returns `None`) right after the
flip, then put a *tripwire* in the script after it: a callable that records "the loop read
past the idle tick", followed by a frame. Assert the tripwire never fired, as well as that the
session ended. Pair it with a positive control: idle ticks over a good state end nothing and
the frames around them still stream. Whenever the same gate is called from two branches,
each call needs a fixture that reaches it with the other branch unable to catch the fault
first.

**Why this is distinct from [[339|shape 339]]:** shape 339 is one helper at many identical
call sites where only one input distinguishes it from a naive replacement. Here the two call
sites are on different branches of one loop, and the second one is shadowed: whatever the
first branch would miss, the second branch catches one iteration later, so the end state
cannot tell them apart. Only the *timing* (what the loop consumed before ending) can.
