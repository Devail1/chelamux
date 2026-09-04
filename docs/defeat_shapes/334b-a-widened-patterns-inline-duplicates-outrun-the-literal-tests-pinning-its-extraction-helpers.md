## 334b. A widened pattern's inline duplicates outrun the literal tests pinning its extraction helpers

**Assertion form:** the same format-widening from shape 334 — a defeat-shape identifier
gains an optional lowercase-letter suffix (`328` -> `328` or `328b`) — but this time the
regex that recognizes a numbered section heading (`^## \d+[a-z]?\. `) is duplicated
literally, unparameterized, at three separate scan sites: the `DEFEAT_SHAPES.md` index
guard (`re.search`, refusing a numbered section pasted into the static index), the
per-file catalog splitter (`re.split`, turning each file's text into its numbered
sections), and the one-section-per-file counter (`re.findall`, refusing a second heading
appended to an existing shape file). Shape 334's fix added direct literal-suffix unit
tests for the two *extraction* helpers (`_shape_token_from_filename`,
`_shape_token_from_heading`) and the reference-resolution regex — but none of those three
inline scan-site copies is either helper, so none of them inherited that coverage.

**Mutation that defeats it:** narrow any of the three inline copies of
`^## \d+[a-z]?\. ` back to `^## \d+\. ` (dropping the `[a-z]?` suffix branch). Every file
that currently exists under `docs/defeat_shapes/` — all 108+ of them, including the one
shape 334 itself added — is unsuffixed, so every real on-disk fixture these three scanners
run against still matches the narrowed pattern exactly the same as the wide one. Applied
by the judge to a throwaway checkout of PR #427's head (round 2),
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` — green before the mutation (3508 passed, 0
failed) — stayed green with any one of the three narrowed individually.

**Why this looks like it already closes the gap:** shape 334's own guard form — "unit-test
each extraction function... directly against a literal suffixed input... independent of
whether any real file on disk uses the suffix yet" — reads as a complete recipe, and it
*was* followed, faithfully, for the two functions and the one regex the PR's own writeup
named. The blind spot is that "the suffix format" isn't owned by only those three
places — it is *also* baked, separately, into three more inline literals that never went
through the same review, because nothing forced an inventory of every place the pattern
text appears. A `grep` for the literal pattern string would have found all six sites at
once; reasoning from "which functions did I just add or touch" finds only the ones that
are functions.

**Guard form that survives:** hoist the shared literal to ONE module-level pattern
(`_SHAPE_HEADING_RE`) and point every scan-only call site (search/split/findall — sites
that don't need the captured token, just need to find/count/split on a heading) at that
one constant, then pin the constant directly with a literal-input unit test
(`re.search(_SHAPE_HEADING_RE, "## 328b. Title", ...)` must match) independent of what's
on disk. Single-sourcing means one test protects every call site by construction — a
future narrowing of the shared constant fails that one test, whichever of the three (or a
fourth, fifth) call site it lives at. This is the same fix shape as 334's decoy-parser
half (call the shared implementation instead of re-deriving it), applied to string
literals instead of function bodies.

**Found:** CMX-334 rework round 2 (2026-09-02), PR #427. `chela judge`'s mutation battery
found the index guard's copy of the suffix-widened heading pattern defeatable
(`tests/test_judge.py:558`) with the suite still green (3508 passed, 0 failed); the same
literal, independently duplicated at `tests/test_judge.py:503` and `:1056`, shared the
identical blind spot though the judge fired only the index-guard copy that round. Closed
by hoisting all three to a shared `_SHAPE_HEADING_RE` module constant with its own direct
literal-suffix unit test, `test_shape_heading_re_matches_a_suffixed_heading`.
