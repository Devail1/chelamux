## 86. A compensating undo on a failure branch is tested as a bare helper, while the end-to-end test drives only the success branch

**Assertion form:** a loop does `announce(X)` → `try_the_thing()` → on refusal, `retract(X)`.
One test calls `retract` directly and asserts the announcement is gone; a second test drives
the real loop end to end and asserts the announcement fired. Both are honest, both go red if
their own target breaks — and together they read as "announce and retract are both covered".

**Why that's a gap:** neither test ever reaches the line that *calls* `retract`. The helper
test skips the loop; the loop test's fixture lets `try_the_thing()` succeed, so the refusal
branch (the only caller of `retract`) is never entered. A sibling branch the loop takes for a
different input (here: an infra-only red that is reported but deliberately NOT acted on) has
the same problem — the one fixture feeds the common branch, and each side branch's call is
dead to the suite.

**Mutation that defeats it:** replace the call in the refusal branch with `None` (or delete
the side branch's `announce`). The helper still works when called; the loop still announces on
the happy path; the suite stays green — while in production a refused action leaves a stale
announcement that silently swallows the next real event it was standing in for.

**Guard form that survives:** drive the loop itself once per branch that carries a call —
stub `try_the_thing` to refuse (and, separately, to raise), assert the retraction happened,
AND assert the downstream consequence the undo exists for (the next genuine event still gets
through). Pair it with the success branch asserting the announcement is *kept*, so neither
side of the branch rides on the other's fixture. Same for a delivery-time "has this rotted?"
predicate: feed it a queued event whose claim has actually gone stale, per rot condition — a
predicate no test ever hands a rotted input is an always-`None` function to the suite.
Related: [[07|shape 7]] (two callers, one guarded), [[7c|shape 7c]].

**Found:** CMX-86 rework round 1 (2026-10-12), PR #655 — `dispatcher.tick`'s CI loop: the
CAS-refused and DB-refused branches' `_inbox_forget_ci_edge`, the infra-red branch's
`_inbox_ci_red`, and `inbox._cmx86_stale`'s `run_ci_red` head-moved check all survived. Closed
in `tests/test_inbox_cmx86.py` by `test_a_cas_refused_ci_rework_retracts_the_echo_at_the_call_site`,
`test_a_db_refused_…`, `test_an_infra_red_is_a_ci_red_notice_too_but_not_sent_back`, and the
`test_a_queued_*_rots_*` tables.
