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

**Round 4 — the fourth recurrence, and a change of guard shape instead of a fourth pin:**
rounds 1-3 each closed exactly the clause(s) their own round's mutations had exploited, which
by construction left the clauses *neighboring* those pins — in the same sentences, the same
parenthetical, the same bullet — reachable only through the surrounding prose being present
at all, not through its actual claim. Round 4's three mutations landed in precisely those
gaps and stayed green (3175 passed):

```diff
- are expected
-   and fine.)
+ are a defect
+   to fix by renumbering.)
```
```diff
- is already stale the moment a sibling branch is also picking a number
+ is never stale even when a sibling branch is also picking a number
```
```diff
- be a rare backstop rather than the routine merge-time renumber it used to be.
+ be the routine merge-time renumber it has always been rather than a rare backstop.
```

The first inverts the parenthetical's own conclusion — the tail immediately past round 3's
`(Numbers only need to stay unique, not contiguous` pin — telling the reader a task-numbered
entry's gap is a defect to fix rather than the expected, fine state the entry exists to
establish. The second negates the *because*-clause explaining round 3's `is *by construction*
the collision` pin, a few words later in the same sentence, leaving the doc asserting the
decentralized guess is safe. The third flips the closing sentence of the bullet round 3
pinned via `to any other free one and move on`, telling the reader renumbering at merge time
is still the routine path rather than the rare backstop this entry makes it.

By round 4 the test carried eight literal substring assertions spread across one paragraph
and its sibling bullet, and each round's mutations were drawn from the words sitting between
the previous round's pins — a search that always has somewhere left to go, because pinning a
clause never pins the clauses next to it. Round 4 closes the shape itself instead of adding a
ninth clause-level pin: it replaces every clause-level assertion on this paragraph and this
bullet with **two whole-block literal assertions**, each block captured verbatim from the doc
at write time (not retyped by hand, which would risk silently "fixing" a typo and pinning
text the doc doesn't actually contain). A block-literal assertion has no unpinned prose left
inside it — there is no longer a gap between pins for the next mutation to land in, because
there are no longer multiple pins with gaps between them, just one contiguous span that must
match exactly or the assertion fails.

**Guard form that survives (updated for round 4):** for a paragraph that has already needed
more than one clause-level pin, stop adding clause-level pins — capture the entire paragraph
(or the smallest enclosing block that contains every clause under test) as one literal string,
extracted from the doc's own normalized text rather than hand-typed, and assert containment of
the whole block. This generalizes the round 1-3 guidance ("pin the literal clause that carries
the semantic weight") one level up: once a single sentence has needed two or more separate
clause pins, the sentence itself is the unit that should be pinned, not its clauses.

**Round 5 — widening the pinned block to "the whole paragraph" still isn't enough, because
`in` is one-sided:** round 4's fix replaced every clause-level pin with two
`assert BLOCK in text` checks, one per paragraph. `in` proves the block is *unchanged* — it
proves nothing about what surrounds it. A doc regresses just as easily by *addition* as by
*edit*, and round 5's three mutations all inserted new text immediately adjacent to a pinned
block's own boundary — never inside one — so both blocks stayed intact substrings while the
doc's instructions were diluted or reversed. All three stayed green (3175 passed):

```diff
- If that shape isn't catalogued yet, add it as part
+ If that shape isn't catalogued yet, number the new file one past the highest entry already
+ in `docs/defeat_shapes/`, and add it as part
```
```diff
- branch to put the new entry on.
+ branch to put the new entry on. If looking up your task number is inconvenient, picking the
+ next number past the highest file already in the directory listing is fine too.
```
```diff
- rare backstop rather than the routine merge-time renumber it used to be.
+ rare backstop rather than the routine merge-time renumber it used to be. Either way, taking
+ the next free number off the directory listing at merge time remains the normal way to pick
+ one.
```

The first inserts a "number one past the highest" instruction *in front of* the
growth-instructions block's own opening words, so a reader hits the forbidden instruction
before ever reaching the (still word-for-word intact) prohibition that follows it. The second
and third each append a sentence immediately *after* a pinned block's closing words,
reinstating the exact decentralized listing-derived numbering both blocks exist to forbid.
No widening of the pinned span helps here in principle: however wide a block gets, `in` only
ever proves that span is unchanged, and there is always a "before the block" and "after the
block" for the next insertion to land in. This is a different defect from rounds 1-4 (which
were about a pin being too *narrow* within a span already being read) — it is `in` itself
being structurally blind to anything outside whatever span it's given, no matter how that
span's edges are chosen.

**Guard form that survives (updated for round 5):** stop using containment (`in`) against a
block extracted from a larger document and switch to *exact equality* (`==`) against a span
bounded by structure the doc already has — here, the entire `## How this catalog grows`
section, sliced from its own heading to the next top-level `## ` heading (or EOF). A
heading-to-heading slice has a natural, unambiguous boundary that isn't guessed by the test
author, and exact equality leaves no unpinned prose anywhere inside that boundary — before,
between, or after the previously-pinned sub-paragraphs — for an insertion to hide in. The
general form: when a guard needs to protect prose against both edits *and* insertions, pin
the smallest structurally-bounded region that contains the invariant (a heading's section, a
function's body, a JSON object's keys) with `==`, not an arbitrarily-sized literal block with
`in` — no amount of widening a substring pin fixes `in`'s one-sidedness, only bounding the
region and switching the comparison does.
