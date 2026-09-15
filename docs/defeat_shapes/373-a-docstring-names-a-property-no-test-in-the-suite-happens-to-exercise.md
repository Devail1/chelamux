## 373. A docstring names a property ("checked against X, not Y") that every existing test happens to satisfy either way, so nothing pins it

**Assertion form:** `render_prompt`'s docstring says unknown-variable detection is "checked
against the TEMPLATE, not the rendered output, so a provided value that happens to contain
literal `{{...}}` text can never be mistaken for an unresolved reference." The existing suite
(`tests/test_render_prompt.py`) covers: an all-known template, an unprovided var, multiple
unprovided vars, extra unused vars, and prose that merely *looks* like the `{{...}}` syntax.
None of those five cases ever passes a var **value** containing literal `{{...}}` — the one
input the docstring's claim is actually about.

**Why that's a gap and not just missing coverage:** the mutation the judge applied
(`chela/workflow.py`) reorders the two steps — substitute first, THEN scan for unknown refs —
which checks the rendered *output* instead of the *template*, exactly the thing the docstring
says never happens. Every one of the five existing tests still passes unchanged under that
reorder, because none of them ever produces an unresolved `{{...}}` in the *output* that wasn't
already in the *template*. A docstring claim with no test that can fail if the claim is false is
indistinguishable, to CI, from no claim at all.

**Mutation that defeats it:** swap the two blocks in `render_prompt` so the unknown-var scan
runs on `out` (after substitution) instead of `template` (before it). Every existing assertion
in `tests/test_render_prompt.py` still holds.

**Guard form that survives:** add a case where a *value* contains a literal unresolved
reference the template itself never mentions — `render_prompt("{{a}}", {"a": "{{b}}"})` must
equal `"{{b}}"`, not raise on `b`. That only holds if the unknown-scan runs against the
template; under the reordered mutation it raises `unknown template variable(s): {{b}}` instead.

**Found:** CMX-373 rework round 1 (2026-09-15), PR #525. Flagged as a non-blocking note by the
judge (not itself the corruption round's finding) alongside the mutation on the same function;
catalogued here because it is the general shape the mutation exploited — closed by
`test_render_prompt_checks_the_template_not_the_rendered_output`.
