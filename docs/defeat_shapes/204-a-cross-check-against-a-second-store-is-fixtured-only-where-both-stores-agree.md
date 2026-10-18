## 204. A cross-check against a second store is fixtured only where both stores agree

**Assertion form:** auto-topics reuses a folder's CLOSED topic from the topic ledger
(`chela/topic_ledger.py`) before creating one, and `_reopen_for_cwd` also skips any closed
topic the binding registry still has bound (`registry.window_for_thread(t) is None`). The
reuse tests built every ledger and registry through the reconcile itself, so whenever a topic
was closed in the ledger it was also unbound in the registry. Separately, the "oldest first"
test opened its topics in the same order the clock stamped them, so the ledger's dict
(insertion) order already was the sorted order.

**Mutation that defeats it:** drop the registry filter (`free = list(ledger.closed_for(cwd))`),
or drop the `sorted(...)` in `closed_for`. Both stay green: with the two stores in lockstep
the filter never removes anything, and with insertion order equal to creation order the sort
never moves anything.

**Why it looks covered:** each guard is real code with a real test nearby, and the happy path
it protects is tested end to end. But a check of store A against store B only does work when
A and B disagree, and no fixture ever made them disagree (here: a rebind binds a topic the
ledger still lists as closed). Same for a sort: it only does work when the input is not
already in order (see [[365b]]).

**Guard form that survives:** for a cross-check, build the disagreeing state by hand (ledger
says closed, registry says bound) and assert the second window gets a fresh topic and the
bound one is never reopened. For a sort, pick a key order that differs from insertion order
AND from the id order, and assert the consumer (the reconcile's reopen call), not only the
sorted list.

**Found:** CMX-204 rework round 1, PR #756 (held-out survivor; these were the likeliest
candidates). Fixed in `tests/test_telegram_topic_reuse.py`
(`test_reuse_skips_a_closed_topic_that_is_still_bound`,
`test_reuse_picks_the_oldest_closed_topic_of_the_folder`). The same round pinned the ledger's
recorded fields and times, the atomic save, the archived count (closed only), and the
cleanup confirm text.
