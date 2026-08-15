## 60. A shared helper's contract is proven at one call site only; a sibling call site that bypasses the helper entirely survives

**Assertion form:** two call sites route through the same helper to get a shared contract —
here, a "skip the write when the durable value already agrees" check and a best-effort
`try`/`except` around the write, both living inside one small function:

```python
def _promote(wid: str, session_id: str) -> None:
    try:
        if sessionids.session_id_for(wid) == session_id:
            return
        sessionids.set_session_id(wid, session_id)
    except Exception:
        log.warning(...)
```

Both call sites are textually identical — `_promote(wid, sid)` — so a suite that pins the
helper's contract by calling the FIRST call site (with a stale-value fixture, an
already-agreeing fixture, and a failing-store fixture) *looks* like it has proven the
contract everywhere the helper is called. It has only proven it where those fixtures were
actually aimed.

**Mutation that defeats it:** at the SECOND call site only, replace the call to the shared
helper with an inlined, weaker substitute — a raw unguarded store write with no skip and no
`try` (`_promote(wid, sid)` → `sessionids.set_session_id(wid, sid)`), or a hand-rolled skip
condition that checks presence instead of equality (`if sessionids.session_id_for(wid) is
None: _promote(wid, sid)`). Every existing test at that second call site still passes,
because none of them ever puts it under a non-empty store, an agreeing store, or a failing
store — every fixture that reaches it starts from an empty store and a mock that never
raises, the same shape as DEFEAT_SHAPES #58's empty-store blind spot, but now scoped to a
*specific caller* rather than to the helper's own unit tests.

**Why this slips through even with the helper itself well tested:** unit tests on `_promote`
(or on the first call site that exercises it) prove the function's contract in isolation.
They say nothing about whether a SECOND, independently-editable call site actually calls it —
that is a wiring fact, not a logic fact, and a mutation that edits only the second call site
changes the wiring without touching the function the first call site's tests are pinned to.
A reviewer skimming the diff sees `_promote(...)` at both call sites and reasonably assumes
one is thereby covering both.

**Guard form that survives:** for every call site sharing a helper's contract — not just the
first one a test happens to exercise — mirror the SAME fixtures onto it: a pre-existing
DIFFERENT value (proves overwrite-on-disagreement), a pre-existing SAME value with the write
itself observed via a spy, not just the end state (proves skip-on-agreement), and a store
that raises (proves the `try`). Fixtures written against one call site do not transfer to a
sibling call site that is merely textually identical today — the two can diverge silently the
moment either one is edited without the other, which a code review reading the diff in
isolation has no way to catch.

**Found:** `chela/sessions.py`'s `resolve_window` (CMX-296, PR #369, round 4) — the tier-2
(`claude --resume <sid>` / `pane.resumed`) call site's `_promote(wid, pane.resumed)` replaced
with (a) a presence-only skip guarding an otherwise-unchanged `_promote` call, and (b) a raw,
unguarded `sessionids.set_session_id(wid, pane.resumed)` bypassing `_promote` entirely — both
stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3151 passed) because every
CMX-296 promotion test that reached the tier-2 call site started from an empty store and a
store that always accepted the write; the stale-pin-update, skip-on-agreement, and
store-failure fixtures existed only for the tier-1 (event log) call site
(`test_a_promotion_updates_a_pin_that_names_a_different_session`,
`test_an_already_pinned_session_is_not_rewritten`,
`test_a_promotion_failure_does_not_break_resolution`). Closed by mirroring all three onto the
tier-2 call site: `test_a_cmdline_promotion_updates_a_pin_that_names_a_different_session`,
`test_a_cmdline_promotion_does_not_rewrite_an_already_pinned_session`, and
`test_a_cmdline_promotion_failure_does_not_break_resolution`.
