## 70. A two-value collision guard reads one side rendered and the other side from a hardcoded source snapshot

**Assertion form:** a guard exists to prove two independently-styled surfaces stay visually
distinct — e.g. "role colour A must never equal window-type colour B." The guard correctly
mounts one side in a real, cascaded document and reads it back with `getComputedStyle` (per
[[5|shape 5]]'s fix for a *single* value). But the other side of the comparison is written as
a literal array of hex constants copied out of the stylesheet by hand, instead of being read
from the same cascaded document the first side already uses. The two sides of the comparison
look symmetric in the test body — both end up as colour strings compared with `.includes()`
or `.equal()` — but only one of them is actually observing anything live.

**Mutation that defeats it:** recolour the hardcoded side's real CSS rule to collide with the
rendered side's value (e.g. change `.ar-type.claude { color: #56B4E9; }` to the exact hex the
rendered `.ar-role.orchestrator` badge resolves to). The stylesheet now genuinely produces the
collision the guard's own title claims to forbid — but the guard's hardcoded array still holds
the OLD hex, so the comparison runs old-constant-vs-real-value instead of
real-value-vs-real-value, and the assertion that they differ still trivially holds. A prior,
unrelated `CSS.includes(hex)` presence check on the same stylesheet stays green too, because
the hex literal is still *somewhere* in the file (in a different rule, or a `color-mix()`
alongside it) — nothing about the mutation removes the string, it just moves what renders.

**Guard form that survives:** when a guard's whole point is that two rendered surfaces must
NOT collide, read **both** sides out of the same cascaded document via `getComputedStyle` —
mount fixture markup for the second surface's classes alongside the first's in the one jsdom
instance already built for the first, and compare the two live values to each other. Never let
one side of a collision/distinctness comparison fall back to a literal copied from source,
even when the literal was accurate at the time it was written — it is a snapshot, and the
guard's job is to notice when the snapshot and the live cascade diverge.

**Why this is distinct from [[5|shape 5]]:** shape 5 is one guarded value read from source
instead of rendered. Here the PRIMARY guarded value (the role colour) is already correctly
rendered and read live — the defeat is specific to a *comparison* guard, where the thing being
compared AGAINST is the unrendered half. Fixing shape 5 alone (making sure the guard reads
*a* rendered value) is not sufficient when the guard's claim is about a relationship between
two surfaces; both ends of that relationship need to be live, or the relationship being
asserted isn't the one that actually holds at runtime.

**Found:** CMX-300 rework round 3 (2026-08-17), PR #374. `tests/sidebar.test.mjs`'s
"role colour is colourblind-safe and CASCADES onto the rendered badge" test read
`.ar-role.orchestrator` / `.ar-role.dispatched` colours live via `getComputedStyle` on a jsdom
built from the real `style.css`, then compared them against
`['#56B4E9', '#009E73', '#E69F00'].map(hexToRgb)` — three window-type hexes copied out of the
same stylesheet by hand rather than read from it. The judge recoloured
`.ar-type.claude { color: #56B4E9; }` to `#CC79A7` (the exact rendered orchestrator badge
colour) in a throwaway checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3174 passed) because the comparison never noticed — the literal array still said `#56B4E9`.
Closed by mounting `<span class="ar-type claude/shell/server">` fixtures in the same jsdom
document already carrying the two role badges, and reading all five colours back through
`getComputedStyle` on that one document instead of hardcoding the type side.
