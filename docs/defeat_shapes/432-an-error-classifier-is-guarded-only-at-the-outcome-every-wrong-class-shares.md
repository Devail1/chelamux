## 432. An error classifier is guarded only at the outcome every wrong class shares

**Assertion form:** code sorts a failure into one of several kinds, and each kind fails the
read the same way but does something different on the side. Linear's adapter turns a
`RATELIMITED` GraphQL error on an HTTP 400 into `rate_limited`, which starts the back-off,
rather than `graphql`/`http`/`malformed`, which do not. The test is parametrized over every
failure and asserts the contract the brief stressed: `fetch_by_ids(...) is None`, never `[]`.
That looks exhaustive because the RATELIMITED case is right there in the table.

**Mutation that defeats it:** misclassify it. `if code == "RATELIMITED"` → `"RATE_LIMITED"`
sends it to `graphql`, which is still a failed read, so `None` comes back and the table stays
green. What changed is the side effect, and nothing observes it: no back-off, so the daemon
hits a rate-limited API again on every tick. The same applies one level down. A back-off
whose length is never read survives `2 ** strikes` → `strikes + 1` and a dropped
`Retry-After`, and a cap on archives per sweep survives `>=` → `>` when the fixture has
fewer issues than the cap.

**Guard form that survives:** assert the thing that tells the classes apart, not the outcome
they share. Check the `kind` the transport raises. Then check the consequence only the right
kind has: the back-off entry exists, its window is the expected length on an injected clock,
and a second source in the same window does not call the transport. For a cap, use a fixture
larger than the cap and count the mutations. For a growth curve, read every step, including
the clamp and the reset after a good read.

**Found:** CMX-432 round 1 (2026-10-02), PR #583 — `chela/sources/linear.py`
(`_raise_graphql_errors`, `_call`'s back-off, `archive_sweep`). Closed by
`tests/test_linear_source.py::test_a_graphql_ratelimited_error_is_a_rate_limit`,
`test_back_off_grows_exponentially_with_consecutive_strikes`,
`test_back_off_honours_retry_after` and
`test_a_sweep_archives_at_most_archives_per_sweep_issues`.

**Related:** [407b](407b-an-only-x-is-final-rule-is-tested-on-two-of-a-three-valued-outcome.md)
enumerates the values but not what each one does. This shape enumerates them and then asserts
only what they have in common.
