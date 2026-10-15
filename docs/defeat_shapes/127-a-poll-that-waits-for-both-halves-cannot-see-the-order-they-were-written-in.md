## 127. A poll that waits for both halves of a state change cannot see the order they were written in

**Assertion form:** a background thread finishes by writing two fields a client polls — a
result (`last_result`) and a busy flag cleared (`applying = False`). The real client stops at
the FIRST read where the flag is clear and takes the result from that same read, so the result
must be written first. The tests poll with a predicate over BOTH halves —
`last_result.run == run and not applying` — then assert the result.

**Mutation that defeats it:** swap the two writes in the thread's `finally`, clearing the flag
before recording the result. A real client polling in that window reads "done" with the
previous run's result (or none) and reports the wrong outcome.

**Why it looks covered:** the test's wait predicate is stricter than the client's. It keeps
polling until both writes have landed, so it passes in either order — the window the swap
opens is exactly the one the predicate skips over. Once both writes land, every read agrees.

**Guard form that survives:** check the invariant at every step of the writer, not at the end.
`threading.settrace` a line tracer onto the writer thread and, on each line where the flag is
already clear, record the result field; assert every record is this run's. Deterministic, no
sleeps, and red under the swap. More generally: when a reader acts on a *partial* state, the
test must read the partial states too, not wait for the final one.

**Found:** CMX-127 rework round 2, PR #680 (held-out survivor; this was the likeliest
candidate). `chela/dashboard/app.py` `api_update_apply()`'s `_run` `finally`; fixed in
`tests/test_update_apply_route.py` (`test_a_pull_records_its_outcome_before_applying_clears`). The same
round also pinned `/api/update/status`'s `Cache-Control: no-cache` and `_BOOT_ID`'s pid
identity, which no test read.
