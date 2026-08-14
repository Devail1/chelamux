## 5. Asserting a source constant instead of the rendered value

**Assertion form:** the guard checks a value pulled from source — a constant re-imported, a
function's mere existence, a template literal read out of the `.js` file — rather than what
actually got rendered, wired, or POSTed at runtime.

**Mutation that defeats it:** sever the wiring that's supposed to CONSUME the constant (revert
a `chela.applyUpdate()` production call-site to `onclick="void 0"`). The constant itself is
untouched, so a check against the constant — or against `applyUpdate` merely being a defined
function — stays green even though nothing on the page calls it anymore.

**Guard form that survives:** read the value back from the RENDERED artifact — the actual
`onclick` attribute on the actual button node, the actual POST body, the actual DOM text —
never from re-reading the source that's supposed to produce it.

**Found:** `tests/settings_update.test.mjs:160-165` — every earlier assertion in that file
reads `btn.disabled` / `row.textContent` (how the control *looks*), and the judge corrupted
`onclick="chela.applyUpdate()"` to `onclick="void 0"` with the whole suite staying green. The
fix asserts `btn.getAttribute('onclick')` matches `/chela\.applyUpdate\(\)/` directly — the
rendered wiring, not the control's appearance.

**Found again:** `tests/test_runtime_truth.py`'s `process.node_ipc_env` pair (CMX-281 rework
round 1, PR #352). `Finding.detail` is one string mixing a dynamically-rendered
`{k}={v!r}` clause (built from what `os.environ` actually held) with STATIC advice prose
that spells out both var names verbatim, unconditionally, for troubleshooting purposes
(`` `env -u NODE_CHANNEL_FD -u NODE_CHANNEL_SERIALIZATION_MODE` ``). The tests asserted
`"NODE_CHANNEL_FD" in findings[0].detail`, which the static prose alone satisfies — the
judge blanked the dynamic clause entirely and the suite stayed green. This is the same shape
as the `settings_update.test.mjs` case with a subtler disguise: it isn't a *separate* source
constant sitting elsewhere in the file, it's a compile-time-constant substring living inside
the very field that also carries the rendered value, so the two look, at a glance, like one
observation-derived string. The fix asserts the rendered `k=v!r` pair itself
(`"NODE_CHANNEL_FD='3'"`) and that the absent sibling's rendered pair is NOT present — pinned
to what the observation produced, not to any name the prose happens to mention.
