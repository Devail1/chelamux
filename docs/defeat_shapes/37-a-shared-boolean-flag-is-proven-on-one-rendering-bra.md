## 37. A shared boolean flag is proven on ONE rendering branch and assumed for a sibling branch of the same function

**Assertion form:** one render function computes a single flag once (`const isEnv =
k.source === 'env'`) and then consumes it at two independent template branches further
down — a `kind === 'number'/'text'` branch producing an `<input>`, and a `kind === 'bool'`
branch producing a `<select>`. A test suite proves the flag disables the `<input>` branch
(the only branch its fixtures happen to use, because the one fixture with `source: 'env'`
never also sets `kind: 'bool'`), and a reviewer scanning "is env-precedence tested?" sees a
passing disabled-field assertion and reasonably calls the whole function covered.

**Mutation that defeats it:** leave the shared `const isEnv = k.source === 'env'` computation
untouched (mutating it would fail the `<input>`-branch test, so this is a *narrower*,
harder-to-notice cut) and instead hardcode the disabled-ness at only the `<select>` branch's
own template site — `${isEnv ? 'disabled' : ''}` -> `${false ? 'disabled' : ''}` inside the
`kind === 'bool'` block specifically. Every existing test (which only ever drives `source:
'env'` through non-bool knobs) stays green; a bool-kind knob whose env var is set now renders
an editable `<select>` that silently discards whatever the operator picks.

**Why this is distinct from shape 22:** shape 22 is one function returning several sibling
*objects*, all declaring the same field, with `live()` only reading the field off whichever
object a boot snapshot happened to publish. Here there is one object (one knob) and one flag,
but the flag is *consumed* at two different template branches inside the same render call —
the gap is which branch's own copy of the ternary a test's fixture happens to reach, not
which object a snapshot happens to publish from. The fix pattern (drive every branch
independently) is the same lesson recurring at the template-consumption layer instead of the
object-construction layer.

**Guard form that survives:** when a shared flag feeds N template branches inside one render
function, construct a fixture that reaches EACH branch with the flag in its "guarded" state —
not just the branch every other test in the file already happens to use. `git grep` the flag
inside the function body to count how many places consume it, the same census shape 7
prescribes for call sites.

**Found:** CMX-287 rework round 1 (2026-08-14), PR #358. The verdict's own reported mutation
(`k.source === 'env'` -> `false`) does NOT survive against this repo's actual test suite —
`settings_timing.test.mjs` and `settings_dispatch.test.mjs` predate CMX-287 unchanged and
already drive that exact mutation red (verified by hand: 3/7 tests in those two files fail
under it). What WAS genuinely unguarded is one level narrower: `settings_dispatch.test.mjs`'s
only `source: 'env'` fixture (`merge_base`) is a plain text knob, so it only ever reaches
`_renderDispatchRows`'s `<input>` branch. A `kind: 'bool'` knob with `source: 'env'`
(`judge_enabled` in the fixture below) reaches a separate `<select>` branch whose own
`${isEnv ? 'disabled' : ''}` was never independently driven — hardcoding it to `${false ?
'disabled' : ''}` left every pre-existing test green. Closed by
`tests/settings_modal_precedence.test.mjs`, which also re-drives the reviewer's original
mutation through the real `#settings-tabs`/`.settings-tabpanels` modal markup (the pre-CMX-287
fixtures use a bare `#settings-drawer`/`#drawer-body` div) rather than through the older
drawer's minimal DOM, so no future round can pin the "decorative" claim on the DOM shape
differing from what a user actually sees.
