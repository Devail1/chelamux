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
