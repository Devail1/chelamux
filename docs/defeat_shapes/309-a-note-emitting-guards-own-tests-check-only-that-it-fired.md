## 309. A note-emitting guard's own tests check only that it fired, never the specificity of its exemption predicate, the content of its own body, or the wiring that reaches every report state

**Assertion form:** `judge._changelog_missing_note` returns `None` when an exemption applies
(CHANGELOG.md itself was touched, or the whole diff is prose) and otherwise returns a dict
with a `title` and a `body` carrying the actionable instruction; `run_experiments` appends
that dict to `report.notes` unconditionally, ahead of its own early-return gates, so the note
is meant to be "visible on every verdict instead of nowhere at all". Round 1's tests
exercised the two `None` cases and, on the firing case, asserted only `note is not None` and
`"CHANGELOG.md" in note["title"]` — never a fixture that mixes a non-prose file with a
*different* markdown file, and never a read of `note["body"]` at all. Round 2's tests fixed
both of those but still called `run_experiments` only with `{"experiments": []}` (the one
branch every fixture in the file happened to share), and pinned only two substrings living in
the *tail* of `note["body"]`'s three concatenated string literals.

**Mutation that defeats it:** four independent ones, in two rounds, each invisible to the
suite at the time:

1. *(round 1)* Broaden the exemption's identity check to a category check:
   `Path(f).name == "CHANGELOG.md"` → `Path(f).suffix == ".md"`. Any diff that touches a
   non-prose file alongside README.md, CONTRIBUTING.md, or any other `.md` file now silently
   satisfies the exemption and the note never fires — even though CHANGELOG.md itself was
   never touched. No fixture in the suite combined a non-prose file with a non-CHANGELOG
   `.md` file, so this was untestable by the existing cases.
2. *(round 1)* Dead-code the body: `"body": (...)` → `"body": "" and (...)`. Python
   short-circuits `and` on a falsy left operand, so `note["body"]` silently collapses to `""`
   while `note["title"]` — the only field any test read — is untouched. The note still
   "fires" by every assertion in the suite; its entire actionable payload is gone.
3. *(round 2)* Narrow the append's condition to the one branch every fixture exercises:
   `if changelog_note is not None:` → `if changelog_note is not None and not items:`. A
   report reached with a non-empty `items` list (any PR that actually proposes experiments —
   the overwhelmingly common case) silently loses the note, while every existing test — all
   of which pass `{"experiments": []}` — keeps passing.
4. *(round 2)* Blank only the first literal in the concatenated body string:
   `"This diff changes non-prose files but never touches CHANGELOG.md. If any of "` → `""`.
   The remaining two literals still contain both substrings the round-1 test checked
   (`"## [Unreleased]"`, `"CONTRIBUTING.md"`), so that assertion passes even though the
   sentence stating the mechanical fact the note exists to report — that non-prose files
   changed and CHANGELOG.md did not — is gone.

**Guard form that survives:**

- Add a fixture where a non-prose file and a distinct, non-CHANGELOG `.md` file are touched
  together with no CHANGELOG.md change, and assert the note still fires — this pins the
  exemption to the specific filename, not the extension.
- Assert the body's real content, spanning *every* one of its concatenated literals (not just
  whichever one holds a distinctive phrase) — e.g. `"changes non-prose files but never
  touches CHANGELOG.md" in note["body"]` in addition to `"## [Unreleased]" in note["body"]`
  and `"CONTRIBUTING.md" in note["body"]`. Any field a caller actually reads for its content —
  not just its presence — needs its content read back in the test, or dead-coding part of it
  is invisible.
- When a guard is wired ahead of / around an early-return gate, add a fixture that reaches the
  guard's effect through the *other* branch of that gate — here, a `run_experiments` call
  with a non-empty, genuinely-executing `items` list that reaches a normal (non-cannot-verify)
  verdict — not just the one branch every other fixture in the file happens to share.

**Found:** CMX-309 rework round 1 (2026-08-18) and round 2 (2026-08-18), PR #385. Each round,
the judge applied the round's mutations to `chela/judge.py` in a throwaway checkout;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green under every one (round 1: 3226
passed; round 2: 3228 passed), because the suite at each point had exactly the gaps the
mutations exploited.
