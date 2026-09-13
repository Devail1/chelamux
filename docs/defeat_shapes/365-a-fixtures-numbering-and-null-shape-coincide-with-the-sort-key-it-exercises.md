## 365. A sort-order fixture's issue numbers and null-shape coincide with the very key the sort is supposed to exercise

**Assertion form:** `test_oldest_issue_is_dispatched_first` built three issues whose
`createdAt` values were oldest-to-newest in the same direction as their issue numbers
(oldest issue had the lowest number, newest the highest), then asserted the returned
order was oldest-first. `test_a_null_created_at_sorts_last` covered "no usable timestamp"
with a single JSON `null`, and asserted that issue sorted last.

**Mutation that defeats it:** two, both against `chela/sources/gh_issues.py`'s sort key
`(created_at is None, created_at or "", int(number))`:

1. `(created_at is None, created_at or "", int(number))` → `(created_at is None, "",
   int(number))` — drops the timestamp from the sort entirely, leaving the `int(number)`
   tie-breaker as the *de facto* sole ordering. Because the fixture's numbering already ran
   in the same direction as the dates, sorting by number alone reproduced the exact same
   output the test asserted — the fixture couldn't tell "sorted by date" from "sorted by
   number that happens to correlate with date."
2. `created_at = issue.get("createdAt") or None` → `created_at = issue.get("createdAt")` —
   drops the normalization that folds a falsy-but-not-`None` timestamp (empty string) into
   the same "no usable timestamp" bucket as `None`. The only fixture case for "no usable
   timestamp" was a JSON `null`, which is already `None` straight out of `json.loads` —
   `issue.get(...)` returns `None` either way, so the mutation had literally nothing to act
   on. The empty-string shape the assignment line exists to handle was never exercised.

**Guard form that survives:** for a sort key with more than one component, choose fixture
values where the components actively *disagree* — here, give the newest issue the lowest
number and the oldest issue the highest, so a guard that silently degrades to the
tie-breaker produces a *visibly wrong* order instead of accidentally reproducing the
correct one. For a normalization that maps several distinct "bad" input shapes onto one
sentinel, exercise more than one of those shapes — `None` and `""` both claim to hit the
same code path here, but only `""` actually passes through the `or None` normalization at
all; a fixture built from only one of them can leave the normalization itself unexercised
even while the "sorts last" behavior it enables still (trivially) passes.

**Found:** CMX-365 rework round 1 (2026-09-13), PR #500. The judge's required-mutation-set
verdict named both mutations as surviving `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
(3983 passed, exit 0) unchanged. Closed by re-numbering the fixture's issues so date order
and number order run opposite directions, and by adding an empty-string `createdAt` case
alongside the existing `null` one; re-applying each mutation by hand now turns its
respective test red while the other stays green.
