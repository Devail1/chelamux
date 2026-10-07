## 21b. A hands-off guard is tested only where there is nothing to touch, or where its trigger state never happens

**Assertion form:** CMX-21 hardened `scripts/agent-terminals.sh` against the 2026-10-06 orphan
with three new "stand down" guards. When its `TMUX_TMPDIR` dir is gone, `cleanup()` returns
before it reaps `webterm_*` sessions (a bare `tmux` would land on the DEFAULT socket, which
belongs to the live supervisor). An orphaned supervisor (PPID 1, no `pm_id`) with that dir gone
exits. And `envutil.tmux_scrub_names` leaves a `CHELA_CHILD_ENV_FORWARD` name in place. The
tests drove the supervisor with a missing `TMUX_TMPDIR` and asserted that it **refused to
heal**. Nothing was planted on the fallback socket for `cleanup()` to wrongly reap, and the
supervisor was always a direct child of pytest, so it was never orphaned. The forward exemption
was tested on `server_env`, which has its own copy of the check, and never on
`tmux_scrub_names`.

**Mutation that defeats it:** `tmux_tmpdir_missing && return` → `false && return`;
`if tmux_tmpdir_missing && orphaned; then` → `if false && …`; `(key not in forward and (` →
`(True and (`. All three stayed green across the full suite. The reap found an empty socket.
The orphan branch was gated on a state that no fixture created. The scrub's exemption had no
test of its own.

**Guard form that survives:** a guard that says "don't touch X" must be tested with an X put
where the bug would reach it. Plant a `webterm_<session>_*` session on the socket tmux falls
back to (the same `chelatest-*` `-L` name, with no `TMUX_TMPDIR`), terminate the supervisor, and
assert that the session is still alive. The negative control is the same session on the
supervisor's own socket, and it IS reaped. A guard whose trigger is a process state needs a
fixture that really creates that state: double-fork under `setsid` so the supervisor is
reparented to PID 1, then assert that it exits. The control is the same orphan with its dir
intact, which keeps serving. An exemption that is copied into two functions gets a test on each
copy (see also 7c).

Found by the judge on PR #604 (CMX-21, round 1). Guards:
`tests/test_env_leak_hardening.py::test_supervisor_cleanup_spares_fallback_socket_webterms_when_its_dir_is_gone`,
`::test_orphaned_supervisor_with_its_tmux_tmpdir_gone_exits`,
`::test_tmux_scrub_names_keeps_a_forwarded_marker_or_hazard`.
