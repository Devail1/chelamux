## 103b. An edge-triggered diff is tested only on its FIRST edge, so the baseline advance is dead

**Assertion form:** a loop pushes a frame when a snapshot differs from the previous one
(`if cur != prev: yield frame` … `prev = cur`). The test primes the loop, changes the state
once, and asserts the next frame is the change. It passes, and its docstring says the frame
fires "when the set changes".

**Why that doesn't guard the edge trigger:** the first change differs from the primed
baseline whether or not the baseline ever moves. The `prev = cur` line only matters on the
tick AFTER the change, and the test stops before that tick. Delete the advance and the loop
re-sends the same frame on every tick for as long as the state stays changed. The client then
re-fetches every poll interval, forever. The suite stays green because nothing reads a second
frame.

The same verdict found the shape twice more in the client:

- **A reset that is only ever tested from a clean state.** `run()` clears the last refused
  reason (`_flash = ''`) before each decision. The only test drove ONE refused decision and
  checked the reason appeared. Nothing then made a second decision with a reason already on
  screen, so `_flash = _flash` (never clear it) left a stale error on the sheet after a later
  success, and the suite stayed green.
- **A computed attribute next to an asserted one.** The pill test asserted its
  `textContent` ("2 pending approvals") but never its `aria-label`. A constant label
  (`'Pending approvals'`) took the count away from screen readers and nothing went red.

**Mutation that defeats it:** `prev_ovr = cur_ovr` → `prev_ovr = prev_ovr` (CMX-103,
`chela/dashboard/app.py::_sse_stream`). Suite green.

**Guard form that survives:** drive the loop at least one tick PAST the edge with the state
unchanged, and assert that tick yields no frame (here a keepalive, because the fake
`monotonic` jumps past the keepalive interval on every call). Then change the state back and
assert that a second frame does fire. In general a "reset before X" or "advance after X" step
needs a test that runs X twice, with the second run starting from the state the first one
left behind.
