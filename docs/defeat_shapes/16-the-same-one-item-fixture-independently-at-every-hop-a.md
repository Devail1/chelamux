## 16. The same one-item fixture, independently, at every hop a list-shaped value passes through

**Assertion form:** a value that is a LIST travels through several functions on its way from
where it is produced to where it is finally acted on — extracted from a report, stored on a
row, read back, rendered into text, scanned against another list, printed. Shapes 13 and 15
each pin ONE hop of a chain like this. This shape is what happens when nobody asks the
question at the level of the whole chain: every hop was written assuming a list of length
one, every hop's test fixture independently happens to use a list of length one, and each
hop gets discovered and fixed on its own round, one at a time, because nothing forces the
question "does EVERY hop this value passes through make the same assumption?" to be asked
once, up front, for the whole pipeline.

**Mutation that defeats it:** truncate to `[:1]` at any hop not yet separately pinned.
Because each hop is independently guarded (or not) by its own fixture, fixing hop N tells
you nothing about hop N+1 — shape 13's fix (the enforcement-side scan) shipped in round 6 and
shape 15's fix (the render step) shipped in round 7, and FOUR more hops on the exact same
pipeline — the extraction from the judge's own report, the storage on the review row, the
submitted-side of the enforcement scan, and the final print loop — were still open in round
8, each because its own test suite's fixtures, built independently by different rounds,
happened to use a one-item list too.

**Guard form that survives:** don't fix hops as they're found one at a time. When a
list-shaped value is discovered to have this defect at ANY hop, walk the value's entire
journey — every function that receives it, stores it, or passes it on — and give every
fixture along the WHOLE chain a two-item list in the same pass, not just the hop the current
finding named.

**Found:** the REQUIRED MUTATION SET's seven-hop journey (CMX-269): `judge.judge_run`
extracting `blocking` from its own report (shape unfixed until round 8),
`dispatcher.request_changes` storing it on the review entry (unfixed until round 8),
`latest_required_mutations` reading it back (pinned from the start — every multi-item
fixture reaches it), `_required_mutations_section` rendering it into the brief (shape 15,
round 7), `_missing_required_mutations`'s required-side loop (shape 13, round 6), the same
function's submitted-side `submitted_keys` (unfixed until round 8), and
`main.cmd_task_finished`'s print loop (unfixed until round 8). Two rounds each closed one
hop; round 8 closed the remaining four in a single pass —
`test_the_REQUIRED_MUTATION_SET_carries_every_survivor_not_just_the_first` (one test,
closing the source and storage hops together, since both sit on the same call chain),
`test_verify_self_check_clears_when_the_required_mutation_is_resubmitted_second_in_the_list`,
and `test_cmd_task_finished_prints_every_missing_required_mutation_not_just_the_first`.
