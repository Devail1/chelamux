## 411b. A re-exec test asserts its marker flag reached the child, not every flag the caller gave

**Assertion form:** `chela judge run --detach` re-execs itself as a detached child. Round 1's
fix for [[411|shape 411]] drove `--detach` through `sys.argv` → `main.main()`, faked only the
spawn, and asserted that a child was spawned with `"--detached-child" in argv`. A separate
unit test asserted `detached_argv(..., cleanup=False)` contains `--no-cleanup`. So the CLI was
reached and the argv builder was right about `--no-cleanup`, and the test of the CLI only
looked for the marker the re-exec adds.

**Mutation that defeats it:** drop a flag the CALLER passed at the handler, before it reaches
the argv builder.

```diff
- judge.detach_judge_run(args.run, args.experiments, cleanup=not args.no_cleanup)
+ judge.detach_judge_run(args.run, args.experiments, cleanup=True)
```

The suite stays green (4606 passed). The CLI test never passed `--no-cleanup`, and the test
that did pass it called `detached_argv` directly. In production, `chela judge run --detach
--no-cleanup` (debugging a judge by hand) deletes the worktree and kills the window anyway,
because the child is the process that cleans up and it never got told not to. (CMX-411, PR
#562, round 2.)

**Guard form that survives:** pin the child's ENTIRE argv with `==` in the CLI-driven test,
and parametrize it over every flag the caller can give (`()` and `("--no-cleanup",)` here).
A membership check on the one flag the feature adds says nothing about the flags it is meant
to forward. Do the same at the receiving end: drive the child's own flags
(`--detached-child`, `--no-cleanup`, all four combinations) through `main.main()` and assert
the exact kwargs `judge_run` received.

**Why this is distinct from [[411|shape 411]]:** in 411 no test joined the flag to the call.
Here one flag (`--detach`) is joined end to end, and the test's assertion is narrow enough that
a SECOND flag riding through the same call is left unguarded. Fixing a defeat shape for the
flag the verdict named does not cover the other arguments on the same line.
