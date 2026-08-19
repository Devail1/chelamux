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

**Found:** `chela/release_notes.py`'s `_default_changelog_d_path()` (CMX-312 rework round 1,
PR #388) — judge mutation `changelog.d` → `changelog.d.disabled`, suite stayed green (3245
passed) because nothing called the function with the mutation in place.
