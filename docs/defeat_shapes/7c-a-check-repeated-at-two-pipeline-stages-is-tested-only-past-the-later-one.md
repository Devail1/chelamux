## 7c. A check repeated at two pipeline stages is tested only past the later one, so the earlier copy can be deleted

**Assertion form:** CMX-7 validates a guest's access request twice. The token proxy's
`clean_request` checks it before writing a line to `requests.jsonl`, and `share_requests.ingest`
checks it again when it reads that line into the store. The test for a bad `access`
(`test_ingest_drops_a_line_with_an_unknown_access`) wrote the bad line **straight into
`requests.jsonl`** and asserted that `ingest` dropped it. Every proxy test posted a body with a
valid `access` (or no `access` at all, which defaults to `ro`). The suite therefore proved that
the *pipeline* rejects `rwx`. It never proved that the *first stage* does, and the first stage
is the one that controls what lands on disk.

The same PR repeated the mistake one level over, with a counter carried across calls.
`MAX_PER_SESSION` was tested by filing 55 requests and calling `ingest` **once**. A per-batch
counter (`count = 0`) and a per-session counter (`count = sum(...)` over the store) give the
same answer in a single batch, because the store is empty when the batch starts.

**Mutation that defeats it:** `or access not in ("ro", "rw"):` → `or False:` in
`clean_request`. The full suite stayed green: the downstream check still dropped the line, and
no test looked at the file between the two stages. The cap mutation
`count = sum(…)` → `count = 0` also stayed green, because nothing called `ingest` a second time
for a session that had already filed requests.

**Guard form that survives:** when a check is repeated on purpose (defense in depth), test
**each stage on its own**. Give each stage the bad input, and assert at that stage's output,
before the next stage can hide the mistake. For the proxy, that means POSTing every bad
`access` value and asserting a 400 **and** that `requests.jsonl` was never created. For any
limit whose state outlives one call, cross the limit **across two calls** (30 + 30 against a
cap of 50), so the second call starts with a non-zero count it has to read back.

Found by the judge on PR #591 (CMX-7, round 2). Guards:
`tests/test_share_requests.py::test_the_proxy_refuses_an_unknown_access_and_files_nothing`,
`::test_the_cap_holds_across_ingests_not_per_batch`.
