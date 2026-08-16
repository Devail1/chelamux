## 301. A prose guard pins substrings untouched by the mutation it was written to catch

**Assertion form:** a doc-correctness test written right after landing an instructional
change, asserting on words that are true of the fix in isolation but never actually
overlapping the specific span the fix changed:

```python
assert "numbered one past the current highest" not in text, (...)
assert "your own CMX task number" in text, (...)
assert "centrally serialized counter" in text
```

The doc's real sentence is `numbered after **your own CMX task number** ... — **not** "one
past the current highest"` — the words "numbered" and "one past the current highest" are
separated by the whole instruction, so the literal phrase `"numbered one past the current
highest"` never appears in the doc, before the fix or after it. The other two assertions
pin phrases the fix *added*, but neither one is anywhere near the two spans a rework of this
entry would actually touch: the prohibition word itself, and the worked example's number.

**Mutation that defeats it:** touch the exact clause the doc exists to get right, leave
every asserted substring standing:

```diff
- — **not**
-   "one past the current highest".
+ — **or**
+   "one past the current highest".
```

```diff
- e.g. task `CMX-301` → `301-your-shape-slug.md`
+ e.g. task `CMX-301` → `69-your-shape-slug.md`
```

The first flips a prohibition into an endorsement — reopening the exact six-way collision
the entry was written to close. The second points the one concrete, copyable worked example
at a "current highest" guess (69 = 68+1 at the time of writing) instead of the task's own
number, teaching the reader the exact guess the surrounding prose forbids. Neither diff
touches "your own CMX task number", "centrally serialized counter", or produces the string
"numbered one past the current highest" — every assertion in the test stays green under both
mutations simultaneously, and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3174
passed) with either corruption in place.

**Why this slips through even though the assertions look like they cover the change:** a
prose-diff test written against the *summary* of what changed (a task number, a "don't do
this" rule) rather than against the *literal clause* the mutation could flip reads as
coverage without being coverage — three assertions can all be true of both the fixed text and
a corrupted text at once when none of them anchors on the word (`not`/`or`) or the value
(`301`/`69`) doing the actual work. The tell: none of the three assertions, read on their
own, could tell you *which* of the two competing instructions ("number off the task id" vs.
"number off the current highest") the doc currently gives — they're compatible with both.

**Guard form that survives:** pin the literal clause that carries the semantic weight, not a
paraphrase or a fact adjacent to it — the prohibition word directly next to the thing it
prohibits (`'— **not** "one past the current highest"' in text`, which is false the instant
`not` becomes anything else), and the worked example's number tied to its own task id
(`"e.g. task \`CMX-301\` → \`301-your-shape-slug.md\`" in text`, which is false the instant
the second number diverges from the first). Before trusting a prose-doc assertion, hand-apply
the exact corruption you're worried about and confirm the assertion goes red — an assertion
that would still pass against a document making the *opposite* claim isn't testing the claim.

**Found:** `docs/DEFEAT_SHAPES.md` /
`tests/test_judge.py::test_defeat_shapes_growth_instructions_number_by_task_id_not_current_highest`
(CMX-301, PR #375, rework round 1) — both mutations above, applied by the judge to a
throwaway checkout of the PR's head, stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` (3174 passed, 0 failed). Closed by pinning the exact prohibition clause and the
exact worked-example substring instead of the paraphrased assertions above.
