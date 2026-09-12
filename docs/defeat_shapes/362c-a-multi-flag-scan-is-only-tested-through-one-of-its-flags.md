## 362c. A multi-flag scan is only tested through one of its flags, so dropping another goes unnoticed

**Assertion form:** `_tmux_violation`'s session-scoping carve-out (`tests/conftest.py`) scans
`rest` for *either* of two flags to find the call's target: `for flag in ("-t", "-s"): ...`,
with the comment `-t targets an existing window/session, -s names a new one` right there on
the line. Both flags feed the same `target` variable and the same exemption check below —
either one, alone, is enough to exempt a call scoped to `CHELA_TMUX_SESSION`.

**Why the obvious direct tests don't catch it:** every existing test of this carve-out —
`test_the_tmux_fence_classifier_exempts_a_private_socket_even_when_the_subcommand_leads`, the
PATH-shim test, the two live-`Popen` integration tests, even the per-subcommand membership
parametrization (`test_every_mutating_tmux_subcommand_is_pinned_in_the_membership_set`, which
always calls with `"-t", "@1"`) — happens to only ever construct `-t`-shaped argv. Nothing in
the suite calls `_tmux_violation` with an `-s`-shaped invocation (`tmux new-session -s
<session>`), even though that is the exact shape the block comment names as the reason `-s`
is in the tuple at all. A tuple scanned in a loop reads as one unit; nothing about the test
suite's shape signals that only one of its two elements has ever been exercised.

**Mutation that defeats it:** `("-t", "-s")` → `("-t",)`. Every existing test still passes —
they were all `-t`-shaped to begin with — while a real `tmux new-session -s
chela-tests-no-such-session` now falls through the scan with `target=None`, fails the
exemption, and gets refused by the fence as if it were unscoped. Tightening-only from the
fence's own perspective (it can only flag *more* calls, never let one through it shouldn't),
which is exactly why nothing already in the suite goes red.

**Guard form that survives:** a direct unit test of `_tmux_violation` with an `-s`-shaped
argv — `_tmux_violation(["tmux", "new-session", "-s", safe_session], None) is None` — proves
the second flag in the scan is load-bearing, not redundant, the same way
`test_every_mutating_tmux_subcommand_is_pinned_in_the_membership_set` proves each member of a
set individually rather than trusting one representative element to stand in for the whole
collection. General form: when a guard scans a tuple/set of equivalent options looking for
any match, cover each option with its own case — one shared test that happens to only ever
exercise the first alternative leaves every other alternative's removal silent.

**Found:** CMX-362 rework round 3 (2026-09-12), PR #495. The judge's required-mutation-set
verdict named this surviving corruption after rounds 1–2 closed the corruptions in
[[362|shape 362]] and [[362b|shape 362b]]. Closed by adding
`test_the_tmux_fence_classifier_exempts_a_new_session_via_the_s_flag_not_just_t`
(`tests/test_restore_cli.py`).
