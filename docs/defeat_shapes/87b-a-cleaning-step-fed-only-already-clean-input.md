## 87b. A cleaning step is fed only input that is already clean

**Assertion form:** a function normalizes its input before using it (`" ".join(theme.split())`,
`version[1:] if version.startswith("v")`, `(version or "").strip()`, `body.strip()`), and the
tests call it with the value a well-behaved caller passes (`"a calmer release"`, `"0.2.0"`,
`"Longer notes."`). Every assertion about the commit subject, the tag, the card and the stored
record is correct for clean input.

**Mutation that defeats it:** weaken or delete the normalization — `" ".join(x.split())` →
`x.strip()`, drop the `v` strip, `.strip()` → identity. For clean input the cleaned and the
uncleaned value are byte-identical, so the whole suite stays green while a multi-line theme
now writes several lines into a commit subject, and `chela release v0.2.0` is refused or
becomes `vv0.2.0`.

**Why it is easy to miss:** the step has no branch to cover — line coverage and the mechanical
if/return/call sweep both see it executed. Only a VALUE mutation exposes it. The same holds for
derivations with a dedup (`if tid not in seen`) and for constants inside a command line (a
`-p no:cacheprovider` flag whose effect is a file NOT being created): the happy path cannot
tell the present from the absent.

**Guard form that survives:** for each normalization, one test whose input is dirty in EVERY
way the step cleans (leading/trailing blanks, interior runs, newlines, tabs, the prefix, the
empty/None case, a repeated item) plus the near-miss it must NOT clean (`vv0.2.0`, `V0.2.0`),
asserting the exact observable output at every place it lands (commit subject, tag message,
stored record, card). For a flag whose effect is an absent side effect, run the real command
(minus its wrapper) and assert the artefact is not created, with a control run that proves the
artefact WOULD be created without the flag.

**Found:** CMX-87 (`chela/release.py` `prepare()`, `task_ids()`, `_release_tests()`), guarded in
`tests/test_release.py` — `test_a_multi_line_theme_is_collapsed_to_one_line_everywhere`,
`::test_a_version_is_cleaned_to_the_same_release`, `::test_task_ids_are_unique_in_first_appearance_order`,
`::test_the_release_tests_command_leaves_no_pytest_cache`.
