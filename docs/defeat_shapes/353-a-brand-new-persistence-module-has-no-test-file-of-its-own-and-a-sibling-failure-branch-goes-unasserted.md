## 353. A brand-new persistence module has no test file of its own, and a sibling failure branch that should write to it goes unasserted

**Assertion form:** a new module, `chela/resume_state.py`, is added to back a durable retry
bound — `record_failure`/`clear`/`blocked_reason`/`tries`, keyed on `MAX_TRIES`, persisted to
`CHELA_DIR/resume-attempts.json`. Its only caller, `chela.restore.resume()`, reaches it
through three DI-seam kwargs (`resume_blocked`, `record_resume_failure`,
`clear_resume_failure`), and `tests/test_restore.py`'s `_resume_kit()` fixture stubs all
three to the "everything is fine" shape by default. Every test in the suite that mentions
`resume_state` reaches it only through that stub — no `tests/test_resume_state.py` exists,
so none of `record_failure`'s counter arithmetic, `clear`'s delete, or `blocked_reason`'s
`MAX_TRIES` comparison ever executes for real. Separately, `resume()` calls
`record_resume_failure` from **two** call sites — the spawn-failure branch (`if not
result.ok:`) and the liveness-failure branch (`if not check_resumed(...):`) — but only the
liveness branch's call was ever asserted; the existing spawn-failure test
(`test_resume_reports_RESUME_FAILED_and_writes_nothing_when_the_launch_fails`) checked only
that the row stayed untouched, never that the failure was actually persisted so a *later*
`chela restore --resume` pass would see it as blocked.

**Mutation that defeats it:** four independent one-line mutations, none visible to the whole
suite. In `chela/resume_state.py`: (1) `MAX_TRIES = 1` → `MAX_TRIES = 1000` (the bound stops
binding), (2) `data[session_id] = {"tries": prior + 1, ...}` → `{"tries": 0, ...}` (the
counter never rises), (3) `clear`'s `del data[session_id]` guarded behind `if False and
session_id in data:` (a "cleared" record is never actually removed). In
`chela/restore.py`'s spawn-failure branch: (4) `record_resume_failure(v.session_id,
result.error or "spawn failed")` wrapped in `if False:` (a session whose *launch itself*
keeps failing is relaunched forever, never blocked). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest
-q` stayed green (3857 passed, 0 failed, 0 error(s)) with every one of these in place — no
test drove the real `resume_state` functions, and the spawn-failure test's only assertions
were about what got archived/removed, not about what got recorded.

**Why this recurs shape 319/330 one level up:** those entries describe a single *function*
whose only tests monkeypatch it away entirely — the fix is a direct test of the real
function against a faked evidence source. Here the same gap exists at *module* granularity:
an entire persistence module, with its own on-disk store and its own bounded-retry
semantics, was added with zero tests calling it directly — its only "coverage" is a
consumer's DI stub answering on its behalf. The sibling-branch half (mutation 4) is a
different, compounding mechanism: two call sites forward to the *same* write function, and
proving one call site's forwarding says nothing about the other's — the identical shape
[[352b|shape 352b]] describes for a tooltip builder's un-mirrored sibling clause, reproduced
here for a failure-recording call site instead of a render clause.

**Guard form that survives:** a dedicated `tests/test_resume_state.py`, isolated against a
temp `CHELA_DIR` (the same `monkeypatch.setenv` + `importlib.reload` pattern
`tests/test_sessionids.py` uses), calling the real functions directly — `record_failure`
bumping the persisted try count across two separate calls, `blocked_reason` honoring
`max_tries` both below and at the bound (and pinning the real, un-overridden
`MAX_TRIES` default too, the way [[352c|shape 352c]] recommends for a module constant), and
`clear` proven to actually remove the row by reading the store file back afterward, not just
by re-querying `blocked_reason`. Separately, a spawn-failure test that asserts
`record_resume_failure` was actually called with the spawn error — mirroring the existing
liveness-failure test's assertion — rather than only checking that nothing was archived or
removed.

**Found:** CMX-353 rework round 1 (2026-09-10), PR #470. The judge's own throwaway-checkout
mutation battery found all four mutations above survived with 3857 tests green — nothing in
the suite reached `chela/resume_state.py` directly, and the existing spawn-failure test in
`tests/test_restore.py` never asserted `record_resume_failure` ran. Closed by
`tests/test_resume_state.py` plus
`test_resume_persists_a_resume_failure_when_the_SPAWN_itself_fails_not_only_on_a_dead_liveness_check`
in `tests/test_restore.py`.

**See also:** [[319|shape 319]] and [[330|shape 330]] — the same "only ever reached through a
stub" gap, one level up (a whole module, not just one function); [[352b|shape 352b]] — the
same un-mirrored-sibling-branch mechanism, for a failure-recording call site instead of a
render clause; [[352c|shape 352c]] — the same "override swallows the real default" gap,
applied here to `_default_check_resumed`'s own tunable constants
(`_LIVENESS_SETTLE_S`/`_LIVENESS_CONFIRMATIONS`) in the same PR's liveness-check tests.
