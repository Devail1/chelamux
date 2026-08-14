## 15. A list rendered verbatim, tested with a list of exactly one — the render-side mirror of shape 13

**Assertion form:** the same shape as #11 (a required SET tested with a set of length one) —
but on the OTHER side of a check/render pair. Shape 11 was the *enforcement* side deciding
which items in the set are satisfied; this is the *render* side deciding which items in the
same set reach the agent's brief as copy-pasteable data. A one-item fixture cannot
distinguish "dump the whole list" from "dump only the first item" — `mutations` and
`mutations[:1]` produce byte-identical output when `len(mutations) == 1`.

**Mutation that defeats it:** truncate the list before serializing it —
`json.dumps({"experiments": mutations[:1]}, indent=2)` instead of `mutations`. On a one-item
fixture this is invisible. The concrete failure is worse than a silent gap because the two
sides of the pair are coupled: the judge blocks with two survivors, the brief renders only
the first, the agent copies the JSON exactly as instructed, and shape 13's own fix —
correctly — flags the second as missing. The agent is then refused for omitting a mutation
it was never shown, with no way to discover what it is: an unescapable refuse-loop produced
by fixing one side of a pair and not the other.

**Guard form that survives:** construct a fixture with at least TWO items in the required
set and assert that BOTH appear in the rendered output by their distinguishing fields — not
just that "a" required-mutation section exists, and not just fields the first item alone
would already satisfy.

**Found:** `chela/dispatcher.py`'s `_required_mutations_section`, at both call sites that
render it (CMX-269 rework round 7) — the same two tests fixed for shape 14
(`test_the_rework_prompt_carries_the_REQUIRED_MUTATION_SET_as_a_copy_pasteable_JSON_block`,
`test_a_re_nudged_rework_ALSO_carries_the_REQUIRED_MUTATION_SET`) still built their
`mutations` list with exactly one entry, so round 6's enforcement-side fix for shape 13 had
no render-side counterpart. Fixed by giving each fixture a second, distinct mutation and
asserting both survivors' `guard` and `file` values appear in the rendered prompt.
