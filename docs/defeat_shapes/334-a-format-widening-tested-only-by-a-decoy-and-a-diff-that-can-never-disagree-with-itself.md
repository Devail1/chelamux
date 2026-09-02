## 334. A format-widening is tested only through a decoy parser, and its wired call site is fed a diff that can never disagree with itself

**Assertion form:** a token format is widened — a defeat-shape identifier gains an optional
lowercase-letter suffix (`328` -> `328` or `328b`) — and three places have to agree on it:
two small extraction functions that read the token off a real filename and a real heading
(`_shape_token_from_filename`, `_shape_token_from_heading`), a reference-resolution regex
that reads it out of prose (`\bshapes? (\d+)([a-z]?)\b|...`), and a pure validator
(`_validate_added_defeat_shape_filenames`) wired to a pytest test that diffs the real branch
against `origin/dev` and calls the validator on whatever files that branch actually added.
Four new unit tests were written for the suffix behavior, and all four looked like they
covered it: `test_validate_added_defeat_shape_filenames_accepts_the_ordinary_and_suffixed_cases`
and `test_duplicate_shape_tokens_treats_the_suffix_as_part_of_the_identifier` passed literal
suffixed strings straight to the functions under test.

**Mutation that defeats it:** two distinct failure modes, both invisible to the suite as
written. (1) **Decoy parser:** `_validate_added_defeat_shape_filenames` re-inlines its own
copy of the filename regex (`re.match(r"^(\d+)([a-z]?)-", filename)`) rather than calling
`_shape_token_from_filename` — so a suite that only ever calls the validator with literal
suffixed strings never once invokes `_shape_token_from_filename` or
`_shape_token_from_heading` with a suffixed argument, and never once runs the reference regex
against suffixed prose. Dropping `+ m.group(2)` from either extraction function's return, or
blanking the suffix capture group in the reference regex (`([a-z]?)` -> `()`), passed the
whole suite unchanged. (2) **A diff that can never disagree with itself:** the validator's
own call site lives inside a pytest test that diffs the real branch against `origin/dev` and
calls the validator on whatever files THIS branch actually added. On the branch that
introduced the suffix feature, that diff was always empty (the branch added no shape file of
its own) or, once one was added as part of the very fix this entry describes, always valid —
there was never a bad file in the diff for a no-op call site (`errors =
_validate_added_defeat_shape_filenames(...)` -> `errors = []`) to disagree with. Applied by
the judge to a throwaway checkout of PR #427's head, `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` — green before every one of these four mutations (3503 passed, 0 failed) — stayed
green under each individually.

**Why this looks like it already closes the gap:** "we added unit tests for the suffix" and
"the validator is wired to a real branch/diff integration test" both read, correctly, as the
kind of guard this catalog usually asks for — direct calls to the real function, not a
monkeypatched stand-in (the fix shapes 319/330 already prescribe). The gap is one level
subtler in each case. For the decoy parser: "direct call to the real function" is true of the
*validator*, but the validator was never built out of the two extraction helpers it looks
like it should share — writing `_validate_added_defeat_shape_filenames`'s own filename regex
by hand, rather than calling `_shape_token_from_filename`, means testing the validator proves
nothing about the helpers a reader would assume it exercises. For the self-diffing
integration test: it *is* wired to real git state, which is exactly the fix shapes 319/330
prescribe for "only ever monkeypatched" — but real state and INTERESTING state are different
properties, and a test that only ever sees its own always-valid diff has no interesting state
to offer, no matter how real the plumbing feeding it is.

**Guard form that survives:** for the decoy-parser half, unit-test each extraction function
and the reference pattern DIRECTLY against a literal suffixed input
(`_shape_token_from_filename("328b-x.md") == "328b"`,
`_shape_token_from_heading("## 328b. T") == "328b"`, a reference-pattern test asserting
`"328b"` in a `shapes ...` sentence resolves to `"328b"`) — independent of whether the validator happens to share
their logic, and independent of whether any real file on disk uses the suffix yet. For the
self-diffing-test half, split the wiring test's actual error-computing logic into a plain
function parameterized on `(task_number, branch, added_paths)` rather than deriving those
from git state internally, keep the git-diffing pytest test as a thin caller of it (preserving
its real-branch skip-loudly behavior), and add direct tests that call the split-out function
with an INJECTED bad file — one whose filename disagrees with the task number but whose
heading agrees (isolating the validator's own call site), and the mirror case (heading
disagrees, filename agrees, isolating the second half of the same function) — so a call site
reverted to a no-op has something concrete to fail to catch, without needing a real bad commit
on any real branch.

**Found:** CMX-334 rework round 1 (2026-09-02), PR #427. `chela judge`'s mutation battery
found all three of `_shape_token_from_filename`, `_shape_token_from_heading`, and the
reference-resolution regex's suffix handling defeatable with the suite still green (3503
passed, 0 failed each time), plus the validator's own call site inside
`test_defeat_shapes_added_files_are_numbered_by_branch_task_id` reverted to `errors = []` with
no test noticing. Closed by adding direct suffix-literal unit tests for all three
extraction/reference functions, and by splitting the wiring test's error-computing logic into
`_defeat_shape_numbering_check(root, task_number, branch, added_paths)` plus two new tests
that call it with an injected file whose filename and heading disagree with each other and
with the branch's task number in opposite combinations.
