## 84. A setup step for EVERY window is fixtured only on the window with nothing sent after it, so gating or reordering it around the launch passes

**Assertion form:** `spawn_window` defines a `claude` shell function in every window it opens
while Remote Control is on — a "+ New shell" window AND an agent window chela launches `claude`
into. Every test opened a "+ New shell" window (`command=None`), found the definition in the
sent keystrokes, and ran it in a real `bash` with stubs. The tests proved the wrapper's own
logic thoroughly; they never opened a window that had a command.

**Why that's a gap:** with no command there is nothing for the definition to be ordered against
and no second branch to skip. Two properties live only on the command path: the definition must
be typed BEFORE chela's own launch (typed after, it lands in claude's TUI as a prompt and the
shell never gets it), and it must not be gated on `not command`. Both are invisible to a fixture
that never sends a command.

**Mutation that defeats it:** move the `_send(target, _rc_shell_function(...))` block below the
`if command:` launch send — or change its gate to `remote_control_enabled() and not command`.
Every "+ New shell" test still finds exactly one definition and runs it correctly.

**Guard form that survives:** also open the window WITH a command; assert the definition's index
in the send log precedes the launch's, and run the exact launch line chela sent THROUGH the
definition in `bash` — argv must carry exactly one `--remote-control <name>`. Pair the
setting-off test with the command case too, so neither gate side rides on one fixture.
Related: [[7c|shape 7c]] (a check at two pipeline stages tested past only one),
[[62|shape 62]] (fixture mounts the branch, assertion aims next to it).

**Found:** CMX-84 rework round 1 (2026-10-11), PR #646 — closed by
`test_agent_window_defines_the_wrapper_before_its_launch` and
`test_chelas_own_launch_through_the_wrapper_carries_one_remote_control`. The same round also
closed skip-list members (`-h`, `-v`) never fed ([[407c|shape 407c]]), the no-window-id target
fallback never reached, an unquoted `"$@"` never fed a spaced argument, and `$?` never read.
