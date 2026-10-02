## 431. A named-event hook is tested on one side, and for only some of its names

**Assertion form:** a fix threads a callback `hook(event, **info)` from a long-running
producer (the judge battery) to a consumer (the closure in `judge_run` that folds each event
into the status json the watchdog reads). The producer test records the events and asserts
SOME of the names it emits (`"baseline"`, `"confirm"`, `"consistency"`); the consumer test
asserts SOME of the fields the closure writes (`baseline_seconds`, `progress_at`). Each
assertion is real. Together they look like the hook is covered end to end.

**Mutation that defeats it:** two, one per gap.
- *Consumer arm renamed* — `elif event == "confirm":` → `"confirmed"`. The producer test
  still sees `"confirm"` emitted; nothing ever drives the consumer with it and reads back
  `confirmations`, so the wall stops budgeting full-suite runs and the suite stays green.
- *An emission nobody counts* — `heartbeat("experiment")` dead-coded. Every assertion names
  some OTHER event; the one emission that moves `progress_at` during the consistency re-run
  (which passes no `progress` callback) is gone, and a slow re-run reads as stuck.

**Guard form that survives:** treat the event-name string as a contract with two ends and
pin BOTH ends for EVERY name. Producer side: assert the count (and position) of each name the
docstring promises, not just the ones the feature story mentions. Consumer side: drive the
REAL consumer with each name and read back the field that name is supposed to change —
including a negative (a non-`confirm` event must not count as one). The name list in the hook's
docstring is the checklist; a name with an assertion on only one side is unguarded.

**Found:** CMX-431 round 1, 2026-10-02 — both mutations survived a 5121-test suite.
