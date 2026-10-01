## 412. A new routing helper is asserted to exist while the call site can revert to the old one

**Assertion form:** a change re-routes an action through a NEW helper that sits beside the
OLD one it replaces (both still defined, because the old one is the fallback). The guard
checks that the new route's marker appears in the injected source — e.g.
`"window.__chelaUpload" in _TERM_PASTE_KEY_SHIM` — and that the shim is served.

**Mutation that defeats it:** point the one call site back at the old helper
(`pasteClipImage(...)` → `pasteImage(...)`). The new helper is still defined, so the marker
string is still in the source and the shim is still served; nothing calls it any more, and
Ctrl/Cmd+V quietly goes back to the legacy `/tmp` path. Suite green.

**Why it is not just shape 05:** the source-substring check is not lazy about a constant —
it names the right thing. What it cannot see is *which of two live, defined paths the
dispatch picks*, and the old path is kept on purpose (it is the switch-off fallback), so
"is the old code gone?" is not available as a check either.

**Guard form that survives:** drive the user action on the RENDERED page with every shim
running, and assert the ROUTE it reaches (`/api/term/upload`, not `/api/term/paste-image`).
Run the same action with the switch OFF as the negative control — it must reach the old
route — so the harness is proven able to tell the two apart.

**Found:** CMX-412 rework round 1 (PR #563). Fixed by `tests/test_term_upload_served.py` +
`tests/term_upload_harness.mjs` (served `/term/@1/` in jsdom, real Ctrl+V / paste / drop).
