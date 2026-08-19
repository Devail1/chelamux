## 312. A sibling CLI default left unpinned because every test passes it explicitly

**Assertion form:** a CLI option's default is `some_path_helper()` — computed relative to the
module's own file location, not the caller's cwd, so it resolves to a real, meaningful path
in the repo. A *structurally identical sibling* option, right next to it, has its default
pinned by a real end-to-end test: the CLI is invoked with that flag omitted, and the test
asserts on content that only exists in the real target the default is supposed to resolve to.
The sibling option looks covered by the same pattern — it isn't, because every test that
touches it supplies the flag explicitly instead.

**Mutation that defeats it:** repoint the unpinned default's helper at a directory that also
exists and also parses (a typo, a `.disabled` suffix, a stale rename) — `chela/release_notes.py`'s
`_default_changelog_d_path()` returning `.../ "changelog.d.disabled"` instead of
`.../ "changelog.d"`. `_default_changelog_path()` (CHANGELOG.md's default) *is* covered this
way — `test_cli_prints_notes_for_a_real_version` runs `python -m chela.release_notes 0.3.0`
with no `--changelog` and asserts real repo content came back. `--changelog-d`'s default has
no equivalent: every test that reaches `collect_fragments`/`promote_unreleased` passes
`--changelog-d` explicitly (with a `tmp_path` fixture), so nothing ever calls
`_default_changelog_d_path()` at all. The documented no-flags invocation
(`python -m chela.release_notes --release X.Y.Z`) would silently collect zero fragments —
exactly the silent-entry-loss failure mode the feature exists to close — and the whole suite
stays green through the mutation because the code path it broke is never reached.

**Why the sibling's own fix doesn't transfer directly:** mirroring
`test_cli_prints_notes_for_a_real_version` for `--changelog-d` would mean running the CLI's
`--release` mode with no `--changelog-d` against this repo's *real* `CHANGELOG.md` and
`changelog.d/` — but `--release` is destructive (it overwrites `CHANGELOG.md` and deletes
every consumed fragment file), so doing that from a test would corrupt the repo's own
changelog on every test run. The reachable, non-destructive option that still exercises the
actual default is a direct wiring test: call the private default-resolver function itself and
assert it lands on the real repo path — `release_notes._default_changelog_d_path() ==
_REPO_ROOT / "changelog.d"`. This is not a symptom of shape 5 (asserting a source constant
instead of the rendered value): the assertion invokes the actual function under test and reads
back what it actually returns, it just can't additionally thread that return value through
`--release`'s destructive write path without a live copy of the repo to write into.

**Guard form that survives:** when a CLI has two structurally identical default-producing
options and only one has an end-to-end default test, don't assume the pattern generalizes —
check whether the *other* option's consumer is destructive (writes files, deletes files, has
side effects beyond stdout) before reusing the same end-to-end shape. If it is, pin the
default-resolver function directly instead of skipping the sibling default entirely.

**Round 2 — the same excuse, borrowed by a THIRD sibling default it didn't actually apply
to:** round 1's fix above landed with a stated reason for why `--changelog-d`'s default
needed the indirect resolver-pin instead of an end-to-end test: `--release` is destructive,
so running it with the flag omitted against the real repo isn't safe. `--release` has a
*third* structurally identical default in the same function — `--date` (`date = args.date or
_date.today().isoformat()`) — and it was left just as unpinned as `--changelog-d` had been:
every `--release` test in the file supplies `--date` explicitly. But `--date`'s consumer has
no destructiveness problem at all — `test_cli_release_writes_the_changelog_and_deletes_consumed_fragments`
already runs `--release` end-to-end against a synthetic `tmp_path` `CHANGELOG.md` /
`changelog.d`, not the real repo, so omitting `--date` from that exact same call is exactly
as safe as the ones already in the file. Judge mutation:
`date = args.date or _date.today().isoformat()` → `date = args.date or "1970-01-01"`; the
whole suite stayed green (3246 passed) because nothing exercised the `or` branch. The
indirect fix that was *correct* for `--changelog-d` (because that consumer really is
destructive against the real repo) made the *unpinned* `--date` look like it needed — or
already had — the same treatment; it didn't need indirection at all, just the missing
end-to-end test case in a pattern that already existed. **The generalized lesson:** when a
shape's fix goes indirect for a stated reason, re-verify that reason against every sibling
individually before leaving the others unfixed — check whether the sibling's own consumer is
*already* exercised end-to-end through a safe fixture (like `tmp_path`) elsewhere in the
suite. If it is, the direct fix (add the omitted-flag case to that existing pattern) is both
available and cheaper than reaching for the indirect one.

**Found:** `chela/release_notes.py`'s `_default_changelog_d_path()` (CMX-312 rework round 1,
PR #388) — judge mutation `changelog.d` → `changelog.d.disabled`, suite stayed green (3245
passed) because nothing called the function with the mutation in place. Round 2 (same PR):
`--date`'s default in `main()`, judge mutation above, suite stayed green (3246 passed) for
the same underlying reason — closed by
`test_cli_release_defaults_date_to_today` (`tests/test_release_notes.py`), which reuses the
existing `tmp_path` end-to-end pattern with `--date` omitted instead of pinning a resolver
function in isolation.

**Round 3 — the resolver-pin itself became the new unguarded gap:** round 1's fix
(`test_default_changelog_d_path_points_at_the_repos_own_changelog_d`) calls
`_default_changelog_d_path()` directly and asserts its return value — that pins the *helper*,
but says nothing about whether `argparse`'s `--changelog-d` option still uses that helper as
its `default=`. A corruption that repoints only the argument's `default=` kwarg (leaving the
helper function itself untouched and therefore still returning the right path when called
directly) reproduces the exact same silent-zero-fragments failure round 1 existed to close,
and the round-1 test can't see it — it never touches `main()`'s parser at all. Judge mutation:
`default=_default_changelog_d_path()` → `default=Path("changelog.d.disabled")` in the
`--changelog-d` `add_argument(...)` call; the whole suite stayed green (3247 passed) because
nothing ever built the real parser and read back what `--changelog-d` actually defaults to.
Closed by extracting parser construction out of `main()` into a standalone `_build_parser()`
function, then adding `test_changelog_d_argument_default_is_wired_to_the_resolver`, which
calls `release_notes._build_parser().parse_args([])` and asserts
`args.changelog_d == _REPO_ROOT / "changelog.d"` — this reads the value argparse actually
resolves the option to, pinning the helper and the wiring that consumes it in one assertion,
instead of asserting on the helper in isolation.

**The generalized lesson, restated:** a test that calls a default-producing helper function
directly and asserts on its return value pins the *function*, not the *option* — an
indirect fix like round 1's is a trap disguised as a fix unless a companion assertion also
drives the actual consumer (the built parser, or the CLI end-to-end) with the flag omitted.
When a sibling option's consumer is safe to run end-to-end (see round 2), prefer that
directly; when it isn't (as here — `--release` is destructive against the real repo), pin the
*wiring point* (the parser's resolved default) rather than stopping at the helper it calls,
so a mutation that only touches the `default=` kwarg — not the helper — still goes red.

**A second, unrelated shape survived the same round:** `promote_unreleased`'s docstring
claims the newly promoted `## [version] — date` section is inserted directly below
`## [Unreleased]` (newest-first) — this is load-bearing because `latest_released_version`
returns the first non-`Unreleased` heading it finds, and `tests/test_version.py` asserts
`_pyproject_version() == latest_released_version(_changelog_text())`. Every assertion in
`test_promote_unreleased_combines_existing_body_and_fragments` reached the promoted content
through `extract_release_notes(rewritten, "0.7.0")`, which finds a `## [0.7.0]` heading
*anywhere* in the text via regex search — so the test can prove the content is present
without ever proving *where*. Judge mutation: swap the concatenation order in
`promote_unreleased`'s `return` statement so the new section is appended after the old body
instead of inserted directly below `## [Unreleased]` (content unchanged, position reversed);
the suite stayed green (3247 passed) because no assertion depended on order. Closed by adding
`assert latest_released_version(rewritten) == "0.7.0"` to the same test — a search that finds
a heading by content is blind to a search that depends on the heading being *first*; when an
invariant is about position/order, assert through the function that actually depends on that
position, not through one that searches past it.

**Round 4 — the read-side helper used to inspect the result independently re-applies the
exact transform the write-side mutation removed, so the corruption never reaches the
assertion:** `promote_unreleased` merges duplicate `### <Category>` headings across the
boundary between the existing `## [Unreleased]` body and the collected `changelog.d/`
fragments by calling `_merge_duplicate_subheadings(combined + "\n")`. The only test that
reaches this call site read the result back through `extract_release_notes(rewritten,
"0.7.0")` — but `extract_release_notes` *itself* calls `_merge_duplicate_subheadings` on
whatever body it slices out (line 129), so any two `### <Category>` blocks it returns get
silently re-collapsed into one on the way out, independent of whether `promote_unreleased`
merged them on the way in. Judge mutation: `merged = _merge_duplicate_subheadings(combined +
"\n").strip("\n")` → `merged = (combined + "\n").strip("\n")` — i.e. skip the merge entirely
inside `promote_unreleased`. The raw `rewritten` text now genuinely contains two adjacent
`### Added` headings (verified by slicing the text directly, before it passes through any
reader), but every existing assertion routed the same text through
`extract_release_notes` first, which merged them right back into one — so `"already there"
in new_release` and `"from a fragment" in new_release` both still held, and the whole suite
stayed green (3248 passed).

**Why this is distinct from round 3's shape above:** round 3 was blind because the assertion
searched for content *anywhere* in the text, never checking position. This is a different
blindness: the assertion's own read path performs the *same normalization* the write path was
supposed to perform and didn't — two independent call sites of `_merge_duplicate_subheadings`
(one on write, one on read) mean a broken write call is invisible unless the read call is
bypassed. It also isn't shape 46 (idempotent gated action) — the gate here isn't a no-op by
construction, it's a no-op *because a second, independent application of the same idempotent
transform runs downstream of it and cleans up after it*.

**Guard form that survives:** when the only way to inspect a write-side transform's output is
through a reader that performs its own version of the same transform, that reader cannot be
used to prove the write side ran — slice and assert on the raw output directly instead. Fixed
here: `promoted_section = rewritten[rewritten.index("## [0.7.0]"):rewritten.index("## [0.6.0]")]`
followed by `assert re.findall(r"(?m)^### (.+)$", promoted_section) == ["Added"]`, reading the
un-normalized text `promote_unreleased` actually returned instead of routing it back through
`extract_release_notes` first.

**Found:** `chela/release_notes.py`'s `promote_unreleased` (CMX-312 rework round 4, PR #388)
— judge mutation `_merge_duplicate_subheadings(combined + "\n").strip("\n")` →
`(combined + "\n").strip("\n")`, suite stayed green (3248 passed) because
`extract_release_notes`'s own internal merge call absorbed the corruption before any
assertion could see it. Closed by asserting on the raw `rewritten` slice in
`test_promote_unreleased_combines_existing_body_and_fragments` instead of the
`extract_release_notes`-filtered `new_release`.

**Round 5 — every fixture happened to make the function's central claim true anyway, so the
one arm that makes it a *claim* (rather than an observation) was never run:**
`promote_unreleased`'s last two lines are a ternary on `merged`: content to promote gives
`## [X.Y.Z] — DATE\n\n<body>\n`; nothing to promote gives the bare heading `## [X.Y.Z] —
DATE\n`. That second arm is what the docstring's central sentence is actually about — "the
heading this function writes is always present, never a step a maintainer can forget" — and
it is the one arm no existing fixture reached: every test that calls `promote_unreleased` or
drives `--release` gives it either an existing `## [Unreleased]` body, a fragment, or both, so
`merged` was truthy in all six of them (the sixth raises before reaching the line at all).
Judge mutation: `f"## [{version}] — {date}\n\n{merged}\n" if merged else f"## [{version}] —
{date}\n"` → `f"## [{version}] — {date}\n\n{merged}\n" if merged else ""` — i.e. write
*nothing* when there's nothing to promote. The suite stayed green (3248 passed) because no
fixture ever exercised an empty `## [Unreleased]` with an empty `changelog.d/` — which is not
an edge case; it is the NORMAL steady state this PR creates *between* releases, since its
whole point is that fragments get deleted and `## [Unreleased]` stays empty until the next one
lands. Under the mutation, the documented `python -m chela.release_notes --release X.Y.Z` run
at exactly that moment would write no `## [X.Y.Z]` section at all, and a later `git tag` would
push a release whose `release.yml` extraction step then fails on a tag already live.

**Why this is distinct from rounds 3/4's shapes above:** those were blind *reads* — an
assertion that found real content through a search or a normalizing reader that couldn't see a
positional or duplication defect. This is a blind *fixture set* — every test that reaches the
line takes the same branch of a two-branch conditional, so the other branch has zero coverage
regardless of how the result is read back afterward. The tell is the same shape as catalogued
shape 40 (a defensive fallback branch is never hit), but inverted: the untested arm here isn't
defensive belt-and-braces sitting behind the "real" logic, it's the arm the surrounding
docstring's headline claim is actually about — the "always present" guarantee is exactly the
`else` branch, so leaving it untested left the function's one stated purpose unverified while
every other behaviour around it was covered in detail.

**Guard form that survives:** when a function's docstring makes an "X is always true, even
when Y" claim, check that some fixture actually constructs the Y case — a claim about a branch
that's never taken by any test is unverified regardless of how much the taken branch is
covered. Closed by `test_promote_unreleased_writes_the_bare_heading_when_theres_nothing_to_promote`:
an empty `## [Unreleased]` section with an empty `changelog.d/`, asserting the bare heading is
present via a plain substring check on the raw `rewritten` text (not through
`extract_release_notes`/`latest_released_version`, which would raise `ReleaseNotFoundError` —
an exception rather than a targeted assertion failure — if the heading were silently dropped).

**Found:** `chela/release_notes.py`'s `promote_unreleased` (CMX-312 rework round 5, PR #388) —
judge mutation `f"## [{version}] — {date}\n\n{merged}\n" if merged else f"## [{version}] —
{date}\n"` → `... if merged else ""`, suite stayed green (3248 passed) because no fixture ever
gave the function nothing to promote. Closed by
`test_promote_unreleased_writes_the_bare_heading_when_theres_nothing_to_promote` in
`tests/test_release_notes.py`.

**Round 6 — a two-element boundary list where every fixture happens to have only one
element, or the two happen to coincide, so `min` and `max` pick the same candidate:**
`promote_unreleased`'s body boundary is `body_end = min(candidates)` over
`candidates = later_heading_starts + ([footer] if footer != -1 else [])` — "whichever comes
FIRST: the next `## [...]` heading, or the `---` footer rule". `min` only differs from `max`
when `candidates` holds *more than one* element with *different* positions — i.e. the
changelog has both a later dated heading **and** a `\n---\n` rule below the `## [Unreleased]`
section being promoted. Every fixture through round 5 gave the function either zero
candidates (`candidates` empty) or exactly one (a later heading, no footer anywhere in the
fixture text) — `grep -n '\n---\n' tests/test_release_notes.py` matched no `promote_unreleased`
fixture. With a single-element (or empty) `candidates` list, `min` and `max` are the same
value, so the "whichever comes first" rule was never actually exercised in either direction.
Judge mutation: `body_end = min(candidates)` → `body_end = max(candidates)`. This is not
hypothetical: this repo's own `CHANGELOG.md` has exactly this shape (`## [Unreleased]`, one or
more dated sections, then a trailing `\n---\n` process note), and CONTRIBUTING.md's Releasing
step 1 now tells a maintainer to run `--release` against that exact file — under `max`,
`existing_body` would swallow every dated release section below `## [Unreleased]` into the
newest one, a strictly worse version of the `0.4.0` incident this module exists to close.
`latest_released_version` and `extract_release_notes(text, version)` both stay green through
the mutation for any fixture that never puts two differently-positioned candidates in the same
changelog, which is exactly what left it unpinned for five rounds.

**Why this is distinct from rounds 3/4/5's shapes above:** those were a blind *read* (an
assertion that searched past a positional/duplication defect) and a blind *fixture set* (every
fixture took the same branch of a two-way conditional). This is a blind *cardinality*: the
conditional here isn't binary, it's a `min`/`max` chosen from a list, and the two functions
are indistinguishable whenever every fixture's list has cardinality ≤ 1. The fix isn't "add
the missing branch" (round 5) or "add the missing category" (round 4) — it's "construct a
`candidates` list with two elements in each possible relative order."

**Guard form that survives:** when an invariant is phrased as "whichever comes first" (or
"last", "smallest", "closest") over a set of candidates, check how many fixtures actually
construct a set with more than one member — a `min`/`max` swap, or any other selection-order
bug, is invisible to every fixture whose candidate set never has more than one element,
regardless of how many such fixtures exist. Fixed here with two new fixtures, one for each
relative ordering of the two delimiters (footer-before-heading and heading-before-footer),
each asserting through a raw slice of the output (per round 4's lesson: `extract_release_notes`
re-derives its own — correct, unmutated — boundary on the result, so it's a valid oracle here,
but a plain substring/position check on the raw `rewritten` text is the more direct proof) that
the promoted body stops at the delimiter that's actually first in the source text, not at
whichever one the code happens to reach last.

**Found:** `chela/release_notes.py`'s `promote_unreleased` (CMX-312 rework round 6, PR #388) —
judge mutation `body_end = min(candidates)` → `body_end = max(candidates)`, suite stayed green
(3249 passed) because no fixture ever gave the function a changelog with both a later heading
and a footer rule at different positions. Closed by
`test_promote_unreleased_stops_at_a_footer_rule_that_precedes_the_next_heading` and
`test_promote_unreleased_stops_at_the_next_heading_that_precedes_a_footer_rule` in
`tests/test_release_notes.py`.
