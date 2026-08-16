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
**Fixing the mutations a judge round actually found is not the same as fixing every assertion
sharing their shape:** this entry recurred three times (see Round 2 and Round 3 below) in the
same test, on sibling clauses of the same paragraph the fix had already touched, because each
round patched only the clause its own round's mutations hit. When one presence-only assertion
in a test is found to be this shape, audit every *other* assertion in that same test for the
same defect before closing the round — not just the one(s) named in the verdict.

**Found:** `docs/DEFEAT_SHAPES.md` /
`tests/test_judge.py::test_defeat_shapes_growth_instructions_number_by_task_id_not_current_highest`
(CMX-301, PR #375, rework round 1) — both mutations above, applied by the judge to a
throwaway checkout of the PR's head, stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` (3174 passed, 0 failed). Closed by pinning the exact prohibition clause and the
exact worked-example substring instead of the paraphrased assertions above.

**Round 2 — the same shape, one entry over:** round 1's fix pinned the two clauses its own
two mutations had exploited, but left the *original* three assertions in the same test
untouched: `assert "your own CMX task number" in text`, `assert "centrally serialized
counter" in text`, and the "not in text" check on the bare phrase. Those three are
presence-only checks of a phrase whose meaning is carried by the words *next to* it, not
inside it — exactly this shape, just not yet pinned there. Three more mutations exploited
that gap and stayed green (3175 passed):

```diff
- numbered after **your own
+ numbered after anything except **your own
```
```diff
- reuse that number instead of computing a new
+ ignore that number and compute a new
```
```diff
- flight at once never receive the same one
+ flight at once may receive the same one
```

Each leaves its neighboring pinned phrase (`"your own CMX task number"`,
`"centrally serialized counter"`) standing untouched while reversing what the sentence
actually instructs. Closed the same way as round 1: pin the literal clause that contains the
word doing the semantic work (`after` directly before `**your own`; `reuse ... instead of
computing a new one from a listing`; `never receive the same one`) instead of a phrase
merely adjacent to it. No new file was added for this round — it is the identical defeat
shape recurring in three siblings of the two clauses round 1 already fixed, not a new one;
the lesson here is that fixing the *mutations found* is not the same as fixing every
assertion sharing their shape in the same test.

**Round 3 — a third recurrence, in the sibling bullet and the two remaining clauses of the
same paragraph:** rounds 1-2 each closed the specific clauses their own round's mutations
exploited, but two more clauses of the same paragraph, plus the collision *backstop* sentence
in the bullet directly below it (also edited by this PR), were still unpinned or only
reachable indirectly. Three more mutations exploited that gap and stayed green (3175 passed):

```diff
- to any other free one and move on
+ to the next free one and move on
```
```diff
- (Numbers only need to stay unique, not contiguous
+ (Numbers only need to stay contiguous, not unique
```
```diff
- is *by construction* the collision
+ is *by construction* not the collision
```

The first reverts the collision *backstop* — what to actually do when the uniqueness test
fires for real — back to the "current highest" guess the whole entry exists to abolish, in
the one place the doc gives concrete instructions for that case. The second inverts the
parenthetical that makes a task-numbered entry (`301-...md` sitting above the `01-`-`70-`
legacy range) legal rather than something to "fix" by renumbering. The third negates the
rationale itself, leaving the doc implicitly endorsing the decentralized guess as safe.
Closed the same way as rounds 1-2: pin the literal clause each mutation targets (`to any
other free one`; `unique, not contiguous`, in that order; `*by construction* the collision`,
with nothing between the emphasis and "the collision") instead of a phrase merely nearby.
Still no new file for this round, for the same reason as round 2 — same shape, third
recurrence, not a new one. See the added note in the Guard-form field above: this recurrence
is the direct consequence of only patching the assertions a round's own mutations named,
instead of auditing every assertion in the same test for the same defect the first time it
was found.
