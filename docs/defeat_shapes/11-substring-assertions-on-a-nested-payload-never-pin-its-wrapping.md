## 11. Substring assertions on a nested payload never pin its wrapping envelope

**Assertion form:** the guard builds a structured payload (`{"experiments": [...]}`) and only
ever asserts substrings that live *inside* the inner list — a field name, a value, a piece of
surrounding prose — never anything that depends on the outer envelope actually being there.

**Mutation that defeats it:** strip the envelope and serialize the inner list directly
(`json.dumps(mutations)` instead of `json.dumps({"experiments": mutations})`). Every
substring the guard checks (`'"guard": "..."'`, `'"file": "..."'`, `'"before": ...'`, the
surrounding instructional text) is still a substring of the un-enveloped JSON, so the suite
stays green — even though the payload a downstream reader expects (a JSON *object* with an
`experiments` key) no longer parses as one. The concrete failure lands one step later, outside
the test: `judge.load_experiments` rejects a bare array with "must be a JSON object with an
`experiments` list", so an agent that copies the (silently wrong) rendered block verbatim, as
instructed, spends a round on a formatting error the brief itself caused.

**Guard form that survives:** assert on the structural marker that only the correct envelope
produces — e.g. `'"experiments": [' in rendered` — not just on substrings that would survive
either shape.

**Found:** `chela/dispatcher.py`'s `_required_mutations_section` (CMX-269 rework round 5) —
`test_the_rework_prompt_carries_the_REQUIRED_MUTATION_SET_as_a_copy_pasteable_JSON_block`
asserted `'"guard": "the glyph cue"'`, `'"file": "guard.py"'` and similar field-level
substrings, none of which distinguish `json.dumps({"experiments": mutations})` from
`json.dumps(mutations)`. Fixed by adding `'"experiments": [' in prompts[0]`.
