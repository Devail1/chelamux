## 296b. Two workflow-extension lists feed one call, and only one list has a test row

**Assertion form:** a classifier takes two lists, and the workflow can extend each one:
`risktier.classify(files, risktier.always_human(wf), risktier.core(wf))`. The tests give the
workflow a `merge.core` addition and assert the path becomes `core`. Nothing gives it a
`merge.always_human` addition. A second form showed up next to it: a blank config value
(`hand_in.lint_cmd`) was tested only as an absent key (`None`), never as whitespace.

**Mutation that defeats it:** swap the untested argument for its default:
`risktier.always_human(wf)` → `risktier.always_human(None)`. The built-in always-human list
still applies, and every fixture path either matches a built-in or is plain standard, so the
suite stays green. A workflow's own always-human path is then classified as standard and skips
the judge. The same happened with `lint.strip()` → `lint`: a `None` lint becomes `""` either
way, so only `'   '` can tell the two apart, and no test used it.

**Guard form that survives:** write one row per extension list that reaches the call. The row
puts a path only in that list (not in any default) and asserts the tier it gives (`human`). Do
this at every call site that classifies (here three: `hand_in_tier`, `_stamp_prose_only`,
`_spawn_judge`), because each site builds its own arguments. For a normalized config value, feed
the form the normalization exists for (whitespace), not only the absent key.

**Found:** CMX-296 round 2 (2026-10-21), PR #809, `chela/dispatcher.py` (`hand_in_tier`,
`verify_standard_hand_in`; the pre-slot stamp was flagged as flaky). Closed by
`tests/test_standard_no_judge.py::test_hand_in_tier_reads_the_runs_own_worktree[workflow-always-human]`,
`::test_a_workflow_core_path_takes_the_judge`, `::test_spawn_judge_stamps_standard_and_judges_core[workflow-always-human]`
and `::test_a_blank_lint_is_skipped_never_run_as_an_empty_command`.

**Related:** [103c](103c-a-guard-pins-one-instance-of-a-category-so-its-sibling-mutations-survive.md):
there one instance of a category was pinned and its siblings were not. Here the sibling is a
second argument to the same call.
