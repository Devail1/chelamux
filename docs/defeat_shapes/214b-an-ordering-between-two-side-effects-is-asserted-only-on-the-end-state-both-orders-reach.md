## 214b. An ordering between two side effects is asserted only on the end state both orders reach

**Assertion form:** a command must do A *before* B because of what is true in between (send
"resume" while the queue hold still stands, so no tick claims mid-resume). The test runs the
command and asserts the end state: A happened, B happened. Both orders reach that end state,
so the test cannot see which order ran.

**Mutation that defeats it:** swap A and B. CMX-214 (`chela/main.py`, `cmd_dispatch_hold`):
`hold.release(...)` was moved above `fleet.resume_all()`. The resume test still saw every
"resume" message sent and the hold gone, and stayed green. The same PR's count line
`{sum(sent.values())} of {len(sent)}` survived `{len(sent)} of {len(sent)}` too, because every
fixture delivered every message, so the two numbers coincided ([[342|shape 342]]).

**Guard form that survives:** make the fake for the later-checked side effect record the
state the ordering protects *at the moment it fires*, then assert that recorded state, not
the end state. CMX-214 now has
`test_resume_messages_while_the_hold_stands_and_counts_only_deliveries`
(`tests/test_fleet_pause.py`): its `send_message` fake records `hold.active() is not None` per
call and asserts `[True, True]`, and one of the two messages fails so the delivered count
(1 of 2) differs from the total.

**See also:** [[127|shape 127]] (a poll that waits for both halves cannot see their write
order) and [[167|shape 167]] (every trigger fired in one step before a single assertion).
