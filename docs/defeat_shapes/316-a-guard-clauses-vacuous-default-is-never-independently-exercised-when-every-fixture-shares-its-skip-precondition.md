## 316. A guard clause's vacuous default is never independently exercised when every fixture in the file shares the precondition that skips it

**Assertion form:** a reader function opens with an early guard clause —
`if <marker-of-the-uninitialized-state> is None: return <vacuous safe default>` — before its
main body, which scans structured content for the real answer. Every fixture in the test file
is built around the "normal," already-initialized case (a changelog with a dated release
section, a config with its first entry already written, a log with at least one record), so
every single one of them takes the main body and none ever takes the guard clause. This is
close to shape 57 ("a not-found arm is never independently armed") but simpler and easier to
miss precisely because it looks trivial: shape 57's else arm needs a *paired* fixture (a key
without its record) that a test author has to think to construct; here the guard clause is a
one-line early return that reads as too simple to need its own test — "of course an empty/unset
input returns the empty/vacuous default" — so nobody writes the fixture that is nothing BUT
that precondition (a changelog with only `## [Unreleased]`, no dated section at all — the state
of a repo before its first release).

**Mutation that defeats it:** replace the vacuous default with something that actively scans
the same input the main body would have scanned — `return set()` → `return {m.group("id") for
m in PATTERN.finditer(full_text)}`. Every existing fixture still takes the OTHER branch (the
`is not None` one), so this line never executes in the whole suite, and the mutation is
invisible. The danger is that the new behavior is not a random wrong answer: it accidentally
resembles what the function is *supposed* to do in the branch that DOES run (scan for markers),
so it will not be caught by a reviewer skimming the diff for a return statement that looks
wrong at a glance.

**Guard form that survives:** write a fixture whose input contains ONLY the state that trips
the guard clause and nothing else — no dated section, just `## [Unreleased]` — including
content that would produce a non-vacuous answer if the main body's logic ran on it by mistake
(a task-id marker sitting under `## [Unreleased]`). Assert the function returns the vacuous
default despite that marker being present, so a mutation that makes the guard clause scan the
input instead of returning the default is caught by the marker unexpectedly appearing in the
result — not just by an empty-input fixture, which a `return {}` -shaped mutation would also
satisfy vacuously.

**Found:** `chela/release_notes.py`'s `released_task_ids` (CMX-315 rework round 2, PR #393) —
`if dated is None: return set()` guards the case where a changelog has no dated release
section yet (a repo before its first release). Every existing fixture in
`tests/test_release_notes.py` built its changelog with a `## [0.8.0]` (or similar) dated
section, so the `is None` branch had never run once; the judge's mutation made it scan the
*whole* document instead (including `## [Unreleased]`), which would refuse a repo's first-ever
release with "already published in a dated release section" when no dated section exists at
all. Closed by `test_released_task_ids_treats_no_dated_section_as_nothing_released` (and its
mirror on `stale_fragments`), both built from a changelog that is nothing but `## [Unreleased]`
plus a task marker, asserting the marker does NOT come back as released/stale.

**Note on the other two findings in the same verdict:** the same round also found shape 15 (a
rendered list proven only at length one — `stale_fragments()`'s return value was proven for
many, but the *raised error's* rendered `names` string, the thing the maintainer actually
reads, was only ever proven for a single stale fragment) and shape 33 (the word "back-merge"
appears in both the diagnostic half of an error message and its actionable-instruction half, so
an assertion on the bare keyword survives deletion of either half). Both were already
catalogued; only the guard-clause shape above was new.
