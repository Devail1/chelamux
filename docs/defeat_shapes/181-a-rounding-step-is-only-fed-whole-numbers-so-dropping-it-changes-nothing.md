## 181. A rounding step is only fed whole numbers, so dropping it changes nothing

**Assertion form:** a renderer formats a number through a rounding step
(`${Math.round(f.percent)}%`), and every test pins the exact rendered line, byte for byte:
`'ctx ▰▰▰▰▱▱▱▱▱▱ 42% · @7'`. An exact-string match reads as the strictest possible check.

**Mutation that defeats it:** delete the rounding: `${Math.round(f.percent)}%` →
`${f.percent}%`. Every fixture's percent is `42`, a whole number, and rounding a whole
number returns it unchanged. The step has a fixed point, and the fixtures sit on it. The
rendered line is identical, so every exact match still passes. Swapping in `Math.floor`,
`Math.ceil` or `Math.trunc` survives the same way. This is [[02|shape 2]] for a transform,
not a threshold: the input never moves to a value where the step matters.

**Why it looks covered:** the fixture value comes from real data (`84000 / 200000 = 42`)
and the assertion is an exact match. Neither says that the input is one the step leaves
unchanged.

**Guard form that survives:** feed the step values it actually changes, and pick them so
that each plausible substitute gives a different output: `41.6 → 42%` (catches removal
and `floor`/`trunc`), `41.4 → 41%` (catches `ceil`), `44.5 → 45%` (pins half-up, catches
round-half-even). The same applies to any normaliser (trim, clamp, lowercase,
`toFixed`): at least one fixture must be one it changes.

**Found:** CMX-181 judge round 1, PR #750. Fixed in `mods/chela-hooks/hooks/status.test.ts`
("the context percent … is drawn rounded as …").
