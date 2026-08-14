## 45. A "derived from the real artifact" guard is re-narrowed back to a hand-list, and the artifact it derives from can't contain runtime-templated markup anyway

**Assertion form:** [[44|shape 44]]'s own prescribed fix — derive the expected `window.chela.X`
names from the `onclick`/`oninput` attributes parsed out of `REAL_HTML`, rather than
hand-listing them, "so a future inline handler is covered automatically instead of needing
someone to remember." The derivation is written, but two things quietly undo it: (a) the
parsed name list is immediately `.filter()`-ed back down to the exact names the *current*
finding named, so it reads as derived but behaves as a hand list wearing a regex; and (b) the
regex only matches one attribute kind (`onclick`) with one call shape (empty parens), so a
sibling wired via `oninput="chela.Y(this.value)"` — a different attribute, a call with an
argument — never enters the candidate set even before the filter runs.

**Mutation that defeats it:** drop a name from the `Object.assign(window.chela, {...})`
surface that the filter doesn't mention, or that the regex's attribute/arity restriction
can't match in the first place (an `oninput` handler, or an `onclick` with a non-empty
argument list like `onclick="chela.Z(this)"`). The hop-1 text in `index.html` is untouched, the
filtered/regex-restricted candidate set never contained the dropped name, so the "derived"
assertion has nothing to say about it and stays green while a real interaction throws.

**Why this is distinct from shape 44:** shape 44 is "hop 2 has no guard at all." This is what
happens the round after hop 2's guard is *written* — a fix that reads as the general form
(comment says "derived... automatically... future handler... covered") while the code one line
down re-attaches the exact same hand-enumeration shape 44 was meant to retire, just spelled as
a `.filter()` instead of a literal array. A reviewer (human or judge) who reads the comment and
sees a `matchAll` call moves on without checking whether the filter or the regex's attribute/
arity assumptions quietly shrink the set back down.

**A second, independent way the same "derive from REAL_HTML" idea fails — not fixable by
widening the regex:** some inline handlers are never present in the *static* file at all.
`decisions.js`'s `_rowHtml` builds `onclick="chela.openDecisionTicket(this)"` as a JS template
string, injected into `#decisions-list` at *runtime* — no amount of regex work against
`REAL_HTML` (a `readFileSync` of the served template) can ever see it, because the string
being scanned structurally cannot contain markup a script generates after the page has
already parsed. A "derive from REAL_HTML" guard, however written, has zero reach into a
caller wired this way; it needs a second guard that renders the real output and reads the
attribute the module actually produced.

**Guard form that survives:** derive candidate names from the real artifact with no `.filter()`
narrowing the result back to a subset chosen by hand, and widen the regex to match every
attribute kind and call arity actually used (`\b(?:onclick|oninput)="[^"]*chela\.(\w+)\([^"]*"`,
not `onclick="[^"]*chela\.(\w+)\(\)[^"]*"`) — then assert the derived set *equals* the full
expected list (not merely "these members are present"), so a future name the regex fails to
match, or a future filter someone adds, shows up as a length mismatch instead of silently
vanishing. For markup a module generates at runtime rather than shipping in the static
template, add a second, separate guard: render the real output (drive the actual render
function, not a hand-typed fixture — see [[38|shape 38]]) and compile+run its onclick attribute
through the live `window.chela`, the same way [[44|shape 44]]'s guard does for static hop 1.

**Found:** CMX-288 rework round 4 (2026-08-14), PR #359. Round 3's fix for shape 44 added
`.filter(name => name === 'openDecisionsMenu' || name === 'hideDecisionsMenu')` right after its
own `matchAll`, and its regex matched only empty-paren `onclick`. `setDecisionsQuery` (wired via
`oninput="chela.setDecisionsQuery(this.value)"` on the search box, in the same markup block
round 3 rewrote) was invisible to both restrictions; `openDecisionTicket` (wired via
`onclick="chela.openDecisionTicket(this)"`, rendered at runtime by `_rowHtml`) was invisible to
the REAL_HTML approach entirely. Dropping `openDecisionTicket` from the `Object.assign` surface
stayed green under every existing test, including round 3's own "derived" guard. Closed by
widening the REAL_HTML scan to both attribute kinds/arities with no post-hoc filter, asserting
set equality against the full expected list, and adding a second guard that renders a real
clickable row via `enterDecisions()` and runs its actual `onclick` attribute through
`window.chela`.
