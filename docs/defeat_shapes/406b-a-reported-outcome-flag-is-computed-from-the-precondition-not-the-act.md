## 406b. A reported-outcome flag is computed from the PRECONDITION, not the ACT — so asserting the flag never sees the act skipped

**Assertion form:** a function performs a side effect behind a condition
(`if run.get("window_name") and live_wid: _kill_windows_named(...)`) and returns a flag
describing it (`"window_killed": bool(live_wid)`). The test for the path named after the side
effect (`test_a_running_run_with_an_idle_agent_closes_and_its_window_goes`) asserts only
`result["window_killed"] is True` — it reads like proof the window went.

**Mutation that defeats it:** narrow the condition guarding the act
(`... and live_wid and force:`). The kill no longer happens on the idle, non-`--force`
path — the exact case the test is named for — but the flag is derived from `live_wid`, the
*precondition*, which the mutation did not touch. The flag still reports `True`; the test
stays green while a closed run leaves its agent's window alive.

**Guard form that survives:** assert the side effect itself (here: the stubbed tmux recorded
a `kill-window` for `@7`), the way the sibling `--force` test already did. And make the flag
honest — set it where the act happens (`window_killed = True` inside the branch), not from
the input the branch reads — so a flag assertion can no longer vouch for an act that was
skipped. Distinct from [[369c|shape 369c]] (return value pinned, side effect never observed
on a *no-op* path): here the return value *claims* the side effect, and is wrong.

**Found:** CMX-406 rework round 2 (2026-09-30), PR #556 — `chela close` idle-agent path.
