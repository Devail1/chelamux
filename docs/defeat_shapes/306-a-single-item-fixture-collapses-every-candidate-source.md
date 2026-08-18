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
