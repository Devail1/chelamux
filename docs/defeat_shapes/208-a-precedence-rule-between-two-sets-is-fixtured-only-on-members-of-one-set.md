## 208. A precedence rule between two sets is fixtured only on members of one set, so "who wins" is never asked

**Assertion form:** a classifier checks set A before set B and returns A's tier on any hit,
so a member of both must come out as A. Here A is the merge gate's ALWAYS-HUMAN paths and B
is PROSE. The test pairs every A path with a B file (`["docs/x.md", path]`) and asserts
`human`. It passes for every default path.

**Mutation that defeats it:** consult A only when B does not already cover the whole list:
`hit = next(…)` → `hit = None if all(is_prose(f) for f in files) else next(…)`. The suite
stays green.

**Why it looks covered:** none of the default A paths is itself in B (`WORKFLOW.md` is
carved out of prose, and `.github/…` and `plugin/…` are code), so every fixture list has a
non-B member and "A first" agrees with "B first". The two sets overlap only through
configuration: a workflow adding a `docs/**` path to `merge.always_human`. No fixture
configured that. The same PR also left the call sites unchecked. The gate and the
dispatcher both build A from the workflow, and both could drop the workflow's list
(`always_human(wf)` → `always_human(None)`) and stay green, because the workflow list was
unit-tested only. That half is [[387|a refusal chain fixtured only on the default config]].

**Guard form that survives:** fixture an element that is in both sets, assert that A wins,
and add a negative control showing the same element classifies as B when it is not in A.
Then repeat that at every call site that builds the sets from configuration, with a real
config that adds the overlapping member: the merge gate (a WORKFLOW.md on disk) and the
dispatcher's judge spawn (a `WorkflowDef` with `merge.always_human`).

**Found:** CMX-208 rework round 1 (2026-10-18), PR #755. Closed in `tests/test_risktier.py`
(`test_an_always_human_path_wins_even_when_it_is_itself_prose`), `tests/test_contract.py`
(`test_merge_gate_classifies_against_the_workflows_own_always_human_paths`) and
`tests/test_judge_docs_only_park.py`
(`test_a_workflow_always_human_prose_path_is_never_prose_skipped`).
