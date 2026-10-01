## 411. A CLI flag's translation into a kwarg is skipped, because every test passes the kwarg itself

**Assertion form:** a feature is switched on by a CLI flag (`chela judge run --detach`, and the
hidden `--detached-child` the detach re-execs). The handler turns the flag into a call:
`if args.detach: judge.detach_judge_run(...)` and
`judge.judge_run(..., detached=getattr(args, "detached_child", False))`. The tests are thorough
about what happens next. They call `judge.judge_run(..., detached=True)` and
`judge.detach_judge_run(...)` directly and assert the lock is marked detached, the child gets
its own session, the watchdog stops it on a timeout. The subcommand's dispatch line is driven
too, because other tests (`test_cmd_judge_prints_…`) already go through `main.main()`.

**Mutation that defeats it:** cut the flag off from the call inside the handler.

```diff
- detached=getattr(args, "detached_child", False)
+ detached=False
```

```diff
- if getattr(args, "detach", False):
+ if False and getattr(args, "detach", False):
```

The suite stays green (4594 passed). The tests that go through `main.main()` never pass the
flag, and the tests that pass the flag's value never go through `main.main()`. In production,
the detached child's claim says `detached: false`, so `stop_judge_run` refuses to stop it on a
timeout and the watchdog deletes its worktree from under a live battery. With the second
mutation, `--detach` runs the whole battery in the agent's Bash tool again, which is the bug
the feature was written to fix. (CMX-411, PR #562, round 1.)

**Guard form that survives:** drive the flag through `sys.argv` → `main.main()` and assert
the invariant at its far end, not the call. Here that means reading the claim file back
mid-battery and asserting `detached is True` under `--detached-child` and `False` without it.
For `--detach`, assert that the battery never ran in this process (`judge_run` not called)
and that a child was spawned. Add the counterweight for each flag: the same command without
the flag must give the other outcome, so an always-on mutation is caught as well.

**Why this is distinct from [[368|shape 368]]:** in shape 368 no test reaches the subcommand at
all. Here the subcommand is reached, and so is the function behind the flag, but never in the
same test. Each half is covered and the line that joins them is not.

**Same round, same root:** a new conjunct added to an existing reap arm
(`login_expired = alive and run_started is None and <pane shows banner>`) was never tested with
the other conjuncts true. The new tests' watchdog fixture always returned an empty pane, so
`run_started is None` could be deleted without changing any result. To guard a new conjunct,
make every other conjunct true in the fixture and flip only the new one.
