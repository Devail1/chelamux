## 329. A terminal-set guard tested only through its helper, never its call site or its full membership

**Assertion form:** a sweep function reads `WHERE status IN ('a', 'b')` — a docstring names
the set exactly ("`a` or `b`, and ONLY those two") and calls out the negative space just as
precisely ("terminal-only, no exceptions"). Every test in the file calls the sweep function
*directly*, never through the one production call site that wires it into the tick loop. The
positive fixtures only ever seed member `a`; the negative-control fixtures only ever seed two
of the several non-members the docstring disclaims. Every test passes, the docstring reads as
airtight, and the two-clause guard ("only `a`/`b`", "never anything else") looks proven because
each half has *a* test — just not one that spans its own stated width.

**Mutation that defeats it:** three independent corruptions, each invisible for a different
reason:
1. Delete the call site entirely (`summary["window_reaped"] = _reap_terminal_windows(...)` →
   `= 0`) — every test still calls the helper straight, so the whole feature can be unwired
   from production and the suite never notices it stopped running for real users.
2. Widen the membership set to swallow a live, non-terminal status (`'a', 'b'` → `'a', 'b',
   'live'`) — no fixture ever seeds `'live'`, so nothing exercises the exact status the
   negative control was written to rule out just because it wasn't the one or two the author
   happened to type.
3. Narrow the membership set to drop the second declared member (`'a', 'b'` → `'a'`) — no
   fixture ever seeds `'b'` even though the docstring names it explicitly, so half of what the
   guard claims to protect is asserted only by a human reading the WHERE clause's source text.

**Why one fixture file can hold all three and still be corruptible:** "the helper is unit
tested" and "the helper is wired in" are different claims — see also [[7|shape 7]], though
that shape is about *multiple* call sites splitting coverage; here there is exactly *one* call
site and it is simply never driven by anything. And "the set's two members are named in prose"
is not the same claim as "each member independently has a fixture" — a set of size N needs N
positive fixtures and (ideally) a fixture per *plausible* non-member the guard is supposed to
exclude, not just however many the first round of test-writing happened to reach for.

**Guard form that survives:**
- Drive the ONE real call site end-to-end (here: a genuine `tick()`, not the extracted
  helper) and assert the caller-visible field it feeds (`summary["window_reaped"]`) — this is
  what catches the call site being deleted or no-op'd.
- Seed a fixture for **every** declared positive member of the set, not just the first one a
  test happened to be written against — a `done`-only test suite cannot tell "the two declared
  terminal statuses" from "the one status someone remembered."
- Widen the negative control beyond the two or three statuses that happened to come to mind:
  enumerate every other status the codebase actually defines (`ACTIVE_STATUSES`,
  `REVIEW_STATUSES`, `failed`, …) and seed one row per status in a single fixture, asserting
  none of them get touched — a fixture that only seeds `needs_human`/`awaiting_review` cannot
  distinguish "the WHERE clause is exactly `('done','closed')`" from "the WHERE clause merely
  excludes those two specific values."

**Found:** CMX-329 rework round 1 (2026-09-01), PR #422 — `chela.dispatcher._reap_terminal_windows`
and its sole `tick()` call site. `chela judge` found all three mutations above surviving
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3472 passed) on the original `tests/test_dispatcher_window_reap.py`,
which drove `_reap_terminal_windows`/`_tmux_window_ids` directly in all 7 of its tests and
seeded `done` in every positive fixture, `closed` in none. Closed by adding a real `tick()`
wiring test (`test_tick_wires_the_reap_sweep_into_the_real_call_site`), a `closed`-seeding
positive test (`test_reaps_a_closed_owned_window_still_alive`), and widening the negative
control to seed one row per non-terminal status (`claimed`, `running`, `changes_requested`,
`needs_human`, `awaiting_review`, `failed`) in a single fixture.
