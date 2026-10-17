## 189. An opt-out flag is tested at the CLI only on inputs where it cannot change the answer

**Assertion form:** a gate has three outcomes: refuse (always), refuse unless an opt-out flag
is given, and accept. `chela task-finished` refuses a branch that CONFLICTS with its base,
refuses a branch that is merely BEHIND unless `--allow-behind` is passed, and accepts one that
is up to date. The library function (`check_base_sync(task_id, allow_behind)`) has a full
table test: all three states, flag on and off. The CLI tests drive three cells: conflict with
the flag (refused, because the flag never lets a conflict through), up to date without the
flag (accepted), and behind with the flag (accepted, recorded).

**Mutation that defeats it:** replace the CLI's flag read with a constant,
`check_base_sync(args.task_id, True)`. Every CLI cell still passes. A conflict is refused
whatever the flag says, an up-to-date branch is accepted whatever the flag says, and the
behind cell already passes the flag. The one cell where the flag decides the outcome (behind,
flag absent, must refuse) is covered only by the library test, which hands in `False` itself.
In production every behind branch walks through without the opt-out. Measured: CMX-189
round 1, survived an 8669-test suite.

**Why this is distinct from shape 397:** 397 is a stored value read once per process, so the
suite can only observe one value. Here nothing stops the suite from driving both flag values;
it does drive both, but each on an input where the flag is inert. Coverage of the flag "on"
and "off" looks complete when you list the cells, and it is not.

**Guard form that survives:** at the CLI, drive the cell where the flag is the ONLY thing
that decides: behind, no flag, assert refused (and nothing downstream ran). The general test: for each flag, there
must be a pair of CLI cells on the SAME input that differ only in the flag and get different
answers.

**Found:** CMX-189 round 1, alongside shape 338 in the same verdict. The daemon's `tick()`
forwarded the marker's `base_sync` to `mark_awaiting_review`, and the test called `mark_awaiting_review` by hand with
the marker's value, so `base_sync=None` at the tick call site survived. Fixed by driving
`tick()` over a marker (`test_tick_persists_the_markers_base_sync_on_the_run`).
