## 309. A note-emitting guard's own tests check only that it fired, never the specificity of its exemption predicate or the content of its own body

**Assertion form:** `judge._changelog_missing_note` returns `None` when an exemption applies
(CHANGELOG.md itself was touched, or the whole diff is prose) and otherwise returns a dict
with a `title` and a `body` carrying the actionable instruction. Its tests exercised the two
`None` cases and, on the firing case, asserted only `note is not None` and
`"CHANGELOG.md" in note["title"]` — never a fixture that mixes a non-prose file with a
*different* markdown file (so the exemption's specificity to CHANGELOG.md, as opposed to
"any `.md` file", was never distinguished), and never a read of `note["body"]` at all.

**Mutation that defeats it:** two independent ones, both invisible to the existing suite:

1. Broaden the exemption's identity check to a category check:
   `Path(f).name == "CHANGELOG.md"` → `Path(f).suffix == ".md"`. Any diff that touches a
   non-prose file alongside README.md, CONTRIBUTING.md, or any other `.md` file now silently
   satisfies the exemption and the note never fires — even though CHANGELOG.md itself was
   never touched. No fixture in the suite combined a non-prose file with a non-CHANGELOG
   `.md` file, so this was untestable by the existing cases.
2. Dead-code the body: `"body": (...)` → `"body": "" and (...)`. Python short-circuits
   `and` on a falsy left operand, so `note["body"]` silently collapses to `""` while
   `note["title"]` — the only field any test read — is untouched. The note still "fires" by
   every assertion in the suite; its entire actionable payload (the "add an entry under
   `## [Unreleased]`, see CONTRIBUTING.md" instruction) is gone.

**Guard form that survives:**

- Add a fixture where a non-prose file and a distinct, non-CHANGELOG `.md` file are touched
  together with no CHANGELOG.md change, and assert the note still fires — this pins the
  exemption to the specific filename, not the extension.
- Assert the body's real content (e.g. `"## [Unreleased]" in note["body"]` and
  `"CONTRIBUTING.md" in note["body"]`), not just that a note object exists. Any field a
  caller actually reads for its content — not just its presence — needs its content read
  back in the test, or dead-coding it to an empty/default value is invisible.

**Found:** CMX-309 rework round 1 (2026-08-18), PR #385. The judge applied both mutations to
`chela/judge.py` in a throwaway checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed
green (3226 passed) under each, because `tests/test_judge_changelog_note.py`'s fixtures never
combined a non-prose file with a non-CHANGELOG `.md` file, and no test in the file ever read
`note["body"]`.
