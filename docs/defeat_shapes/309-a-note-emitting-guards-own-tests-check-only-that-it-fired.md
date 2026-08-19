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
10. *(round 7)* Truncate the render loop instead of dead-coding a title:
    `for note in notes:` → `for note in notes[:1]` inside `_notes_section`. Round 5 closed
    "coexistence in the collection" (mutation 7, a fixture with two notes that reads
    `report.notes`) and "the rendered title is real" (mutations 8-9, fixtures that render but
    each build a notes list with exactly ONE entry) as two *separate* fixtures — but never
    combined them. Since the changelog note is APPENDED (always last), slicing the render
    loop to `notes[:1]` silently drops it from every rendered comment on every PR that also
    carries an agent note — the normal case, every round of this PR carried 3-5 — while the
    coexistence fixture (never renders) and both rendering fixtures (never carry a second
    note) all stayed green.
11. *(round 7)* Blank the notes right before a non-`return` cannot_verify assignment:
    `_, added, deleted, files = heavy\n report.cannot_verify = (...)` →
    `_, added, deleted, files = heavy\n report.notes = []\n report.cannot_verify = (...)` in
    the CMX-271 deletion-heavy downgrade. Rounds 2-4's enumeration was keyed on the word
    `return` — five early `return report` statements — and closed all five. This site sets
    `report.cannot_verify` and *falls through* to the shared `return report` at the bottom of
    the function; it is a sixth cannot_verify-setting site that an enumeration keyed on
    `return` cannot find. No fixture in `tests/test_judge_changelog_note.py` produces a
    deletion-heavy diff, and `tests/test_judge_deletion_heavy.py`'s own fixture that reaches
    this branch asserted nothing about `report.notes`, so blanking it right before the verdict
    was invisible to the suite.

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
- ⛔⛔⛔⛔ *(round 7)* Two guard-form bullets satisfied SEPARATELY, each with its own
  single-witness fixture, do not prove their CONJUNCTION. The bullet above says to prove
  coexistence-in-the-collection and prove rendering-reads-the-real-value; round 5 did both —
  but the coexistence fixture never called a renderer, and both rendering fixtures built a
  notes list with exactly one entry, so neither witness ever exercised "a rendered comment
  built from 2+ notes" at the same time. A mutation that needs BOTH conditions at once
  (`notes[:1]` only differs from `notes` when the list has more than one entry, *and* only
  shows up in output when that sliced list is actually rendered) slips through any suite where
  the two properties were each proven true in isolation. When a catalog entry lists N
  guard-form bullets for the *same* underlying value, add at least one fixture that satisfies
  all of them AT ONCE, not just one fixture per bullet — the AND of two green tests is not the
  same claim as one green test on the AND of their conditions.
- ⛔⛔⛔⛔⛔ *(round 7)* An enumeration of "every gate that sets X" keyed on a syntactic marker
  (here, the keyword `return`) only finds the sites that use that marker. `run_experiments`
  has a sixth site that sets `report.cannot_verify` — the CMX-271 deletion-heavy downgrade —
  which assigns and falls through instead of returning early, so a search for `return
  report` inside the function does not find it. Enumerate by the *semantic* question ("what
  are all the statements that can set this field on this report, anywhere in the function?"),
  not by the syntax the previously-found instances happened to share — a shared syntax is a
  hint about where to look, not proof the list is complete.
12. *(round 8)* Truncate a full-collection scan to a fixed position:
    `any(Path(f).name == "CHANGELOG.md" for f in files)` → `any(... for f in files[:1])`, and
    the mirror mutation on the prose-only check, `all(_is_prose_path(f) for f in files)` →
    `all(... for f in files[-1:])`. Both fixtures that pin the CHANGELOG.md exemption
    (`test_changelog_missing_note_is_none_when_the_changelog_was_touched`,
    `test_run_experiments_carries_no_note_when_the_changelog_was_touched`) stage exactly
    `CHANGELOG.md` + `feature.py`; `git diff --numstat` emits paths byte-sorted, and `C` < `f`,
    so CHANGELOG.md is ALWAYS `files[0]` in every fixture in the file — a membership scan and
    a first-position scan are indistinguishable to the suite. The one fixture mixing prose
    with code (`..._still_fires_when_a_different_md_file_is_also_touched`) stages README.md +
    feature.py, and `R` < `f` too, so the non-prose file is ALWAYS *last* there — a full scan
    and a last-position scan are equally indistinguishable. In production, any PR whose diff
    carries a path sorting before `CHANGELOG.md` (`.github/workflows/ci.yml`, a dotfile, any
    all-caps name earlier in the alphabet) loses the exemption despite having written the
    entry — a real trigger, not a theoretical one: CMX-305 touched exactly `.github/…/ci.yml`
    alongside its changelog entry.
13. *(round 8)* Widen an identity check to an allow-list naming a second, specific alternative:
    `Path(f).name == "CHANGELOG.md"` → `Path(f).name in ("CHANGELOG.md", "CONTRIBUTING.md")`.
    Round 1 (mutation 1 above) closed the *category* broadening (`.name ==` → `.suffix ==`)
    with a single witness, README.md — but every fixture that uses README.md pairs it with a
    genuine CHANGELOG.md touch, so it only proves README.md alone doesn't satisfy the
    exemption when CHANGELOG.md is *also* present, never that a *different* named file could
    stand in for CHANGELOG.md on its own. A PR that edits CONTRIBUTING.md alongside code —
    routine, and the literal shape of this PR before this round — silently loses the note
    although CHANGELOG.md was never touched.

14. *(round 9)* Narrow a directory-membership predicate to a filename-prefix predicate:
    `p.parent.name == "changelog.d" and p.name != "README.md"` →
    `p.parent.name == "changelog.d" and p.name.startswith("CMX-")`. Round 8 taught the
    exemption to recognise CMX-312 fragment files under `changelog.d/`, but the test added for
    it (`test_changelog_missing_note_is_none_when_a_changelog_d_fragment_was_added`) stages
    exactly one fragment ever, `changelog.d/CMX-999.md`, and the file's only other
    `changelog.d/` fixture stages `README.md` — which the mutation also rejects, for the same
    reason the real predicate does. A single fragment witness whose name happens to start with
    `CMX-` cannot tell "any file here except README.md" (what
    `release_notes.collect_fragments`, chela/release_notes.py:181-183, actually publishes)
    apart from "a file here whose name starts with `CMX-`" — the same single-item-collapses-
    every-candidate shape [[306|shape 306]] names, here on a filename predicate instead of a
    fallback expression. In production, a fragment that doesn't follow the `CMX-<id>.md`
    convention — a hotfix, a contributor's own naming, any future convention change —
    `release_notes` WILL collect and publish, while the mutated exemption still tells the
    author "No CHANGELOG.md entry," contradicting the predicate's own docstring ("any file
    there except the directory's own `README.md`"). `CHELA_REQUIRE_JS_TESTS=1 uv run
    pytest -q` stayed green (3299 passed) under this mutation.

**Guard form that survives (round 9 addendum):**

- ⛔⛔⛔⛔⛔⛔⛔⛔ *(round 9)* When a predicate's real contract is "any member of a set minus one
  named exception" (directory membership minus `README.md`), a fixture that only ever supplies
  ONE example of "a member that isn't the exception" can't distinguish that contract from any
  narrower one a mutation could substitute (a filename prefix, a specific extension, an
  allow-list of one) — the single example satisfies all of them at once. Add a second member
  that satisfies the true contract but NOT the narrower substitute (here: a `changelog.d/`
  fragment named without the `CMX-` prefix) and assert the exemption still fires for it.

**Guard form that survives (round 8 addendum):**

- ⛔⛔⛔⛔⛔⛔ *(round 8)* A membership predicate (`any(...)` / `all(...)` over a collection)
  proven only with fixtures where the deciding element happens to occupy the same position in
  every one (always first, always last) is proving position, not membership — regardless of
  how many fixtures there are. Add at least one fixture per predicate where the deciding
  element sits somewhere else in the sort order: a file that sorts *before* the exempting
  path (kills a `[:1]`-shaped truncation) and, separately, a prose file that sorts *after* a
  non-prose one (kills a `[-1:]`-shaped truncation). `git diff --numstat`'s byte-sort order is
  deterministic and known ahead of time (`.` and digits before uppercase, uppercase before
  lowercase) — a fixture can be built to land the deciding file at a specific, non-default
  position on purpose, and should assert that position (`files[0] != "CHANGELOG.md"`) so the
  fixture's own precondition can't silently drift back to the default.
- ⛔⛔⛔⛔⛔⛔⛔ *(round 8)* A single "different alternative" witness (here, README.md) proves
  exclusivity against exactly the one name it uses — not against the identity check in
  general. Widening an `==` to an `in (...)` allow-list that names a *different* alternative
  the existing witness never exercised slips past it. When a guard's whole job is "this exact
  value, no other", prove it against more than one plausible-but-wrong alternative, especially
  ones with the shape of a routine, expected diff (here, a second prose/doc file a real PR is
  likely to touch alongside code).

**Found:** CMX-309 rework round 1 (2026-08-18), round 2 (2026-08-18), round 3 (2026-08-18),
round 4 (2026-08-18), round 5 (2026-08-18), round 7 (2026-08-19), round 8 (2026-08-19), and
round 9 (2026-08-19), PR #385. Each round, the judge applied the round's mutations to
`chela/judge.py` in a throwaway checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed
green under every one (round 1: 3226 passed; round 2: 3228 passed; round 3: 3230 passed;
round 4: 3231 passed; round 5: 3234 passed; round 7: 3246 passed; round 8: 3268 passed;
round 9: 3299 passed), because the suite at each point had exactly the gaps the mutations
exploited.
