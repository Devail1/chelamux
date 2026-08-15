## 61. A promotion call site is gated on a field every fixture reaching it holds constant

**Assertion form:** a call site that must fire unconditionally once its own tier's evidence
resolves — `_promote(wid, pane.resumed)` at the tier-2 (`claude --resume <sid>`) site,
`_promote(wid, sid)` at the tier-1 (event log) site — is proven correct, but every fixture
that reaches that specific call site happens to hold some OTHER, unrelated field of the
same request at one constant value:

```python
if sid:
    path = transcript_for_session(sid, base)
    if path is not None:
        _promote(wid, sid)                    # tier 1 — every fixture: pane.resumed is None
        ...
if pane and pane.resumed:
    path = transcript_for_session(pane.resumed, base)
    if path is not None:
        _promote(wid, pane.resumed)           # tier 2 — every fixture: pane.started is None,
        ...                                    #          and every fixture: sid is None
```

Every tier-1 promotion fixture in the suite leaves `pane.resumed` on its dataclass default
(`None`) — no test resumes a session AND also has an already-running hook-tagged one in the
same pane. Every tier-2 promotion fixture leaves `pane.started` unset (`None`, the
/proc-unreadable state) and the event log empty (`sid` stays `None` for the whole function)
— no test reaches tier 2 from a LIVE pane, or with tier 1 having already named a session
that merely failed to resolve a transcript.

**Mutation that defeats it:** wrap the call in a condition on the constant-valued field,
narrowing the call site to exactly the state every fixture already sits in — none of it
touches the tier's own success condition, so the diff reads as a no-op refinement:

```diff
-             _promote(wid, sid)
+             if not pane.resumed:
+                 _promote(wid, sid)
```
```diff
-             _promote(wid, pane.resumed)
+             if pane.started is None:
+                 _promote(wid, pane.resumed)
```
```diff
-             _promote(wid, pane.resumed)
+             if not sid:
+                 _promote(wid, pane.resumed)
```

Each one still runs `_promote` on every fixture in the file — the added `if` evaluates to
`True` in every case the suite happens to construct — so `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` stays green (3154 passed) against all three simultaneously. In production, each
one silently stops a real, correctly-identified session from ever being pinned: a live pane
running `--resume` (mutation 1), a window whose event log named a since-vanished session
before falling to `--resume` (mutation 2), and a hand-typed `--resume` alongside an
already-hook-tagged session (mutation 3, the exact population CMX-296 exists to capture).

**Why this slips through even with the call site's OWN tier well tested:** DEFEAT_SHAPES #58
and #60 are about a value the call site's target STORE holds constant (an empty pin store,
an always-succeeding write) — this shape is about a value on the INCOMING request that the
call site doesn't even read, but which the suite's fixtures never vary. A reviewer checking
"does every fixture exercise this tier's own success/failure paths?" answers yes for both
tiers here; the question that catches this shape is orthogonal — "for every OTHER field on
the same pane/request, does at least one fixture reaching this call site hold it at the
value production traffic will actually have?" A tier's own success condition
(`if sid:` / `if pane and pane.resumed:`) already forces the field IT reads to a specific
value; this shape hides in the fields neither that condition nor the call site reads at all.

**Guard form that survives:** for a call site with no real skip logic of its own (unlike
`_promote`'s deliberate agree/disagree check, itself covered by #58's fix), pick at least one
fixture per call site that sets every OTHER field on the same pane/request to the value a
live, ordinary agent would actually have — a `started` timestamp instead of the
/proc-unreadable default, a `resumed` id alongside an event-log hit, an event log that named
*something* (even if unresolvable) before falling through. A field left on its dataclass
default in every fixture reaching a given call site is exactly the field a narrowing mutation
can silently key off of.

**Found:** `chela/sessions.py`'s `resolve_window` (CMX-296, PR #369, round 5) — three
independent narrowing mutations, one per promoting call site plus the doubled tier-2 site,
all stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3154 passed) because no
fixture in the file varied `pane.started`, `sid`-when-tier-2-is-reached, or
`pane.resumed`-when-tier-1-is-reached away from their defaults. Closed by
`test_a_cmdline_promotion_fires_for_a_LIVE_pane`,
`test_a_cmdline_promotion_fires_even_when_the_event_log_named_ANOTHER_session`, and
`test_an_event_log_promotion_fires_even_when_the_pane_ALSO_resumed`.
