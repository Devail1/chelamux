# Spike: can "the wall fills its stage" be guarded in a layout-less harness at all?

**Question:** CMX-268's revert of the airy-density treatment left a guard in
`tests/dashboard_scale_nav_a11y.test.mjs` (CLAIM 2) that pins four CSS properties —
`#term-stage`'s `padding-left`/`padding-right` and `.grid-stack`'s `max-width`/`width` —
to the values that mean "no centred-column cap." Three rework rounds on PR #338 each
found a new CSS spelling that produced the identical real-world regression (the wall
stops filling its stage) while leaving all four assertions green: an unresolved `vw`
unit, an unexpanded `padding-inline` logical property, a `width` + `margin: auto` pair
instead of `max-width`. Before writing a *fifth* property assertion to close the latest
hole, this spike asks the prior question directly: is "the wall visually fills its
stage" — an OUTCOME, not a property value — guardable in this test harness at all?

**Verdict: no, not as an outcome-level invariant.** `node --test` + jsdom has no layout
engine, so nothing in this harness can ever compute or observe the actual rendered box
size the claim is about. What the harness *can* do is pin individual CSS property
values, which is a bounded proxy for an unbounded set of ways to shrink a box — provably
incomplete, not just incomplete-so-far. Evidence and the recommended path below.

## jsdom has no layout engine — confirmed empirically

jsdom parses and cascades CSS (the CSSOM), but it does not lay boxes out. Every API that
would answer "how big is this element actually rendered" returns a zero or an unresolved
literal, never a computed pixel result:

```js
const dom = new JSDOM(`<div id="stage" style="width:1000px;">
  <div id="grid" style="width:100%;"></div>
  <div style="display:flex;"><div id="child" style="flex:1;"></div></div>
</div>`, { pretendToBeVisual: true });
const { document, getComputedStyle } = dom.window;

document.getElementById('stage').getBoundingClientRect();
// { x:0, y:0, top:0, left:0, right:0, bottom:0, width:0, height:0 }  — always zero

document.getElementById('stage').offsetWidth;
// 0 — always zero, regardless of any CSS

getComputedStyle(document.getElementById('grid')).width;
// "100%" — the literal percentage string, NOT resolved against the 1000px parent

getComputedStyle(document.getElementById('child')).width;
// "auto" — flex-basis/flex-grow resolution never happens; same for CSS Grid's `1fr`
```

This isn't a gap that a smarter fixture or a fifth assertion closes — it's what "jsdom
has no layout engine" *means*. `getComputedStyle` in jsdom answers "what value did the
cascade resolve for this declared property," which is a genuinely different question
from "how many pixels wide does this box render," and the second question has no answer
available anywhere in this dependency (confirmed: `jsdom` is the only DOM implementation
this repo depends on — see `package.json`'s own description of why it exists at all).

## The current 4-property guard is already defeated by a fifth vector, live

To confirm the "one more property" pattern doesn't stop at four, `transform: scale()` —
untouched by any of the four existing assertions — reproduces the exact regression the
guard exists to catch, undetected:

```js
// .grid-stack { max-width: none; width: auto; transform: scale(0.6); }
getComputedStyle(term_stage).paddingLeft   // "0px"  — guard passes
getComputedStyle(term_stage).paddingRight  // "0px"  — guard passes
getComputedStyle(grid_stack).maxWidth      // "none" — guard passes
getComputedStyle(grid_stack).width         // "auto" — guard passes
```

In a real browser this renders the wall at 60% width, centred — pixel-identical to the
reverted airy-density regression the guard was written to prevent — and every existing
assertion is green. `zoom`, `inset` + `position: absolute` + an explicit width, a
`container-type`/container-query cap, `aspect-ratio` forcing a narrow box, `overflow:
hidden` on a shrunk ancestor, or a new wrapper `<div>` inserted between `#term-stage` and
`.grid-stack` (invisible to a test whose fixture markup is hand-copied and doesn't track
the real templates structurally) are further, un-exhausted examples. CSS has no finite
list of ways to make a box not fill its container — this is the identical shape of hole
this same test file already named and conceded for CLAIM 1 (type scale) and GUARD 6
(single accent) across rounds 6/9/10/11 (see that file's own header): *"CSS has
effectively unlimited ways to make ... a value wrong, and pinning them one mutation at a
time never converges."* Property-by-property enumeration of "not capped" has the same
shape as property-by-property enumeration of "not hidden" or "not a second hue" — this
file has already converged on NOT GUARDED for both of those, for exactly this reason.

## Why this can't be fixed by a better jsdom technique

Every workaround this file has reached for so far (`DESKTOP_CSS`'s `vw`→`px`
substitution, `LOGICAL_PROP_CSS`'s logical-property expansion) is a **text-level
normalisation of the stylesheet before mounting**, not a layout computation — it makes
jsdom's cascade answer a specific already-known question honestly, but it cannot make
jsdom compute a box size that no code path in jsdom ever computes. There is no
"try harder" version of `getComputedStyle` that resolves `transform`, `flex-basis`,
`grid-template-columns`, or a percentage against a real parent width — those code paths
simply do not exist in this dependency. Closing this for real requires an actual layout
engine: a headless real browser (Playwright/Puppeteer driving Chromium) taking a real
`getBoundingClientRect()` or a pixel screenshot. Confirmed: neither exists anywhere in
this repo's dependency tree or CI (`package.json` declares exactly one devDependency,
`jsdom`; `.github/workflows/ci.yml` runs no browser). The `chela/telegram/screenshot.py`
PNG renderer and the demo-GIF capture script are unrelated one-off tools, not part of the
test suite, and not layout engines either (the former hand-draws glyph rasters, the
latter drives a real browser but only to generate marketing media, never assertions).

## Recommendation

Don't write a fifth property assertion, or a sixth. The four properties currently
guarded (`padding-left`/`padding-right`/`max-width`/`width`) stay — they're cheap,
already-written tripwires against the three concrete regressions that already happened
historically (CMX-230's original rule, and two respellings of it found in rework) — but
extending this list further is a guaranteed-incomplete chase, proven above by the fifth
vector (`transform: scale`) that already defeats it today. The outcome itself — "does
the wall visually fill its stage at every density" — moves to this file's existing NOT
GUARDED register, verified the same way this file already verifies the other claims in
that register: a manual visual check (the greyscale-capture discipline already used for
CLAIM 1 and GUARD 6), not an automated assertion this harness cannot honestly make. If
automated outcome-level coverage is ever wanted for real, it needs a headless-browser
suite (Playwright) added as new CI infrastructure — a materially bigger project than "one
more property," and out of scope for this spike.
