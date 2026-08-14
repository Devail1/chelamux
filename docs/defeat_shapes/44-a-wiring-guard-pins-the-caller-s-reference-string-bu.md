## 44. A wiring guard pins the caller's reference string but never the callee's binding

**Assertion form:** markup reaches a JS function through a two-hop chain — `element
onclick="chela.X()"` names `X` as a *string*, which only resolves at click time by looking
`X` up on the `window.chela` object some module populates via `Object.assign(window.chela,
{...})` (or an equivalent registration surface). A guard exists for hop 1 —
`assert.match(REAL_HTML, /onclick="chela\.X\(\)"/)` — pinning that the *string* `"chela.X()"`
literally appears in the real template. Nothing pins hop 2: that `window.chela.X` actually
*is* a function by the time a click would run it.

**Mutation that defeats it:** drop `X` from the `Object.assign(window.chela, {...})` call
(or the equivalent export/registration list) while leaving the markup untouched. The hop-1
regex still matches — the literal text `onclick="chela.X()"` never changed — so it stays
green. But `window.chela.X` is now `undefined`; a real click throws a `TypeError` and the
feature is dead. The two assertions look identical in a diff (both say "chela.X is wired")
but check disjoint things: one reads a string out of a static file, the other reads a live
property off a runtime object, and only the second one can ever observe the registration
list being edited.

**Why this is distinct from [[33|shape 33]]:** shape 33 is siblings *at the same hop* — more
attributes on more elements, all still hop-1 string matches. This shape is the *next* hop of
the SAME wiring point: even a fully-enumerated set of hop-1 checks (every attribute, every
element) proves nothing about whether the names they reference actually resolve at runtime.
Closing shape 33 completely still leaves this one open.

**Guard form that survives:** after importing the real module (so its top-level
`Object.assign(window.chela, {...})` has actually run), assert
`typeof window.chela.X === 'function'` for every `X` a real `onclick` attribute names —
ideally *derived* from the onclick attributes parsed out of `REAL_HTML` itself (`[...REAL_HTML
.matchAll(/onclick="[^"]*chela\.(\w+)\(\)[^"]*"/g)]`) rather than hand-listed, so a future
inline handler is covered without anyone remembering to add a matching assertion for it.

**Found:** CMX-288 rework round 3 (2026-08-14), PR #359. Round 2 added three `REAL_HTML`
regex assertions (shape 33) proving `onclick="chela.openDecisionsMenu()"` and
`onclick="chela.hideDecisionsMenu()"` appear verbatim on the real `#btn-decisions`, the modal
backdrop, and the close button. The judge then dropped `openDecisionsMenu` (and separately
`hideDecisionsMenu`) from the `Object.assign(window.chela, {...})` surface at the bottom of
`decisions.js` — `index.html` still read the exact same `onclick` text, so all three round-2
regexes, and all 3115 pytest cases, stayed green, while a real click on the button would
throw. Closed by a single test that parses `chela.(\w+)\(\)` names out of `REAL_HTML`'s
`#decisions-menu`-related onclick attributes and asserts each one is `typeof ... ===
'function'` on the actual `window.chela` the module populated.
