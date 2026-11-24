## 494. A value is asserted on the pure function and on a hand-written record, never on what the real caller passed or wrote

**Assertion form:** CMX-494 gave a suite-slot waiter a second sort field, `task_rank`. The order
was tested by calling `_must_queue(path, since, 0, 1)` directly, over ledgers the test wrote. The
holder's record was tested by writing `slot-0` by hand with `task_rank: 2` and reading it back,
plus one real holder whose `task_rank` equals its `rank` (the reader then derives it, so the field
is dead). The inbox event was tested field by field on its payload, but its `wid` was never read,
although every fixture run had a `window_id`.

**Mutation that defeats it:** drop the last argument at the one real call
(`_must_queue(ledger.path, since, ledger.rank)`), write `"task_rank": None` into the real holder's
record, or pass `wid=None` to `_event(...)`. Each leaf still works and the suite stays green.

**Guard form that survives:** drive the real acquirers and assert the far end: who was ADMITTED
first (judge rounds started in the reverse of their rank, so arrival order is wrong), and the
whole record `held()` reads back from a slot a judge really took, including the label built from
it. Use the one case where the two fields DIFFER (a judge: `rank` 0, `task_rank` 1). For an event,
compare the whole payload and the attribution, with two subjects whose fields all differ, and run
it through the tick once with a stale window id, so the window table and the tmux epoch the tick
passes both decide the answer.

Found by the judge on PR #1008 (CMX-494, round 1). Guards, in `tests/test_slot_queue_rank.py`:
`test_judge_rounds_are_admitted_by_their_tasks_rank`,
`test_a_real_holders_record_says_whose_rank_it_holds_at`,
`test_the_event_is_attributed_to_the_waiting_runs_window`,
`test_a_daemon_tick_queues_the_notice`.
