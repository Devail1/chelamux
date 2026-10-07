## 18b. A fake clock that never moves during the call hides which side of the call it is read on

**Assertion form:** the code reads a clock to stamp an operation — "the second this capture
BEGAN" (`ActivityGatedCapture`, CMX-18), whose correctness depends on the stamp being taken
*before* the slow call, because the cache compares tmux's whole-second `window_activity`
against it. The test injects a fake clock (`wall=lambda: fleet.wall`) and a fake call
(`fleet.capture`) that returns instantly, and advances the clock only BETWEEN ticks. Inside
any one call, "before" and "after" read the same value.

**Mutation that defeats it:** move the clock read below the call (`pane = capture(); cap_sec =
int(wall())`), or round instead of floor. Every fixture still passes: with a clock frozen
across the call, before and after are identical. Live, a capture that straddles a second
boundary, with output written mid-capture in the second the stamp already reads, is stamped
with the NEXT second, looks older than the capture, and is served stale until the backstop
sweep, 30 s later.

**Guard form that survives:** make the fake call MOVE the clock. Have the capture read the
pane, then write new output (stamped with the current second), then step the clock past a
second boundary before returning, starting from a fractional second (`1005.5`, which also
separates `int` from `round`). Assert the next tick re-captures and relays. Only a clock
read before the call passes.

**Related:** [[342|entry 342]] (two quantities that coincide in every fixture; here the pair
is "clock before the call" and "clock after it").
