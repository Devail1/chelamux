## 313. A sibling's "destructive, so it can't be pinned end-to-end" excuse gets borrowed by a default that was never actually destructive

**Assertion form:** a CLI has two optional flags with the same shape — `some_path_helper()`
or `today()` supplies the default when the flag is omitted — feeding the same destructive
subcommand (`--release`, which overwrites `CHANGELOG.md` and deletes fragment files). One
default (`--changelog-d`) gets fixed by pinning its resolver function directly, with a
documented reason: an end-to-end test would have to omit the flag while running `--release`
against the real repo, and `--release` is destructive, so that's unsafe. The *other* default
(`--date`) is left exactly as unpinned as the first one was — every test that reaches it
supplies `--date` explicitly — under the same unstated assumption, except it's wrong for this
flag: every existing `--release` test already runs against a `tmp_path` `CHANGELOG.md`/
`changelog.d`, not the real repo, so an end-to-end test omitting just `--date` is exactly as
safe as the ones already in the file. The sibling that got the harder, indirect fix
(function-level pin) makes the one that only needed the *easy* fix (drop one flag from an
existing `tmp_path` test) look like it must need the same treatment, or already got it.

**Mutation that defeats it:** `date = args.date or _date.today().isoformat()` →
`date = args.date or "1970-01-01"` in `chela/release_notes.py`. Every `--release` test in the
suite passes `--date` explicitly, so the `or` branch computing today's date is never
evaluated; a release cut with the documented no-flags invocation
(`python -m chela.release_notes --release X.Y.Z`) would silently stamp every new
`## [version]` heading `1970-01-01` and the suite stays green throughout.

**Why the sibling's fix doesn't transfer, and why that matters here:** `_default_changelog_d_path()`
(shape 312) genuinely couldn't be pinned by omitting the flag in a `--release` test, because
doing so would run `--release` with no `--changelog-d` against this repo's *real*
`changelog.d/` — destructive, so the fix went indirect (pin the resolver function in
isolation instead). `--date`'s default has no such obstacle: `test_cli_release_writes_the_changelog_and_deletes_consumed_fragments`
already runs `--release` end-to-end against a synthetic `tmp_path` changelog with `--date`
supplied. Nothing about omitting `--date` from that same call makes it touch the real repo —
the destructive part is contained by `tmp_path` either way. Treating "the sibling needed an
indirect fix" as "so this one probably does too" skips the one-line check (does *this*
flag's already-tested consumer actually reach real files?) that would have shown the direct,
easy fix was available all along.

**Guard form that survives:** when a shape's fix goes indirect for a stated reason
(destructive consumer, no safe end-to-end path), re-verify that reason against every sibling
individually before deciding they all need the same fix — check whether the sibling's own
consumer is *already* exercised end-to-end through a safe fixture (like `tmp_path`) elsewhere
in the suite. If it is, the direct fix (add the omitted-flag case to that existing pattern) is
both available and cheaper than reaching for the indirect one.

**Found:** `chela/release_notes.py`'s `--date` default in `main()` (CMX-312 rework round 2,
PR #388) — judge mutation `_date.today().isoformat()` → `"1970-01-01"`, suite stayed green
(3246 passed) because every `--release` test supplied `--date` explicitly, right next to
round 1's `_default_changelog_d_path()` fix that had already established the `tmp_path`
end-to-end pattern this default could have reused directly.
