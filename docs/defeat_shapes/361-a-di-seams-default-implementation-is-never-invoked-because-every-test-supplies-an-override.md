## 361. A DI seam's default implementation is never invoked because every test supplies an override for the seam, not just the ones that need to

**Assertion form:** a new leaf function is added (`_default_kill_window(wid)`, a thin
`subprocess.run(["tmux", "kill-window", "-t", wid], capture_output=True)`) and wired as the
resolved-inside-the-body default for a DI kwarg (`kill_window=None` → `_default_kill_window`
in `resume()`, the same "resolved at call time, not bound at import time" pattern every other
tmux/filesystem leaf in this module already uses). This is shape 319's shape exactly — but
319's guard form (call the real function directly) was never missing; what was missing was
noticing it was missing, because the module's own shared test fixture (`_resume_kit`)
supplies a default for *every* DI kwarg it knows about, `kill_window` included in spirit —
except it doesn't actually override `kill_window`, it just leaves it at `None`. Every test in
the file either builds its kit through `_resume_kit()` (no `kill_window` override) or passes
its own explicit `kill_window=lambda wid: ...` override for the tests that assert on the
kill-window *contract* (closes on failure, never closes on success, never closes without a
wid). Neither path ever calls `_default_kill_window` itself, and neither path was written
*to* stub it away — one forgot the seam existed, the other was correctly testing a different
layer. The shape is the same corruption as 319/330, but the mechanism that produced the gap
is new: no single test author chose to bypass the leaf, the gap fell out of two independently
reasonable test-writing choices that happened to add up to zero coverage of the default.

**Mutation that defeats it:** `_default_kill_window`'s only line, `subprocess.run(["tmux",
"kill-window", "-t", wid], capture_output=True)` → `subprocess.run(["tmux", "list-windows",
"-t", wid], capture_output=True)` — a tmux subcommand that touches the named window (so it
still "does something" if anyone were watching for a crash) but never closes it. The full
suite (3944 tests) stayed green under this mutation: no test calls `_default_kill_window`
directly, and every test that reaches the `kill_window(...)` call site inside `resume()` has
already substituted its own fake for the parameter, so the real function's body — the only
place `"kill-window"` vs `"list-windows"` could ever be observed — never executes during a
test run.

**Why this is easy to miss reviewing the diff alone:** the PR's tests *do* thoroughly cover
the `kill_window` **contract** — when it's called, with what wid, and when it must NOT be
called (four separate tests: closes-on-failure, never-closes-on-success, never-closes-on-
spawn-failure, never-closes-without-a-wid). That thoroughness is exactly what makes the gap
invisible: a reviewer scanning for "is kill_window tested?" finds four tests and stops
looking, without noticing all four inject a lambda and none let the real default run.
Coverage of a DI seam's *usage* (call site behavior under the parameter) is orthogonal to
coverage of the seam's *default* (what the parameter resolves to when the caller supplies
nothing) — a PR can max out one axis while leaving the other at zero.

**Guard form that survives:** a direct test of the default implementation itself, with
`subprocess.run` monkeypatched at the module level (the same pattern `test_restore_cli.py`
and `test_dispatcher_window_reap.py` already use for their own tmux-argv leaves) and the
resulting argv asserted verbatim — no `resume()` call in between, no other DI kwarg in the
way. Separately, harden the shared fixture: `_resume_kit()`'s default `kill_window` was
changed from "absent" (silently falls through to the real leaf) to an explicit no-op lambda
that is deliberately *not* appended to the fixture's shared `calls` list, so it stays
invisible to every pre-existing `[c[0] for c in calls] == [...]` assertion while also closing
the two pre-existing tests that were — by accident, not by design — shelling out to a real
tmux server for a hardcoded `@99` window id every time the suite ran.

**Found:** CMX-361 rework round 1 (2026-09-11), PR #492. The judge's required-mutation-set
verdict named `_default_kill_window`'s `"kill-window"` → `"list-windows"` swap as surviving
under a full green run. Closed by adding
`test_default_kill_window_issues_real_tmux_kill_window` (direct call, `subprocess.run`
monkeypatched, argv asserted), verified red under the mutation and green after reverting it,
plus giving `_resume_kit()` (and the one other hand-built kit in
`test_resume_liveness_and_retry_bound_defaults_wire_to_the_real_modules`) an explicit
no-op `kill_window` default so no test in the file falls through to the real tmux binary
by omission.
