## 67. A stale-flight guard's before-vs-after equality check never reads what the "before" content actually IS

**Assertion form:** an async load into a shared DOM target resets that target to a placeholder
(`"Loading…"`) SYNCHRONOUSLY, before issuing its fetch — so a later response can never make a
switch (a different session, a different file) briefly show the PREVIOUS render while the new
one is in flight. The test meant to prove the reset exists opens the target once, snapshots
`target.innerHTML` at some point after the open, lets a late/deferred response resolve, and
asserts `target.innerHTML` is unchanged from the snapshot — which is a real and correct guard
against a *different* mutation (a stale response re-rendering after the target already moved
on), but never inspects what the snapshotted content actually contains.

**Mutation that defeats it:** dead-code the synchronous reset itself
(`if (false && content) content.innerHTML = ...`). Nothing downstream changes: the stale-flight
`if (wid !== _openWid) return;` check still runs when the async response lands, so the (now
never-cleared) target still doesn't get overwritten by a late response — the equality check
between "before the late response" and "after the late response" holds regardless of whether
the reset that was supposed to run *before* that snapshot was taken ever fired. The guard is
checking the wrong pair of moments: it needs "before the switch was requested" vs "immediately
after," not "right after opening" vs "after the late response resolves."

**Why this is distinct from [[46|shape 46]]:** shape 46 is a gated action that's structurally
idempotent against the one state variable a test reads, for any input. This shape has no
idempotence — the reset really would produce a different `innerHTML` if it ran. The gap is
purely in *when* the two snapshots being diffed are taken: both snapshots in the existing test
are taken AFTER the point where the reset was supposed to already have happened, so the diff
compares two moments that are identical whether the reset ran or not.

**Guard form that survives:** drive an actual SWITCH — open the target for A, let it render
real content, then trigger a second open for B behind a deferred/never-yet-resolved fetch, and
assert IMMEDIATELY (synchronously, before awaiting the new fetch) that the target no longer
contains A's content. This is the one moment that can only be true if the reset ran
synchronously before the new fetch was issued; comparing snapshots taken only after that point,
as the pre-existing stale-flight tests do, can't distinguish it from the reset never having
existed.

**Found:** CMX-299 rework round 5 (2026-08-16), judge round 4 of PR #373.
`chela/dashboard/static/js/diffpanel.js`'s `openDiffModal` and `_loadDiffPatch` each reset
their shared DOM target (`#diff-modal-content`, `#diff-patch-view`) to a `"Loading…"`
placeholder before issuing their fetch, so switching sessions/files never shows the previous
one's stale content for the whole flight. `tests/diff_modal_wiring.test.mjs`'s existing
stale-flight tests only ever open ONE target and compare its `innerHTML` before vs after a late
response resolving for THAT SAME target — dead-coding either reset
(`if (false && content) ...` / `if (false) view.innerHTML = ...`) left both green:
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3185 tests) passed with either mutation in place.
Closed by two new tests that open the modal for session `@1`, close it, then open it for a
DIFFERENT session `@2` behind a deferred `/diff` fetch and assert `@1`'s file list is gone
from `#diff-modal-content` while `@2`'s fetch is still in flight (and the same shape one hop
down: clicking file `b.py` behind a deferred `/diff/patch` fetch must clear file `a.py`'s
stale patch text from `#diff-patch-view` before `b.py`'s fetch resolves).
