## 473. A consecutive-failure counter is tested by one run of failures, so its reset is dead

**Assertion form:** `scripts/agent-terminals.sh` reaps the wall only from the second
CONSECUTIVE tick that finds no tmux session (`ABSENT_TICKS -lt 2` keeps the set). Two tests
covered it: one failed check then a good one (nothing reaped), and the session gone for good
(everything reaped).

**Mutation that defeats it:** drop the reset on a present tick.

```diff
         SESSION_STATE=present
-        ABSENT_TICKS=0
+        :
```

Every existing test stays green: with a single run of failures in the process's life, "N in a
row" and "N ever" are the same number. In production the second isolated hiccup, hours later,
closes every terminal on the wall.

**Why it looks covered:** both sides of the threshold are tested (1 keeps, 2 reaps). What no
fixture does is count from a state where an earlier failure already happened and recovered.

**Guard form that survives:** fail, recover, fail again, and assert the action did NOT happen.
Make each failure one-shot (the stub consumes a marker file and fails exactly that call)
instead of racing the poll interval, and count the failures the process reported, so a blip
it never saw can't pass as one it tolerated.

**Found:** CMX-473 judge round 1, PR #1003. Closed by
`test_only_consecutive_failed_session_checks_reap_the_wall` in
`tests/test_agent_terminals_bash32.py`.

**See also:** [[83|shape 83]] (state whose only consumer is a later call). Nearest sibling in
the same verdict: the sandboxed-share spawn was exercised only with the session already there,
where a direct `new-window` and `discovery.open_window` do the same thing; closed by
missing-session tests for every spawn path in `tests/test_open_window.py`. See
[[7|shape 7]] for that one (one caller's wiring tested, a second caller's not).
