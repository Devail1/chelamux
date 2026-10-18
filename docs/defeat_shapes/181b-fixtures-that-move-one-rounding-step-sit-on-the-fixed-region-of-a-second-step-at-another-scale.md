## 181b. Fixtures that move one rounding step sit on the fixed region of a second step at another scale

**Assertion form:** one input goes through two rounding steps at different scales: the label
rounds `percent` to a whole number (`${Math.round(f.percent)}%`), the bar rounds
`percent / 10` to a cell count (`Math.round(percent / 10)`). After [[181|shape 181]] the
tests feed fractional percents (`41.6`, `41.4`, `44.5`) and pin the exact rendered line,
so the label's rounding is caught under every substitute.

**Mutation that defeats it:** `Math.round(percent / 10)` → `Math.floor(percent / 10)`.
Every fixture divides to `4.16`, `4.14`, `4.45`, or `4.2`: all below the half cell, where
round and floor agree. The fixtures were chosen to straddle the LABEL's half step (`.5` of
one percent) and none straddles the BAR's (`5` percent). The exact-match line is the same.

**Why it looks covered:** the same fixture feeds both steps and the earlier fix was
explicitly about rounding, so the rows read as "the rounding cases". A fixed point is per
step, not per input: a value that moves the fine step can be a fixed point of the coarse one.

**Guard form that survives:** for each rounding step, name its own half step and put rows
either side of it, in that step's units: `45 → 5 cells` and `44.9 → 4 cells` (catches
floor/trunc and an off-by-one threshold), `41.4 → 4 cells` (catches ceil), `95.4 → 10`.
The same holds for a timestamp floored from ms to s (`resetsAt` at `.750Z`, not `.000Z`).

**Found:** CMX-181 judge round 2, PR #750. Fixed in `mods/chela-hooks/hooks/status.test.ts`
("the context percent … is drawn as … cells and …").
