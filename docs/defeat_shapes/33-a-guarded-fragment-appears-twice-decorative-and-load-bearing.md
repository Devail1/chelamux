## 33. A guarded fragment appears twice — decorative and load-bearing — and presence alone can't tell them apart

**Assertion form:** the guard asserts a fragment is present somewhere in a block of rendered
text (`assert "docs/defeat_shapes/" in body`) to prove a specific load-bearing sentence
landed — but the SAME fragment also appears a second time in the same text, in a decorative
mention that isn't the thing the guard actually cares about.

**Mutation that defeats it:** revert only the decorative mention (or only the load-bearing
one) back to its pre-PR wording, leaving the other occurrence of the fragment untouched. A
plain `fragment in text` check can't distinguish which occurrence survived — as long as
*one* of the two is still there, the assertion passes, even though the specific sentence the
guard was written to protect is the one that reverted.

**Found:** `chela/judge.py`'s `block_body` item 4 (CMX-284 rework round 3) — the sentence
names `docs/defeat_shapes/` twice: once in a decorative parenthetical
(`(see docs/defeat_shapes/ for the catalog itself)`) and once in the load-bearing directive
(`add ONE NEW FILE to docs/defeat_shapes/`). Round 2's fix pinned `"docs/defeat_shapes/" in
body`, which is satisfied by either occurrence alone — so reverting just the parenthetical,
leaving the directive intact, would still pass silently, and the reverse (directive reverted,
parenthetical intact) was the actual round-3 SURVIVED finding.

**Guard form that survives:** assert on each occurrence's own surrounding text, not the
shared fragment in isolation — `"add ONE NEW FILE to \`docs/defeat_shapes/\`" in body` pins
the directive specifically, and `"(see \`docs/defeat_shapes/\` for the catalog itself)" in
body` pins the parenthetical specifically. Two narrower assertions, each anchored to text
unique to its occurrence, close both independently instead of one wide assertion that either
occurrence can satisfy alone.
