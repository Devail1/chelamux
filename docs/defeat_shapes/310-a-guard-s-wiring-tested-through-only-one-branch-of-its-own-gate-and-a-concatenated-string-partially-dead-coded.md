## 310. A guard's wiring is tested through only one branch of its own gate, and a concatenated string literal is partially dead-coded where the surviving half still matches the assertion

**Assertion form:** `run_experiments` appends `changelog_note` to `report.notes`
unconditionally, before either of its two early-return gates (`_git_dirty`,
`not items`) — the docstring's own claim is "visible on every verdict instead of nowhere at
all". `tests/test_judge_changelog_note.py` proved this only through
`test_run_experiments_carries_the_note_even_on_a_cannot_verify_report`, which always calls
`run_experiments` with `{"experiments": []}` — every fixture in the file takes the same
`items`-is-empty branch, so nothing distinguished "appended on every report state" from
"appended only when `items` is falsy". Separately, `note["body"]` is one Python string built
from three adjacent literals concatenated by juxtaposition; the existing test asserted two
substrings (`"## [Unreleased]"`, `"CONTRIBUTING.md"`) that both happen to live entirely
inside the *second and third* literals.

**Mutation that defeats it:** two independent ones, both invisible to the existing suite:

1. Narrow the append's condition to the one branch every fixture exercises:
   `if changelog_note is not None:` → `if changelog_note is not None and not items:`. A
   report reached with a non-empty `items` list (any PR that actually proposes experiments —
   the overwhelmingly common case) silently loses the note, while every existing test — all
   of which pass `{"experiments": []}` — keeps passing.
2. Blank only the first literal in the concatenated body string:
   `"This diff changes non-prose files but never touches CHANGELOG.md. If any of "` → `""`.
   The remaining two literals still contain both substrings the old test checked
   (`"## [Unreleased]"`, `"CONTRIBUTING.md"`), so the assertion passes even though the
   sentence stating the mechanical fact the note exists to report — that non-prose files
   changed and CHANGELOG.md did not — is gone.

**Guard form that survives:**

- When a guard is wired ahead of / around an early-return gate, add a fixture that reaches
  the guard's effect through the *other* branch of that gate — here, a report built from a
  non-empty, genuinely-executing `items` list that reaches a normal (non-`cannot_verify`)
  verdict — not just the one branch every other fixture in the file happens to share.
- For an assertion on a string built from multiple concatenated literals, assert a substring
  that spans (or lives entirely inside) *each* literal at least once, not just whichever
  literal happens to contain a distinctive phrase. A substring pinned only to the tail
  literals leaves the head literal free to be dead-coded.

**Found:** CMX-309 rework round 2 (2026-08-18), PR #385. The judge applied both mutations to
`chela/judge.py` in a throwaway checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed
green (3228 passed) under each, because every fixture in
`tests/test_judge_changelog_note.py` called `run_experiments` with an empty `items` list, and
the one body assertion checked substrings that survived blanking the sentence stating the
note's actual mechanical finding.
