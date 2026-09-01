## 330. A new leaf function's only tests monkeypatch it away, and the call site that feeds it a live argument is never pinned either

**Assertion form:** a new leaf function is added (`_cwd_is_live(cwd)`, a thin
`bool(cwd) and os.path.isdir(cwd)` filesystem check) alongside two call sites that consume
its result (`GET /api/restore`'s row split, and `POST /api/restore/resume`'s refusal check).
Every test that touches either call site does so via
`monkeypatch.setattr(dash, "_cwd_is_live", lambda cwd: ...)` — an autouse fixture
(`cwd_alive_by_default`) stubs it `True` for every test by default, and three more tests
override it per-case. No test in the suite calls the real function, and no test observes
*what argument* either call site actually passes it — this is exactly shape 319 (a new
function's only tests monkeypatch it away entirely), but it recurred here on a second axis:
shape 319's guard form closes the function's own body; it does not by itself prove a call
site still forwards the right argument once the function is real again.

**Mutation that defeats it:** two independent one-line mutations, either invisible to the
whole suite. (1) Gut the function's own logic — `bool(cwd) and os.path.isdir(cwd)` →
`bool(cwd) or os.path.isdir(cwd)` — inverts its entire premise (a non-existent-but-non-empty
path now reports live) but every test still gets its canned return value from the stub, so
none of them execute this line. (2) Corrupt a call site instead — `_cwd_is_live(verdict.cwd)`
→ `_cwd_is_live("/")` in the resume route — swaps the row's real cwd for a path that is
always live; every test still patches the *name* `_cwd_is_live` and hands back whatever the
test chose, regardless of what argument the (also-replaced) callable was invoked with, so a
call site degraded to a constant argument produces byte-identical test results to one that
forwards the real value.

**Why this is distinct from shape 319 in practice, not just in form:** shape 319's catalog
entry already names both failure modes in the abstract (function body vs. call site), but a
guard written against *only* the direct-call half (call the real function, assert its
return value) still leaves the call-site half open — a stub still sits at the boundary in
every route-level test, so `_cwd_is_live("/")` and `_cwd_is_live(verdict.cwd)` remain
indistinguishable unless a route-level test also leaves the stub off and inspects what
argument reached it. Recurrence confirms the fix has to close both halves every time this
shape appears, not just the one that looks like "the new function's own tests."

**Guard form that survives:** two kinds of test, matching the two places corruption can
hide, mirroring shape 319's split. First, call the real function directly against a real
`tmp_path` — an existing directory (True), a path that was never created (False), and a
path that exists but is a file, not a directory (False) — this exercises the function's own
body, so mutation (1) turns it red. Second, leave the function **unpatched** in a route-level
test, point `verdict.cwd` at a real (and, for the negative case, deliberately non-existent)
`tmp_path`, and either assert on the route's resulting status code or (stronger) wrap the
real function in a spy that records its exact argument and assert that argument equals the
verdict's own cwd — this catches mutation (2), because a call site degraded to a constant
argument observably stops passing the row's real cwd.

**Found:** CMX-330 rework round 1 (2026-09-01), PR #421 — every one of the four references
to `_cwd_is_live` in `tests/test_restore_api.py` was a `monkeypatch.setattr` stub (the
autouse `cwd_alive_by_default` fixture plus three per-test overrides), so `chela judge`'s own
mutation battery found both `bool(cwd) and os.path.isdir(cwd)` → `bool(cwd) or
os.path.isdir(cwd)` in `chela/dashboard/app.py` and the resume route's
`_cwd_is_live(verdict.cwd)` → `_cwd_is_live("/")` survived — 3468 tests stayed green under
either mutation. Closed by adding direct tests of the real function against `tmp_path`
(existing directory, non-existent path, file-not-a-directory, `None`/empty string) plus a
resume-route test that leaves the function unpatched, points a `MANUAL` verdict's `cwd` at a
real non-existent `tmp_path` subdirectory, and spies on the argument the route actually
passes to confirm it is the verdict's own cwd rather than a fixed path.
