## 365c. A source's sort fix was tested at the source, but the relay that carries that order into the claim queue was never exercised in isolation

**Assertion form:** `test_oldest_issue_is_dispatched_first` and `test_a_null_created_at_sorts_last`
(both in `tests/test_gh_issues_order_and_body.py`) call `GhIssuesSource.list_open_tasks()`
directly and assert its output order. That closes shapes [[365]] and [[365b]] on the sort
key itself, but nothing in the suite calls `chela.dispatcher._claim_order` — the function
whose own docstring makes the actual G3 claim ("`_claim_order` preserves whatever order
the source returns, so the newest issue was always claimed first and the oldest starved
forever"). `_claim_order` has five branches; a `gh_issues`-shaped source (no `path`,
`tasks_from_text`, or `closed_ids_from_text` — it reads the live API on every call and is
already claim-fresh) takes the very first one, an early return before any of the git-fetch
machinery even runs. Every existing `_claim_order` test (`tests/test_dispatcher_depends.py`)
uses a `markdown`-tracker source, so it always has `path` and never reaches this branch.

**Mutation that defeats it:** `chela/dispatcher.py`, `_claim_order`'s first branch:

```diff
-         return _ready(on_disk, set())
+         return _ready(on_disk[::-1], set())
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3983 passed, exit 0): the
`gh_issues` sort-order tests never call `_claim_order`, and every test that *does* call
`_claim_order` uses a source that never reaches the mutated line, so nothing in the suite
could ever see the reversal.

**Guard form that survives:** test the relay function directly, with the specific source
shape (no `path`/`tasks_from_text`/`closed_ids_from_text`) that actually takes the branch
in production, independent of any particular source implementation's own sort tests. A
sort fix proven correct at the source is not the same claim as "that order survives into
what gets claimed" — the two must be pinned by separate tests, one per hop, because a
mutation on either hop alone leaves the other hop's test green.

**Found:** CMX-365 rework round 3 (2026-09-13), PR #500. The judge's required-mutation-set
verdict named this mutation as surviving with the whole suite green. Closed by adding
`test_claim_order_preserves_on_disk_order_for_a_pathless_source` in
`tests/test_dispatcher_depends.py`, which calls `_claim_order` directly with a bare
`SimpleNamespace()` source and asserts on-disk order round-trips unchanged.
