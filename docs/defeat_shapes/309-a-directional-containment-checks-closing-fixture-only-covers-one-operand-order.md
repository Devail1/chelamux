## 309. A directional containment check's closing fixture only covers one operand order

**Assertion form:** an equality check (`lname == name`) is being defended against being
loosened to Python's `in` (substring containment). `in` is directional — `a in b` and
`b in a` are different tests — so there are two distinct ways `==` can be loosened to it:
`name in lname` and `lname in name`. A prior round's fixture closes exactly one of those two
mutants (recorded value contained in the live value) and stops there, because that was the
one direction the judge's mutation happened to apply that round. The fixture reads as "the
equality-vs-containment shape is closed" — it has the right two values, one a substring of
the other — but it only ever calls the comparison with the recorded value on the left. The
mirror mutant, which puts the *other* operand on the left of `in`, is never constructed and
so is never exercised.

**Mutation that defeats it:** `if lname == name:` → `if lname in name:` (the operands
swapped relative to the already-closed `if name in lname:` mutant). Take the existing closing
fixture's two values — a short name that is a substring of a longer one — and simply swap
which one is "recorded" and which one is "live": `window_name="cmx-305"` (recorded, the
longer string) against `live={"@668": "cmx-30"}` (the shorter string). Under `lname in name`,
`"cmx-30" in "cmx-305"` is `True`, so the fallback wrongly claims a live window that is
merely a *prefix* of the row's own recorded name — the exact same class of near-miss the
original fixture was written to catch, just approached from the other operand order. The
original fixture (`window_name="cmx-30"` against `live={"@668": "cmx-305"}`) stays green
under this mutation too: `"cmx-305" in "cmx-30"` is `False`, so nothing in the existing suite
observes the swap.

**Why this is distinct from [[25|shape 25]]:** shape 25 is the same fix applied fully at one
*call site* and partially at a *sibling* call site — two different places in the code, one
of which never got the fix at all. This is one call site, one already-fixed check, where the
fix's own single closing fixture happens to pin only one of two operand orderings of a
directional operator. There is no sibling site with a weaker fix to spot by comparison — the
gap is inside the one fixture that looks like it already proved the point.

**Guard form that survives:** when the mutation being defended against is "`==` loosened to
`in`", write TWO closing fixtures, not one — one with each operand of the (still-abstract)
`in` on the left. Pick two values where one is a substring of the other, then assert the
negative result once with the shorter string recorded and the longer one live, and once with
the longer string recorded and the shorter one live. Passing both is what actually proves
`==`, rather than "`in` read left-to-right the way the first fixture happened to phrase it".

**Found:** `chela/telegram/reconcile.py`'s `dispatched_window_ids`, CMX-308's spawn-time-race
fallback (PR #384), rework round 4. Round 1 closed `if name == lname:` → `if name in lname:`
(recorded `"cmx-30"` inside live `"cmx-305"`) via
`test_a_claimed_rows_name_match_requires_an_exact_name_not_a_substring`. Round 4's judge
applied the mirror mutation, `if lname == name:` → `if lname in name:` (live `"cmx-30"` inside
recorded `"cmx-305"`), in a throwaway checkout, and the suite (3224 tests) stayed green.
Closed by `test_a_claimed_rows_name_match_requires_an_exact_name_not_a_substring_mirrored`,
which swaps which value is recorded and which is live relative to the round-1 fixture.
