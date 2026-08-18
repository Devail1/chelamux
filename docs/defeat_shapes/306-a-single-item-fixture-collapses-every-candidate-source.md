## 306. A single-item fixture collapses every candidate source of a fallback expression onto the same identity

**Assertion form:** a render site picks its subject from a fallback chain —
`const sw = sel.value || wids[0]` (terminals.js, single-pane mode) — where `sel.value` is the
user's SELECTED agent and `wids[0]` is just whichever agent happens to be first in the
online-agents list. A guard exists to prove the rendered pane (and the chip wired into it)
belongs to the *selected* agent: it asserts the emitted `data-ctx-for` / `onclick` argument
equals a specific wid, e.g. `openDiffModal('@1')`. But the fixture backing the test defines
only ONE agent, `[{ window_id: '@1' }]`. With one agent, `sel.value` (once populated),
`wids[0]`, and "the only agent that exists" are three different *descriptions* of the exact
same value — there is no way for the test's single assertion to tell which of them the source
code actually used.

**Mutation that defeats it:** replace the correct source (`sw`, the selected/displayed agent)
with the wrong one (`wids[0]`, the first agent) at the call site:
`${_ctxBarHTML(sw, false)}` → `${_ctxBarHTML(wids[0], false)}`. On a one-agent fixture this is
a no-op — `wids[0]` IS `'@1'`, exactly like `sw` was — so every assertion the guard makes
(`data-ctx-for="@1"`, `openDiffModal('@1')`) still holds bit-for-bit. `chela judge` found this
live on CMX-306 round 1: `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3217
passed) with the mutation applied, even though the guard's own stated purpose — "the
single-pane Files chip must carry the DISPLAYED agent's wid" — was exactly what the mutation
broke. In production this class of regression opens the WRONG session's per-session diff
modal the instant a user has more than one agent open and has selected anything other than
the first one; the one-agent fixture can never observe that, no matter how many assertions it
stacks on top of the collapsed value.

**Why this is distinct from [[52|shape 52]] / [[53|shape 53]]:** those shapes are about an
*index* lookup collapsing onto a positional default (first or last) because too few items were
rendered for the target to land anywhere else. This shape is about a *fallback expression*
(`a || b`, or any "prefer the selected thing, else the default thing" chain) where the two
operands themselves are indistinguishable — not because of where the target sits in a list,
but because the fixture only ever supplies one possible value for BOTH operands to resolve to.
Adding more items in the wrong role doesn't close it (shape 52's three-item fix targets index
position, not fallback-operand identity); the fixture specifically needs a second item that the
`||`'s left-hand side can be pointed at while its right-hand side stays elsewhere.

**Guard form that survives:** give the fixture at least two items, then explicitly steer the
guarded value's real SOURCE (here, the `<select>`'s `.value`, i.e. `sel.value = '@2'`) to a
DIFFERENT item than whatever the fallback (`wids[0]`) would produce (`'@1'`, first-registered).
Assert against the steered value (`'@2'`), not the fallback's — so a call site that silently
swaps in the fallback source produces an observably different DOM attribute / onclick argument
than the one the guard expects, and the suite goes red.

**Found:** `tests/diff_modal_wiring.test.mjs`'s "the Files chip is also emitted (and wired end
to end) in single-pane / mobile mode" guard (CMX-306, round 1, PR #380) used the file's
existing single-agent `AGENTS` fixture (`[{ window_id: '@1' }]`) and asserted
`chela.openDiffModal('@1')` on the single-pane chip. The judge swapped `_ctxBarHTML(sw, false)`
for `_ctxBarHTML(wids[0], false)` at the single-pane call site (terminals.js:1580); the suite
stayed green because `sw` and `wids[0]` were both `'@1'` under that fixture — the guard could
prove the emitted wid was *correct* without ever proving it came from the *selected* agent
rather than the *first* one. Closed by extending the fixture with a second agent (`'@2'`,
mirroring the precedent at line ~709 of the same file), pinning `sel.value = '@2'` before
rendering so `sw` (`'@2'`) and `wids[0]` (`'@1'`) provably diverge, and asserting the chip
carries `'@2'` — which fails red the instant the call site reverts to `wids[0]`.

⚠️ **Correction (CMX-306 round 2, same PR):** round 1's fix above steered `sel.value` away
from `wids[0]`, but a two-item fixture only ever rules out the FIRST positional default —
with exactly two agents, "not first" and "last" are the same agent, so `sel.value = '@2'` is
*also* `wids[wids.length - 1]`. The judge swapped `sw` for `wids[wids.length - 1]` instead of
`wids[0]` and the suite stayed green again, for the identical reason shape 52 documents for
index lookups: closing one enumerated positional default (first) silently leaves its sibling
(last) open. Unlike shape 53's conclusion for `f(length)` index formulas, this fallback
expression only has two possible non-selected operands (`wids[0]` and
`wids[wids.length - 1]`) rather than an unbounded family, so — unlike shape 53's prescribed
differential-assertion fix — a third, non-enumerable fixture item is sufficient here, not just
a narrowing step: with THREE agents and the selection pinned to the MIDDLE one (`'@2'` in
`['@1', '@2', '@3']`), `sel.value` no longer equals `wids[0]` OR `wids[wids.length - 1]`, so
no positional fallback can produce the expected value by accident.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3217 passed) under this second
mutation too, before the fix. **Guard form that survives (updated):** render at least THREE
candidates and steer the guarded value's real source to the ONE that sits in neither
positional-default slot (`wids[0]` nor `wids[wids.length - 1]`) — a two-item fixture pinned
away from `wids[0]` is not enough; it always leaves `wids[wids.length - 1]` unexercised (or,
symmetrically, the reverse).

The same round also found a second, unrelated hole in the same guard: this test file's
`before()` (shared by every test in it) hard-stubs `window.matchMedia` to report a DESKTOP
width for the life of the whole file, so `_isMobileTerm()` (terminals.js:3179) is
unconditionally `false` everywhere in it — including this test, whose own title claims
"single-pane / **mobile** mode." A mutation gating the chip behind
`(!draggable && _isMobileTerm()) ? '' : filesChip}` (the same shape wallnav.test.mjs's
CMX-133 kill-button guard already had to defeat, at terminals.js:842's `mobileFull` idiom) is
invisible to any assertion made under a matchMedia stub that can never report a phone width —
`_isMobileTerm()` simply never returns `true`, so the gate's `!draggable && _isMobileTerm()`
branch is never taken either way and the chip renders regardless of whether the gate exists.
**Guard form that survives:** when a guard's own title (or stated purpose) claims to cover a
`matchMedia`/viewport-gated code path, and the suite's shared setup hard-stubs that same media
query to one fixed value for every test, override `window.matchMedia` to the OTHER value
(phone width, `matches: true`) for the duration of that one test — restoring the original stub
in a `finally` — rather than trusting the file-wide default to exercise it. A title naming a
condition is not evidence the harness ever makes that condition true.

⚠️ **Correction (CMX-306 round 3, same PR):** round 2's two fixes were each a one-directional
close that traded its own blind spot for the mirror of the one it had just closed — the exact
family shape 52/53 name for positional defaults, showing up again here in two OTHER
dimensions (viewport, and "any index" rather than "the two named indices").

*Viewport half:* round 2 stopped hard-stubbing `matchMedia` to DESKTOP for the *whole test*
and instead stubbed it to PHONE for the whole test, to make `_isMobileTerm()` observably
`true` at least once. But a guard that renders ONLY at phone width can prove a
`_isMobileTerm()`-gated mutation *that hides the chip on phone* (round 2's own finding) — it
cannot see the mirror mutation that hides the chip on **desktop**:
`${filesChip}` → `${(!draggable && !_isMobileTerm()) ? '' : filesChip}`. Under an
all-phone-stubbed test, `_isMobileTerm()` is unconditionally `true`, so `!_isMobileTerm()` is
unconditionally `false`, and `!draggable && false` never gates anything — the mutated chip
renders every time the guard checks, for the same structural reason the all-desktop stub
missed the opposite mutation one round earlier. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3217 passed) under this mutation, phone-only stub in place. **Guard form that
survives (updated):** a viewport-gated code path needs the guard's assertions repeated at
**both** matchMedia values within the same test — not a single stub picked to make the
target condition true once — so a gate written in either polarity (`… && _isMobileTerm()` or
`… && !_isMobileTerm()`) fails one of the two renders and goes red either way.

*Positional half:* round 2's fixture reasoning ("the fallback expression only has two
non-selected operands, `wids[0]` and `wids[wids.length - 1]`, so a third fixture item rules
out both") was true of the two operands actually *written in source* — but the guard doesn't
observe source text, only the rendered value, and any `wids[i]` a mutation names is
indistinguishable to it as long as that `i` happens to land on the selected agent's index.
With the selection pinned to the middle of `['@1','@2','@3']` (index 1), the mutation
`${_ctxBarHTML(sw, false)}` → `${_ctxBarHTML(wids[1], false)}` produces `'@2'` — bit-for-bit
the same value `sw` would have — because `wids[1]` and "the selected agent" alias for this
one static fixture, exactly as `wids[0]` aliased `sw` in round 1 before a second agent was
added. Growing the fixture further does not close this the way it closed rounds 1–2: whatever
index the selection sits at, some fixed `wids[i]` expression always equals it for a fixture
that never changes selection mid-test — this is precisely the unbounded-family case shape 53
already prescribes a differential assertion for, which the original entry above incorrectly
reasoned this call site was exempt from (the exemption held only for the two *named* fallback
operands, not for an arbitrary constant index a mutation is free to pick). `pytest -q` stayed
green (3217 passed) under this mutation too, three-agent fixture in place, selection pinned to
`'@2'` for the test's whole duration. **Guard form that survives (updated again):** don't stop
at proving the chip matches the selection ONCE — change `sel.value` to a DIFFERENT agent
(here, `'@3'`, chosen so it is not `wids[1]` either) and re-render within the same test,
asserting the chip now carries the NEW selection. A constant, or any fixed-index expression,
produces the same value across both renders and cannot satisfy both assertions; only a chip
that reads the live selection on every render can. This is shape 53's fix applied to a
fallback expression instead of an index formula — the same reason shape 53 gives for why no
fixture size alone closes an unbounded family applies here once the family is "any wids[i]",
not just "wids[0] or wids[length-1]".

⚠️ **Correction (CMX-306 round 4, same PR):** round 3's fix rendered three variants —
(desktop, `'@2'`), (phone, `'@2'`), (phone, `'@3'`) — intending the first two to prove the
chip survives a viewport flip under the SAME selection. It didn't: `renderTerminals()`
memoizes on `sig = _termMode + '|' + sel.value + '|' + wids.slice().sort().join(',')`
(terminals.js:1540) and early-returns when `sig` is unchanged from the previous render
(terminals.js:1541) — and `sig` has no viewport term. Variant 2 kept `sel.value` at `'@2'`,
identical to variant 1, so its `sig` was byte-identical too: the early-return fired, the stage
was never rebuilt, and variant 2's assertions silently re-checked variant 1's stale DESKTOP
DOM under a "phone width" label. Only two renders ever actually happened —
(desktop, `'@2'`) and (phone, `'@3'`) — because those are the only two consecutive steps
where `sel.value` changed. A wid source conditioned on the viewport, in EITHER polarity,
produces exactly the value each of those two renders expects (`sw` on desktop, `wids[wids.
length - 1]` on phone), so it was invisible. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3217 passed) under that mutation. **Guard form that survives (updated once
more):** when a test drives a memoized render function across a matrix of stubbed inputs
(here: viewport × selection) and the memoization key doesn't cover every axis in that matrix,
a variant that changes only an axis OUTSIDE the key is not a render — it's a no-op that
re-asserts the previous variant's DOM under a new label. Order the variants so every
CONSECUTIVE call changes at least one axis that IS in the key (here, `sel.value`, which is)
— e.g. (desktop, `'@2'`) → (phone, `'@3'`) → (phone, `'@2'`) → (desktop, `'@3'`), where every
step flips the selection — so a real rebuild is forced regardless of which combination of
off-key axes also changed. Belt-and-suspenders: capture the rendered container node before
each call and assert its identity changed after, so a future reordering that lets two
consecutive variants share the in-key axis (silently re-collapsing onto one stale render)
fails loudly instead of quietly re-passing. This is a distinct general shape from the rest of
this entry — not a fixture-size or positional-index problem, but a **render memoization key
narrower than the dimensions the guard means to exercise** — worth naming on its own:
a test author reasons about what the *code under test* should do for each combination of
inputs, but a memoizing render path only ever sees the *sequence of calls actually made*, and
two calls that differ solely along an axis absent from the memo key are, to that function,
the same call twice.

The same round also found the wiring-revert gap from round 1's own header comment was still
open: the Files chip itself is spliced into `_ctxBarHTML`'s output with no
`draggable ? … : ''` gate (deliberately, per CMX-299 — see the production comment at
terminals.js:2246), so flipping the single-pane call site from `_ctxBarHTML(sw, false)` to
`_ctxBarHTML(sw, true)` still emits a present, correctly-wired Files chip — every assertion
this guard made about the chip's identity, wiring, title, icon, click behavior, and modal
content held bit-for-bit under the mutation. What the mutation actually does — silently
re-route the single-pane render onto the DRAGGABLE branch, which additionally emits
`.gs-idx`/`.gs-pr`/`.gs-cost` (terminals.js:2233, 2243–2245) that don't belong on the compact
mobile bar — was invisible because nothing in the guard looked at any element OTHER than the
chip it was written to cover. `pytest -q` stayed green (3217 passed) under this mutation too.
**Guard form that survives:** when a guard exists specifically to prove a render reached one
named branch of a two-branch function, assert on something that ONLY the other branch
produces, not just on the artifact the branch you care about shares with its sibling — here,
asserting `.gs-idx`, `.gs-pr`, and `.gs-cost` are ABSENT from the single-pane bar catches a
call-site flip to the wrong branch even though the thing the guard was nominally added for
(the Files chip) renders identically either way.
