## 361b. Fixing a DI seam's "default never runs" gap by making every test override the seam recreates the same gap one level up

**Assertion form:** shape 361's fix (round 1) closed "no test ever calls `_default_kill_window`
itself" two ways at once: (1) a direct test of the leaf, `subprocess.run` monkeypatched, and
(2) hardening `_resume_kit()` and the one hand-built kit in
`test_resume_liveness_and_retry_bound_defaults_wire_to_the_real_modules` so **every**
`resume()` call site in the file now passes an explicit `kill_window=lambda wid: ...`. Fix
(2) was reasonable in isolation — it also closed a real bug (two tests shelling out to a live
tmux server for a hardcoded `@99`). But it has a side effect its own author didn't assert on:
`resume()`'s actual default-resolution line, `if kill_window is None: kill_window =
_default_kill_window`, is now never exercised by any test in the file either, because no call
site ever leaves the parameter at `None` any more. Shape 361 proved the *leaf* runs somewhere;
nothing proves the *seam that reaches it in production* still points at that leaf.

**Mutation that defeats it:** `resume()`'s `kill_window = _default_kill_window` (the resolution
line, not the leaf's body) → `kill_window = lambda wid: None`. The full suite (3945 tests)
stayed green: every test supplies its own `kill_window`, so this line's right-hand side is
dead as far as any test's control flow is concerned — reassigning it to a permanent no-op
changes nothing any test observes.

**Why this is easy to miss reviewing the diff alone:** shape 361's fix reads as unambiguously
correct — it deleted a real defect (real tmux calls in tests) and added direct leaf coverage.
Both changes are visible, both are good, and both are exactly what a reviewer checking "did
they close the finding" would look for. What's invisible from the diff alone is that closing
the leaf's coverage gap by injecting the parameter *everywhere* trades it for a seam-resolution
gap — the two gaps live on different axes (the leaf's own body vs. the line that decides
whether the leaf or a fake ever gets called), so satisfying one axis reads as satisfying the
finding, full stop.

**Guard form that survives:** at least one test must reach the `kill_window(...)` call site
with the parameter genuinely absent from the call (not merely defaulted to a fixture's no-op),
and observe that `_default_kill_window` — patched at the module attribute `resume()` resolves
at call time, the same pattern shape 361's own wiring tests already use for `_default_
check_resumed`/`blocked_reason`/etc. — is the thing that actually gets invoked. A test that
patches `_default_kill_window` and then separately passes `kill_window=...` to `resume()`
proves nothing about the resolution line; the omission has to be real.

**Found:** CMX-361 rework round 2 (2026-09-11), PR #492. The judge's required-mutation-set
verdict named this exact resolution-line swap as surviving under a full green run — flagged as
a non-blocking note in round 1's own review (predicted, not yet confirmed, before round 2's
mutation battery proved it). Closed by adding
`test_resume_kill_window_default_resolves_to_the_real_default_kill_window`, which calls
`resume()` with no `kill_window` kwarg at all and asserts the monkeypatched
`_default_kill_window` received the call.
