## 14. A field pinned at one hop of a round-trip, untested at the next

**Assertion form:** a value is produced by one function, consumed by another, and the two
are separated by a serialize/render step in between. A test pins the value on the
*producing* side (e.g. an `as_dict()`/`to_dict()` method includes the field) and a separate
test pins it on the *consuming* side (a parser reads the field back correctly) — but nothing
drives an assertion through the hop in the middle, where the value is dumped into a rendered
text block for a human or another process to copy verbatim.

**Mutation that defeats it:** drop the field only at the render hop (e.g.
`json.dumps({k: v for k, v in m.items() if k != "field"})` instead of dumping the dict
as-is). Both the producing-side test and the consuming-side test still pass — neither of
them touches the render step — so the suite stays green even though the field never survives
the round trip in practice. The parser on the far end silently defaults the missing field
back to something else, changing behavior with no visible failure anywhere.

**Guard form that survives:** the fixture driving the render-step test must itself carry the
field with a distinctive, non-default value, and the assertion must check for that value's
literal serialized form in the rendered output — not just for OTHER fields the render step
also happens to preserve.

**Found:** `chela/dispatcher.py`'s `_required_mutations_section` (CMX-269 rework round 6) —
`Experiment.as_dict` was pinned to emit `kind` (round 2), and `judge.Experiment.parse` reads
it back, but the render-step tests
(`test_the_rework_prompt_carries_the_REQUIRED_MUTATION_SET_as_a_copy_pasteable_JSON_block`,
`test_a_re_nudged_rework_ALSO_carries_the_REQUIRED_MUTATION_SET`) used a fixture dict with no
`kind` key at all, so a render that stripped `kind` was invisible to both. Fixed by adding
`"kind": "wiring"` to each fixture and asserting `'"kind": "wiring"'` appears in the rendered
prompt.
