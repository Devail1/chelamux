## 377e. A cascade-resolved CSS guard reads a PROXY for the fact — a shorthand, a subset of the silhouette, a token's name, a hand-rolled condition evaluator — not the value a browser actually uses

**Assertion form:** [[377d|shape 377d]]'s fix — real shell, real stylesheet flattened for the
viewport, `getComputedStyle(el)` — reading:
1. `cs.gridRow === '1'` for "the Wall is on row 1";
2. `clipPath` + `borderRadius` equal between pane header and sidebar for "same status shape";
3. the sidebar's `fontFamily` *names* `var(--ui-font)` for "the chrome is sans";
4. with `@supports` blocks unwrapped by a shortcut: "a top-level `not (…)` is false".

**Mutation that defeats it:** (PR #529 round 5, 2026-09-28, suite green — 4117 passed)
1. `.app > .canvas { grid-row-start: 2; }` — jsdom stores each declaration under the property
   NAME it was written with; it never folds a longhand back into the shorthand (nor expands
   `grid-row`/`grid-area`, or ANY shorthand carrying a `var()`). `gridRow` kept reading `"1"`.
2. `@supports not (display: chela-nonsense) { .app > .canvas { grid-row: 2; } }` — true in every
   browser; the evaluator's "`not` ⇒ false" shortcut dropped it.
3. `.gs-head .term-status-dot.idle { background: var(--text-dim); border: none; }` — idle's hollow
   ring is made by *no fill + a border*; filling it and dropping the border turns it into working's
   solid dot with clip-path and border-radius untouched.
4. `--ui-font: var(--font);` — every rule still names `--ui-font`; the token now resolves to the
   monospace stack.

**Why it looks right:** each proxy agrees with the real fact on the code as written. The gap is
what else can change the real fact without touching the proxy: a longhand, a negated condition, a
fill/border flip, a token re-pointed one level up.

**Guard form that survives:** assert the value the browser uses, in the form the cascade resolves.
- **Longhands, not shorthands.** `cssForViewport` re-emits every rule with `grid-row`/`-column`/
  `-area` and every var()-carrying shorthand (`background`, `border*`, `border-radius`,
  `padding`/`margin`, `font`) expanded to longhands; guards read `grid-row-start`,
  `border-top-left-radius`, … — the cascade then compares like with like.
- **The whole silhouette.** `shapeSignature(win, el)`: clip-path, mask, four corner radii,
  background-image, filled-or-not, per-side border width/style (colour still excluded — "shape,
  not colour"). Plus a distinctness check: the four states' signatures are pairwise different.
- **Resolve tokens.** `resolveVars(win, el, value)` substitutes `var()` chains against the
  element's computed custom properties; assert the resolved stack (first family Geist, no
  `monospace`), not the token's name.
- **Real condition grammar.** `@supports` is parsed as `not`/`and`/`or`/`( … )`/`selector(…)`;
  a declaration test is "supported" iff jsdom's own CSSOM accepts it; unknown shapes throw.
- **Both colour schemes.** Resolve under `prefers-color-scheme: light` AND `dark` — a dark-only
  override is just as real.

**Found:** CMX-377 round 5 (PR #529), judge mutations 1–4. Closed in
`tests/js_helpers/css_viewport.mjs` + `tests/restyle_real_shell_css.test.mjs`,
`tests/dashboard_shell_grid.test.mjs`, `tests/wallnav.test.mjs` (11b-3),
`tests/dashboard_scale_nav_a11y.test.mjs` (--ui-font). Each mutation re-applied → RED → restored.

**See also:** [[377d|shape 377d]] (the inputs gap this is the sequel to), [[377b|shape 377b]].
