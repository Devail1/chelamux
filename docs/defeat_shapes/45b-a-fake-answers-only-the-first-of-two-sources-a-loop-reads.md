## 45b. A fake answers only the first of two sources a loop reads, so dropping the second source passes

**Assertion form:** `_inherited_env_names` collects names from the daemon env plus two tmux
tables — `show-environment -g` (global) and `show-environment -t <session>` (session) — in one
`for scope in (["-g"], ["-t", TMUX_SESSION])` loop. The guard's fake `subprocess.run` returned a
sentinel for the `-g` call and an empty default for everything else, then asserted the
`-g`-only sentinel was unset in the pane. The session call ran, got `""`, and contributed
nothing.

**Why that's a gap:** a source that only ever returns empty is indistinguishable from a source
that is never read. The loop's second iteration was executed but never *observed*, so its
existence was unprotected.

**Mutation that defeats it:** `for scope in (["-g"],):` — drop the session scope. Every test
still passes; a name set with `tmux set-environment -t chela` (or pulled in by
`update-environment` on attach) would reach the agent again.

**Guard form that survives:** give the fake one distinct answer PER source and put a sentinel
in each that exists in no other — then assert every sentinel is gone. Generalises: when code
unions N sources, the fixture needs N disjoint witnesses; one witness proves one source.
Related: [[84|shape 84]] (a setup step fixtured only on the branch with nothing after it),
[[407c|shape 407c]] (a set rule tested on two members of the set).

**Found:** CMX-45 rework round 1 (2026-10-12), PR #652 — closed by the `SESSION_ONLY_SENTINEL`
leg of `test_a_daemon_var_is_absent_in_the_agent_pane_but_chela_wid_and_path_survive`.
