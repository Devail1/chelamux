## 309. A note-emitting guard's own tests check only that it fired, never the specificity of its exemption predicate, the content of its own body, the wiring that reaches every report state, or that it survives coexistence and rendering

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

**Mutation that defeats it:** six independent ones, in four rounds, each invisible to the
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
5. *(round 3)* Narrow the append's condition again, on the *other* early-return gate the same
   guard-form bullet already named: `if changelog_note is not None:` →
   `if changelog_note is not None and not _git_dirty(worktree):`. Round 2's fix added a
   fixture for the `not items` gate but never one for `_git_dirty` — every fixture in the file
   still ran on a clean worktree, so a report that bails out via the dirty-worktree branch of
   `cannot_verify` (not the empty-experiments branch) silently lost the note, invisibly to the
   suite.
6. *(round 4)* Same shape a third time, on the three early-return gates *below* `_git_dirty`
   in `run_experiments`, none of which round 3 enumerated: right before each of
   `report.cannot_verify = …` for the red-baseline gate (`if not baseline.green:`), the
   unprovisionable-worktree gate (`if env_problem:`), and the contamination gate
   (`if contamination:`), insert `report.notes = []`. Every fixture in the file up to that
   point seeded a *green* `test_suite.py`, a provisionable worktree, and an
   experiment-running fixture that always restores cleanly — so none of them ever reached any
   of these three gates, and blanking the notes right before any one of them was invisible to
   the suite. The red-baseline gate is the load-bearing one of the three named in
   `run_experiments`'s own docstring, and by far the most commonly reached in production.

   ⭐ Rounds 2, 3, and 4 are the same bug, found piecemeal: `run_experiments` appends the note
   ahead of **five** early returns (`_git_dirty`, `not items`, `env_problem`,
   `not baseline.green`, `contamination`), and each round's fixture only closed the ones
   already known to be open. A guard-form bullet that says "every early-return gate" has to
   be checked against an actual enumeration of the gates in the function — not against
   whichever ones the previous round happened to name — or a rework can satisfy the bullet's
   letter (add a fixture for *a* gate) while leaving the majority of the gates it lists
   uncovered.
7. *(round 5)* Substitute instead of append: `report.notes.append(changelog_note)` →
   `report.notes = [changelog_note]`. Rounds 1-4 closed every *gate* the append sits ahead of,
   but no fixture in the suite ever called `run_experiments` with agent-authored notes
   *already present* on a report where the changelog note also fires — the one fixture that
   passes `notes=` (`tests/test_judge.py::test_notes_are_posted_and_can_never_block`, via the
   `_run` helper) calls `run_experiments` with the default `base_branch=""`, so
   `_changelog_missing_note` returns `None` there and the append line never executes at all.
   The assignment silently discards every note the judge agent itself wrote, on exactly the
   PRs where this feature fires, while the whole suite stays green.
8. *(round 5)* Blank the notes section in one renderer but not the other:
   `return "\n".join(parts) + _notes_section(report.notes)` →
   `return "\n".join(parts) + _notes_section([])` inside `block_body` — the comment a
   `SURVIVED` verdict writes, and the one a rework agent actually reads. Rounds 1-4 all
   proved the note reaches `report.notes` on every report state; none of them ever rendered
   that report through `block_body`. `comment_body`'s notes section was covered indirectly
   (`test_notes_are_posted_and_can_never_block` reads a note body back out of it), which made
   it easy to assume the sibling renderer was covered the same way — it was not.
9. *(round 5)* Dead-code the rendered title to a fixed fallback:
   `title = str(note.get("title") or "note").strip()` → `title = "note"` inside
   `_notes_section`. Every title assertion added across rounds 1-4 —
   seven of them in this file — reads `note["title"]`, the in-memory dict `_changelog_missing_note`
   returns; none of them reads a *rendered* comment. The literal string `"No CHANGELOG.md
   entry"` that CONTRIBUTING.md and the CHANGELOG entry both promise appears on the posted
   comment can vanish from every comment the judge ever posts while every existing assertion
   in the file stays green, because none of them look at the string the human actually sees.

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
- ⛔ That bullet has to be applied to *every* early-return gate the guard sits ahead of, not
  just the first one found — round 2 fixed the `not items` gate and left `_git_dirty`
  unguarded in the exact same function, one gate below. When a value is appended ahead of N
  early returns, the fixture list needs N branches covered, not one: here, a fixture that
  dirties the worktree (an uncommitted edit to a *tracked* file — untracked files don't count,
  see `_git_dirty`'s own docstring) and asserts the note still lands on the resulting
  dirty-worktree `cannot_verify` report.
- ⛔⛔ Do not stop at "N early returns" as an abstract count — *enumerate them by reading the
  function*, gate by gate, top to bottom, and write one fixture per gate before calling the
  bullet satisfied. Rounds 2 and 3 each closed exactly one gate and moved on; round 4 had to
  close three at once (`env_problem`, `not baseline.green`, `contamination`) because nobody
  had listed all five. The two gates that are cheap to reach with a real fixture (dirty
  worktree, red baseline — just write a failing `test_suite.py`) should be; the two that are
  expensive or environment-dependent to trigger for real (`provision_suite_env` failing,
  `_apply_experiments` reporting a mutation that could not be restored) are legitimately
  reached via `monkeypatch` instead — forcing the return value is fine, since what's under
  test is the wiring (does the note survive this gate), not the gate's own trigger condition.
- ⛔⛔⛔ "The value reaches the collection", "the value survives coexisting in the
  collection", and "the value renders correctly out of the collection" are three separate
  claims, each defeatable independently of the other two — closing the gate-enumeration
  bullets above says nothing about the other two. Concretely: add a fixture that calls the
  code under test with *other* entries already occupying the same list/collection the guard
  appends to, and assert both the pre-existing entries AND the new one survive (catches
  substitution masquerading as append); and for every distinct *renderer* that reads the
  collection (here, `comment_body` for the clean/cannot-verify path and `block_body` for the
  blocked path — two call sites over the same shared `_notes_section` helper), render through
  each one independently and assert the rendered *string*, not the in-memory field, contains
  the value's identifying content. Covering one renderer is not evidence the sibling renderer
  is covered — they are separate call sites and a mutation can target either independently.

**Found:** CMX-309 rework round 1 (2026-08-18), round 2 (2026-08-18), round 3 (2026-08-18),
round 4 (2026-08-18), and round 5 (2026-08-18), PR #385. Each round, the judge applied the
round's mutations to `chela/judge.py` in a throwaway checkout;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green under every one (round 1: 3226
passed; round 2: 3228 passed; round 3: 3230 passed; round 4: 3231 passed; round 5: 3234
passed), because the suite at each point had exactly the gaps the mutations exploited.
