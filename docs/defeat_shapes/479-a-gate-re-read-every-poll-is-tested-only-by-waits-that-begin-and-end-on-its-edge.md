## 479. A gate re-read on every poll is tested only by waits that begin inside it and end with it

**Assertion form:** a wait loop has two independent reasons to wait, a time window and a busy
slot, and checks both on every poll. The tests drive a fake clock: the wait starts at 03:10
(inside), the clock reaches 04:30, the slot is free, the wait ends. They assert the grant time,
the status line and the final ledger.

**Mutation that defeats it:** make the window count only for a wait that *began* in it, or
never clear the window's state when it ends:

```diff
- until = blocked_until(windows) if windows else ""
+ until = (blocked_until(windows)
+          if windows and (started is None or blocked_at is not None) else "")
```

```diff
- ledger.blocked_until = None
+ ledger.blocked_until = ledger.blocked_until or None
```

A waiter queued behind a holder at 02:55 now takes the slot at 03:05. A waiter still queued at
04:30 keeps saying "blocked", so everyone jumps ahead of it. Nothing goes red: no fixture ever
held the slot and crossed an edge of the window in the same wait, and the `finally` clears the
ledger again before the test reads it.

**Why it looks covered:** each reason to wait has its own test, and each test waits for real.
The transitions between them (busy → blocked, blocked → busy) are separate states of the loop,
and a wait that ends exactly when the window does skips both.

**Guard form that survives:** hold the slot for real and script the polls across both edges in
one wait: queued before, window opens, holder leaves inside it; and window ends with the slot
still held. Read the shared record *during* each poll, not after the wait, and ask the other
side of it (the queue and outrank predicates) what it means there. Where the end-of-wait cleanup repeats an
assignment, pin it apart by an exit only it handles (an exception inside the window).

The same round's third survivor was a tick call checked by source order (`src.index(a) <
src.index(b)`) that an inserted `if` defeated: a wiring claim is driven, on the healthy path.

**Found:** CMX-479 rework round 1, PR #1007. Closed by
`test_a_waiter_queued_before_the_window_is_held_by_it_too`,
`test_a_waiter_still_queued_after_the_window_holds_its_place_again` and
`test_every_tick_re_queues_before_it_announces` in `tests/test_suite_blocked_hours.py`.
