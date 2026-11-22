## 470. A call-site guard keyed on the bug's own variable name misses the same bug under another name

**Assertion form:** the fix makes every tmux session target exact (`-t "=${TMUX_SESSION}"`),
and a static guard proves no call site is left bare: a regex over the shell scripts flags
`-t "$TMUX_SESSION"` / `$SESSION` / `$CHELA_TMUX_SESSION` with no `=` in front. Its fixtures
even include a negative row, `kill-session -t "${WEBTERM_PREFIX}x"` → *not* bare, chosen
because that variable "isn't the session". The guard is green and the fix reads as covered.

**Mutation that defeats it:** strip the `=` the same fix added to a target built from any
OTHER variable — `despawn`'s `tmux kill-session -t "=${WEBTERM_PREFIX}$(sanitize "$wid")"`
back to `-t "${WEBTERM_PREFIX}…"`. The guard never looks at that variable, so it stays
green. And it is the worst place to lose it: `@1`'s viewer session is `webterm_chela__1`,
`@10`'s is `webterm_chela__10`, so once `@1`'s is gone a bare kill prefix-matches `@10`'s
and closes another agent's terminal. The negative fixture had *certified* the bug.

**Why it looks covered:** the variable names in the guard are the ones in the bug report,
so the guard matches the report exactly. But the invariant is about the KIND of target
(a session name, which tmux prefix-matches), not about which variable happens to hold it.

**Guard form that survives:** key the static check on what tmux does with the target — for
every subcommand whose `-t` names a session (`has-session`, `kill-session`, `new-session`,
`list-windows`, …), flag ANY variable-built target without `=`, whatever it is called; keep
pane/window subcommands (`display-message -t "$TMUX_PANE"`) out by subcommand, not by name.
Pin the scan's reach (`agent-terminals.sh` is in the file list). Then back it with a
behavioural test that runs the script's OWN function text (cut out verbatim, since the
script can't be sourced) against a scratch tmux server holding the prefix sibling, and
asserts the sibling survives. The Python AST guard has the same name-keyed limit; it says
so in its docstring, and the real-tmux tests are what prove the behaviour.

**Found:** CMX-470 judge round 1, PR #994 (a held-out experiment). Fixed in
`tests/test_tmux_targets.py` (`_SH_SESSION_CMDS`) and `tests/test_service_session.py`
(`test_real_tmux_despawn_never_kills_a_prefix_sibling_viewer`,
`test_real_tmux_ensure_session_has_session_is_exact`).
