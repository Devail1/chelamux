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

**Found a third time, inverted:** `tests/settings_cost.test.mjs`'s tab-switching property
(CMX-287 rework round 3, PR #358). Here there was no source constant to point at at all — the
test called `window.chela.selectSettingsTab(tab)` (the real, un-mutated production function)
directly, so the `.active`-class toggling it asserted was genuinely correct... for a path a
real click never takes. `renderSettings()` renders each rail entry as
`` `<div class="settings-tab" data-tab="${t.id}" onclick="chela.selectSettingsTab(this.dataset.tab)">` ``,
and nothing in the suite ever read that `onclick` attribute — so the judge blanking its
argument (`onclick="chela.selectSettingsTab('')"`) left every tab click a silent no-op while
the suite, which never clicks anything, stayed green. Where the first two instances checked a
*stand-in* for the rendered value (a source copy, static prose), this one skipped the render
step's consumer entirely by calling the handler as a plain function — same failure to observe
the rendered artifact, reached by calling the *right* function with the *wrong* provenance
instead of reading a wrong stand-in. Closed with the idiom `tests/sidebar.test.mjs` already
established for this exact gap (its own round-20/21 comment: "calling
`window.chela.selectView(...)` directly never touches [the onclick]"): read the REAL rendered
node's `onclick` attribute, assert its text names `this.dataset.tab` (not a literal), then
`new Function('chela', el.getAttribute('onclick'))` compiled and run `this`-bound to the node
— first against a recording stub (proves the wire isn't a no-op independent of the handler's
own correctness), then against the real `window.chela` (proves the DOM actually changes).

**Found a fourth time, same PR, one widget over:** `tests/settings_cost.test.mjs`'s
Cost-window switcher (CMX-287 rework round 4, PR #358). The round-3 fix above closed this hop
on the tab rail, but the Cost tab's own `.cost-window-btn` segments — rendered by the same
`renderSettings()` with the same shape (`onclick="chela.setCostWindow('7d')"`) — had a
sibling test (`window.chela.setCostWindow('7d')` called directly) that never got the same
treatment. The judge corrupted the 7d button's literal argument to
`onclick="chela.setCostWindow('live')"`: the attribute still parses, still names a real
window, still reaches a real function, so the button becomes a live, highlighted no-op that
re-fetches Live forever — invisible to a test that only ever drives the handler by name.
Closing one instance of this shape in a file does not close a second, independently-written
widget in the same file; each rendered `onclick` needs its own binding-level test, not one
per file. Closed the same way, plus one refinement: because these buttons' onclick arguments
are literals (not `this.dataset.win`), the fix asserts the attribute text names *that
button's own* `data-win` value (`` new RegExp(`setCostWindow\('${btn.dataset.win}'\)`) ``)
rather than a hardcoded `'7d'` literal — a hardcoded `'7d'` on the test side would still match
a hardcoded, but WRONG, `'7d'` typo'd onto the wrong button in production, so pinning against
the button's own data attribute is what actually catches the mismatch the mutation produced.

**Found a fifth time, in Python, no DOM involved:** `tests/test_diffsurface.py`'s
`test_all_git_subprocess_calls_are_bounded_by_git_timeout` (CMX-299 rework round 10, PR #373).
Every earlier instance of this shape lived in rendered markup (an `onclick` attribute, a
template string); this one has no render step at all — it's a `subprocess.run` spy asserting
`all(t == diffsurface._GIT_TIMEOUT for t in calls)`. `diffsurface._GIT_TIMEOUT` is not a
stand-in for the constant under test, it *is* the constant under test: the mutation
(`_GIT_TIMEOUT = 15` → `_GIT_TIMEOUT = None`) and the assertion's expected value are the same
attribute lookup, so they move together and the comparison is `None == None` after the
mutation lands. The spy can still catch a *call site* dropping its own `timeout=` kwarg
(that's the shape this test was originally written to close), but it can never catch the bound
itself being widened or removed, which is the actual failure `_GIT_TIMEOUT` exists to prevent
(an unbounded git subprocess on a wedged pane cwd hangs forever). The fix pins the literal on
both sides: `assert diffsurface._GIT_TIMEOUT == 15` (catches the constant drifting) *and*
`assert all(t == 15 for t in calls)` (catches a call site drifting off the constant) — dropping
either half leaves one of the two mutations invisible again.
