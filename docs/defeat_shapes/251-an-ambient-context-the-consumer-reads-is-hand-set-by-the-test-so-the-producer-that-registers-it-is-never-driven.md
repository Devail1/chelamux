## 251. An ambient context the consumer reads is hand-set by the test, so the producer that registers it is never driven

**Assertion form:** a callee reads a module-global context instead of a parameter
(`judge.run_suite` credits the freeze of `_run_ctx["task"]`, the task this process is
judging). The guard proves the callee's behaviour by setting that context itself —
`monkeypatch.setitem(judge._run_ctx, "task", "T9")` — and asserting the suite deadline moves
out by T9's frozen time. It is green, and it says nothing about the one line in production
that makes the context true: `judge_run`'s `_run_ctx["task"] = task_id`. This is shape
[[75b]] (a dropped wiring kwarg degrades to a plausible default) with no kwarg to look for.
The registration is a bare statement whose effect is read three calls away, and an
unregistered run falls back to "no credit", which is the OLD behaviour, so no other test
sees a difference. The judge's battery found
`_run_ctx["task"] = task_id` → `pass` SURVIVED on the full suite (CMX-251).

**Mutation that defeats it:** delete or no-op the producer's registration
(`_run_ctx["task"] = task_id` → `pass`), or move it after the call that reads it.

**Guard form that survives:** drive the real producer (`judge.judge_run` on a real
experiments file) with a ledger that holds a known, non-zero credit for THAT task, spy on
the one boundary the context feeds (`procguard.run_shell_group`'s `frozen=`), and assert the
exact credit there (`42.0` for a `[100, 142]` interval), never just "a callable was passed".
Then assert the context is gone once the producer returns, so a leak into the next run is
red too. Keep the hand-set unit test for the callee's arithmetic. It cannot stand in for the
wiring test.
