## 304. A repo-wide sweep test asserts its findings list is empty, but never asserts the glob it walked matched anything — so a broken glob and a clean sweep are indistinguishable

**Assertion form:** a test walks every file matching a glob (`TESTS_DIR.glob("test_*.py")`),
runs a check against each one, collects violations into a list, and asserts the list is
empty — `assert not violations, "..."`. This is the correct shape for the check itself, and
is exactly how `tests/test_constant_guards_pin_the_literal.py`'s
`test_no_test_file_asserts_a_numeric_constant_against_its_own_symbol_unpinned` was written:
it flags any test file that compares a numeric module constant against itself with no
literal pin, per [[05|shape 05]].

**Why that assertion doesn't work here:** `assert not violations` is true in exactly two
worlds — "every file was checked and none violated" (the intended meaning) and "zero files
were checked" (an inert no-op). Nothing in the assertion or its message distinguishes them.
A path refactor that moves `TESTS_DIR`, a directory rename, or a rename that stops matching
the `test_*.py` glob pattern silently empties the walk, and the test keeps reporting PASSED
— it just stopped proving anything. This is [[12|shape 12]]'s "untested because every ..."
family and the brief's own warning turned into code: *"if the walker finds no files, cannot
parse one, or the allow-list swallows everything, it must FAIL or SKIP LOUDLY with a stated
reason — never pass silently."* The three synthetic tests in the same file
(`test_scanner_flags_a_moving_comparison_with_no_literal_pin`, etc.) prove the scanner
*function* is correct on hand-built source; none of them prove the scanner was ever
*applied* to the real repo, because they don't go through the glob at all.

**Mutation that defeats it:** point `TESTS_DIR` at a directory the glob can't populate (an
empty temp dir, or a real refactor that moves `tests/` and forgets to update `TESTS_DIR`).
`violations` stays `[]` by construction — there is nothing to iterate — and
`assert not violations` passes exactly as it would after a fully clean sweep of the entire
real suite.

**Guard form that survives:** count what the walk actually visited and assert that count is
nonzero with a real floor, not `> 0` — a single stray matching file would satisfy a bare
`> 0` and still mean the sweep is barely running. Anchor the floor to something you can
defend in a comment (e.g. a fraction of the repo's current file count, so the guard survives
the suite growing but still fires if it collapses):

```python
scanned = 0
for path in sorted(TESTS_DIR.glob("test_*.py")):
    ...
    scanned += 1

assert scanned > 100, (
    f"the repo-wide sweep only examined {scanned} test file(s) under {TESTS_DIR} — "
    "either tests/ has shrunk far below its historical size or TESTS_DIR / the "
    "`test_*.py` glob has stopped matching the real test files; either way this check "
    "is inert and a green result here proves nothing was scanned"
)
```

Prove the new assertion goes red the same way any guard is proven: point `TESTS_DIR` at an
empty directory in isolation and watch `scanned > 100` fail, before trusting that it would
fire on a real regression.

**Found:** CMX-304 rework round 1 (2026-08-18), PR #378. The orchestrator's review of the
new constant-guard scanner (itself closing [[05|shape 05]] for the sixth time) noted the
repo-wide sweep test asserted `not violations` with no assertion that the glob it walked
ever matched a file — the sweep test proving the scanner's *function* was correct on
synthetic source, but nothing proving the scanner was ever *applied* to the real repo.
