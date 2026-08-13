// CMX-230 — "the dashboard reads as a dev tool because of TYPE SCALE, NAV COUNT
// and HUE COUNT" (Liav's Xirp comparison, 2026-08-11). This is the ticket's own
// GUARDS list, each written to go RED under the specific corruption it names —
// same discipline as tests/kanban_lane_model.test.mjs: assert a precise value
// against the REAL source/module, not "some rule exists".
//
// Deliberate scoping note (read before extending this file): the ticket's
// accessibility ask names four abstract states — idle/working/waiting/error.
// They don't all live on one component. "error" specifically does NOT exist as
// an agent-liveness state in this codebase (chela/agent_manager.py's `liveness`
// docstring: "discovery only ever lists LIVE windows... a listed window is
// never dead" — there is no fourth agent status to fabricate one for). The
// three real state-pill families this file guards are: the wall's .gs-state
// pill (idle/working/needs-you/done/unknown — "needs-you" is this codebase's
// name for "waiting"), the sidebar's .ar-state line (idle/working/waiting), and
// the Work board's .kanban-state-chip/.kanban-card-error (failed is the real
// analogue of "error" — a task-level failure, not an agent one). All three
// already carried a non-hue cue before this ticket; what's new here is the
// guard, not the cue.
//
// CMX-257 round 6 — STRATEGY CHANGE (human directive on PR #326, superseding
// round 4's individual findings): 16+ judge rounds across #298+#326 kept
// re-litigating the SAME unbounded surface — these guards used to parse
// style.css as TEXT and pin arbitrary declarations one property at a time
// (.side-subhead alone drew FIVE separate findings: collapsed-rail hiding,
// text-present, opacity-exists, opacity-value, and — round 6 — visibility).
// That is not a guard gap, it's an infinite one: CSS has effectively
// unlimited ways to make an element invisible or a value wrong, and pinning
// them one mutation at a time never converges. What follows guards the
// ticket's four claims ONCE each, at the level of RESOLVED EFFECT (the actual
// number/behaviour a browser would compute), not by enumerating properties:
//   1. type scale — every wall/pane/card text selector's RESOLVED font-size/
//      line-height comes from the --wall-pane-*/--card-* tokens and never
//      sits below the pre-CMX-230 legibility floor (see "TYPE SCALE" below).
//   2. airy density — REVERTED by CMX-268 (Liav, 2026-08-13: "the wall was
//      better when it took the full space" — a product call on the shipped
//      Xirp-style air, not a defect). The wall-density-airy class, its CSS
//      rule and _setWallDensity are gone. CMX-268 rework round 1 (PR #338):
//      a pure deletion left no guard against the treatment coming back — see
//      "CLAIM 2 (airy density), REVERTED" below for the OPPOSITE invariant
//      (no horizontal padding / no column cap at 1-up and 2-up) plus the
//      companion class-check in tests/wallnav.test.mjs.
//   3. nav inventory — the primary rail is exactly the 3 domain objects, the
//      4 demoted views are present (not deleted) and actually render — occupy
//      space and paint — under the More subhead (see "nav inventory" /
//      ".side-subhead and #side-nav-more" below).
//   4. non-hue cue — every real status family carries a glyph/word, not just
//      colour; this is the accessibility hard requirement and the one claim
//      here that must never regress (see "GUARD 3" below).
// A handful of adjacent, already-converged guards (GUARD 4/5/6/7, the
// .side-list-secondary weight/wiring pair) are kept as-is: they weren't the
// source of the recurring findings and already assert a resolved value or a
// real function's behaviour, not a property-presence scan.
//
// CMX-257 round 9 — HARD NARROWING (human directive on PR #326, superseding
// round 6's "resolved effect" framing): round 6 collapsed property-by-property
// pins into ONE assertion per claim, but each of those assertions was still
// UNIVERSALLY QUANTIFIED ("every wall/pane/card text selector", "the demoted
// group actually renders") — an unbounded surface with a label on it, not a
// bounded one, and the judge kept finding new holes in the same hand-rolled
// CSS resolver (cssRules/resolveAllForContext) for three more rounds. The
// fix isn't a smarter resolver, it's not being one: claims 1-3 below now
// mount the REAL style.css in jsdom (same technique as
// tests/wire_live_css.test.mjs) against a CONCRETE, ENUMERATED fixture — one
// real wall tile, one real sidebar card, one real nav section — and read
// jsdom's own getComputedStyle/custom-property cascade, which is verifiably
// correct for inheritance and specificity (unlike this file's old hand-rolled
// selectorSpecificity()) even though it can't expand var() into a used
// font-size or resolve vw units. Where jsdom genuinely cannot resolve a value
// honestly (clamp()'s vw term — confirmed empirically: it falls back to the
// initial value, indistinguishable from a corrupted flat 0), that specific
// number is NOT GUARDED rather than approximated.
//
// CLAIM 4 (non-hue cue) is the exception: Liav is red-weak and a status
// distinguishable only by hue is the one regression that would actually hurt
// him, so it stays fully guarded and gets the most direct proof available —
// the kanban chip/error text are now asserted off a REAL renderKanban() call
// into a REAL DOM, not a source-text regex pair with the interpolated value
// unpinned (see "WIRING: the kanban card" below).
//
// CMX-257 round 10 — GUARD LESS, DECISIVELY (human directive on PR #326,
// superseding round 9's jsdom-mount framing for claims 1-3): round 9's own
// findings 1/2/3 came back VERBATIM against round 10's mutations — the
// type-scale legibility floors, the demoted-nav-group weight comparison, and
// the airy-density rule's horizontal-only padding — proof that the REMAINING
// guarded half of this file was the wrong half. All three are CSS-VALUE
// assertions (a resolved font-size/line-height, a font-size/weight
// COMPARISON between two elements, which padding edges one rule sets), and
// no test in this file — hand-rolled resolver or jsdom mount alike — has ever
// closed that surface for more than one or two rounds before the judge found
// the next notation/selector-shape angle. Each of the three is now removed
// (not just narrowed) and moved into the NOT GUARDED list below; the
// jsdom-mount fixtures/machinery those tests were the ONLY callers of
// (WALL_TILE_FIXTURE/CARD_FIXTURE/assertTokenFontSize/assertTokenLineHeight,
// resolveAllForContext/selectorSpecificity and the PRIMARY_*/SECONDARY_*
// ancestor sets) are deleted with them, not left as dead code. CLAIM 4 stays
// fully guarded, unchanged by this round (see immediately above) — it is DOM
// and text, which jsdom CAN resolve truthfully, and it is the one claim here
// that would actually hurt a human (Liav is red-weak) rather than offend a
// linter.
//
// CMX-257 round 11 (human directive on PR #326, superseding round 10 for
// GUARD 6 specifically): round 10 kept GUARD 6 ("single accent" — every
// `.active` rule's highlight colour is --accent or a neutral, never a second
// hue) on the theory that resolving a colour to its actual saturation, rather
// than pattern-matching its notation, would close the class of holes that
// defeated rounds 8-10 (rgb(), then rgba()/hsl(), then bare keywords, then a
// keyword nested inside a function). It didn't: colorToRgb only parses hex
// and rgb()/rgba(), and silently `continue`s past anything else (a bare
// hsl() literal, for instance) — failing OPEN, the same shape of hole one
// notation later. GUARD 6 is a CSS-COLOUR-VALUE assertion, the same class as
// claims 1-3 above, and the round-9/10 header already explains why that
// class can't be closed by a text-and-DOM test: CSS colour syntax is
// open-ended, so a guard that must parse a value to classify it always has
// one more notation the parser doesn't cover. Moved to NOT GUARDED below,
// its machinery (the CSS-cascade parser — cssRules/declarations/
// resolvedRootVars/resolvedBodyAtDesktop/mediaSatisfiedAtViewport — and the
// colour-classification helpers — hexToRgb/saturation/isNeutralRgb/sameRgb/
// findVarCalls/resolveVarExpr/colorToRgb/stripVarCalls/CSS_COLOR_KEYWORDS)
// deleted with it, since GUARD 6 was their only remaining caller once claim
// 1's type-scale tests left in round 10. Finding 2 in the same round-11
// verdict is a different kind, and was NOT moved: views.js's "RE-PARENTING,
// NOT REMOVAL" is a DOM fact (does the demoted row still occupy space and
// paint under #side-nav-more, not just does the container exist), which
// jsdom resolves truthfully the same way it does for .side-subhead
// immediately above it (round 14 below narrows this further, but keeps it
// guarded — it never moved to NOT GUARDED).
//
// CMX-257 round 14 — FINAL NARROWING (human directive on PR #326, superseding
// rounds 1-13's growing enumeration of .side-subhead/demoted-group aspects):
// by round 13, .side-subhead alone had drawn findings across TEN rounds —
// hiding, text-present, opacity-exists, opacity-readable, position, renders,
// readable-heading, entity-blank, same-row-styling, and round 13's own
// human-authored enumerated list turning out to have a gap itself (a
// corrupted `color: transparent` satisfied every item on that list, since
// none of them read colour). Each round closed the SPECIFIC aspect named and
// the next round found another aspect of the SAME element — proof the
// element has an unbounded number of visual aspects, not that the guard was
// nearly complete. CLAIM 3 is now reduced to the ONE DOM fact views.js's own
// comment actually claims — the four demoted views still EXIST and RENDER
// (occupy space, are not display:none/visibility:hidden) under a heading
// that itself exists and carries real text — asserted in one consolidated
// test below. Everything else about .side-subhead and the demoted group
// (precise opacity/font-size/colour values, exact source position/ordering,
// and any font-size/weight COMPARISON to the primary rail's own rows) moves
// to NOT GUARDED, item (v) below.
//
// CMX-273 spike (orchestrator, after CMX-268 merged over a blocked verdict):
// three rework rounds on PR #338 each found a new spelling of "the wall stops
// filling its stage" that the four assertions below (padding-left/-right,
// max-width, width) didn't cover (an unresolved `vw`, an unexpanded
// `padding-inline`, a `width`+`margin:auto` pair). Before writing a fifth
// property assertion, this spike asked whether the OUTCOME itself — "does
// the wall visually fill its stage" — is guardable at all in this harness.
// Verdict: no. jsdom has no layout engine (getBoundingClientRect/offsetWidth
// are always zero, percentage/flex/grid widths never resolve against a real
// parent — see docs/SPIKE_WALL_FILLS_STAGE.md), so this file can only ever
// pin an enumerated list of known-bad property values, never observe the
// rendered result. Confirmed live: `transform: scale(0.6)` on `.grid-stack`
// reproduces the identical regression today and passes all four assertions
// below untouched — the same "unbounded CSS surface" shape as CLAIM 1 and
// GUARD 6 above. The four properties stay (cheap tripwires against the three
// regressions that already happened) but this is deliberately NOT extended
// with a fifth/sixth property; see item (vi) below and the spike doc for the
// full evidence and the Playwright-sized project that would be needed to
// guard the outcome for real.
//
// NOT GUARDED here — verified instead by manual greyscale capture (per the
// round-6 directive: "I verified it live on an isolated dashboard... a
// greyscale capture showing every status distinguishable with hue fully
// removed"), and deliberately not re-litigated property-by-property in this
// file: exact opacity/spacing/padding VALUES beyond the specific floors and
// margins asserted below, font weights, precise source order beyond what's
// asserted explicitly (#side-nav < .side-subhead < #side-nav-more), a
// bare-literal value that happens to already clear a floor (detokenisation
// that doesn't also regress the number is out of scope for this file),
// EXHAUSTIVE selector coverage across the stylesheet beyond the concrete
// fixtures enumerated below (the greyscale capture at 1/2/4/6 densities is
// its acceptance check);
// — round 10, removed rather than re-narrowed —
// (i) TYPE SCALE (ticket claim 1): the --wall-pane-*/--card-* tokens'
// RESOLVED font-size/line-height at the wall tile / sidebar card text
// selectors never sitting below the pre-CMX-230 legibility floor (11px /
// 11px / 1.45 / >10px for the .gs-state escape hatch) — the greyscale
// capture at 1/2/4/6 densities is its acceptance check;
// (ii) the demoted nav group (.side-list-secondary) rendering its icon/label
// font-size strictly, visibly lighter than the primary rail's own — the
// greyscale capture is its acceptance check (the WIRING test that nav.js
// still emits the .side-item-icon/.side-item-label classes the demotion rule
// depends on stays guarded, below);
// (iii) — REMOVED, not narrowed: CMX-268 reverted the airy-density treatment
// entirely (wall-density-airy, its style.css rule, and _setWallDensity are
// gone — see claim 2 above), so there is no rule or class left for this item
// to describe;
// (iv) GUARD 6, single accent (ticket claim 3's colour half): every `.active`
// rule's highlight colour is --accent or a neutral, never a second hue — CSS
// colour syntax is open-ended (hex, rgb()/rgba(), hsl()/hsla(), bare
// keywords, keywords or hues nested inside color-mix()/other functions, and
// whatever notation comes next), so a guard that has to parse a colour value
// to classify it always has one more notation it doesn't cover (round 11:
// resolving var() references to actual saturation instead of a name
// allowlist still only parsed hex/rgb(), and failed OPEN — silently skipped —
// on anything else, e.g. a bare hsl() literal) — the greyscale capture at
// 1/2/4/6 densities is its acceptance check.
// — round 14, FINAL narrowing of CLAIM 3 —
// (v) .side-subhead's and the demoted group's own precise STYLING: the exact
// opacity/font-size/colour values the heading and rows render with beyond
// "not display:none, not visibility:hidden" (asserted below), the exact
// source POSITION/ordering of .side-subhead relative to #side-nav and
// #side-nav-more, and any font-size/weight COMPARISON between the demoted
// rows and the primary rail's own rows ("renders lighter than primary" —
// same CSS-value-comparison class as (ii) above, which this subsumes) — the
// greyscale capture at 1/2/4/6 densities is its acceptance check. What stays
// guarded is the one DOM fact below: the four demoted views still exist and
// render under a heading that itself exists and carries real text —
// "RE-PARENTING, NOT REMOVAL", views.js's own claim.
// — CMX-273 spike —
// (vi) "the wall visually fills its stage" as an OUTCOME (does .grid-stack
// actually render at full width at every density, by whatever CSS mechanism
// might narrow it — transform/zoom/inset/container-queries/aspect-ratio/a
// new wrapper element/anything else) — jsdom has no layout engine, so no
// text-and-DOM test in this file can observe a rendered box size, only pin
// enumerated property VALUES (see docs/SPIKE_WALL_FILLS_STAGE.md for the
// empirical proof and a live fifth counter-example). What stays guarded,
// deliberately not extended further, is the narrow, cheap claim below:
// #term-stage's own padding-left/-right and .grid-stack's own max-width/
// width resolve to the specific values the three CMX-268 regressions
// actually broke. A real outcome-level guard needs a headless-browser suite
// (Playwright), not present in this repo; until then this claim's acceptance
// check is a manual visual look at the live dashboard, same discipline as
// (iv) and the type-scale half of claim 1 above.
//
// Run: node --test tests/dashboard_scale_nav_a11y.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

import { tileState } from '../chela/dashboard/static/js/wallmodel.js';
import { primaryNavViews, secondaryNavViews } from '../chela/dashboard/static/js/viewreg.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const src = p => readFileSync(join(ROOT, p), 'utf8');

const CSS = src('static/style.css');
const TERMINALS = src('static/js/terminals.js');
const NAV = src('static/js/nav.js');
const KANBAN = src('static/js/kanban.js');
const VIEWS_SRC = src('static/js/views.js');

// --- jsdom fixtures: mount the REAL style.css and a concrete, hand-written
// fragment of REAL markup (mirroring terminals.js's paneHead()/
// _wallTileHTML()/_ctxBarHTML(), nav.js's _agentRowHtml(), and index.html's
// sidebar section closely enough to match every selector these guards read —
// verified against those sources as of round 9) in jsdom, then read jsdom's
// OWN getComputedStyle/custom-property cascade. This replaces this file's old
// hand-rolled cssRules()/resolveAllForContext() cascade for the three claims
// that kept finding new holes in it (round-9 header comment above has the
// full rationale). jsdom's custom-property INHERITANCE/cascade is verified
// correct (a `body { --x: ... }` override IS visible to a descendant's
// getPropertyValue) even though it does not expand var() into a resolved
// used value for shorthand-like properties such as font-size — computed
// fontSize/lineHeight stays the literal string "var(--name)" when THAT
// declaration is the cascade winner, and becomes a resolved literal instead
// when some other declaration outranks it. Both facts are used below: the
// declaration-identity check (which selector actually won) and the
// custom-property VALUE check (what that token really resolves to,
// inheritance included) are two different questions, and jsdom answers both
// honestly for everything except `vw`, which it does not resolve at all
// (confirmed empirically — see the NOT GUARDED note at the top of this file).
function mountWithRealCss(bodyHtml, extraBodyAttrs, cssOverride) {
    const dom = new JSDOM(
        `<!doctype html><html><head><style>${cssOverride || CSS}</style></head><body${extraBodyAttrs || ''}>${bodyHtml}</body></html>`,
        { pretendToBeVisual: true });
    return dom.window;
}

// --- CLAIM 3, round 22 (human directive on PR #326, judge finding 1+2 on
// round 21's own guard): getComputedStyle(el).display only ever answers
// "what is THIS node's own resolved display" — an ancestor's display: none
// does NOT change a descendant's computed display (computed value, not used
// value; true in a real browser exactly as much as in jsdom, confirmed by
// the judge's mutation on `.side-list-secondary` itself surviving every
// per-node display/visibility read below it). Rounds 14/19/20/21 each pinned
// the next node down the chain one at a time (row, then label, then icon)
// and the judge kept hiding the node ABOVE the ones enumerated. The fix is
// not a fourth node, it's the question itself: can the user see this row AT
// ALL, which means every ancestor from the node up to the mount root must be
// checked, not just the node's own declaration. jsdom does no layout (no
// offsetParent shortcut), so walking parentElement and reading each one's
// own getComputedStyle IS the honest mechanism — the same thing a browser
// does to decide whether a box generates at all.
function _visibleInTree(win, el) {
    for (let node = el; node; node = node.parentElement) {
        const cs = win.getComputedStyle(node);
        if (cs.display === 'none') return false;
        if (/^(hidden|collapse)$/.test(cs.visibility)) return false;
    }
    return true;
}

// jsdom's getComputedStyle has NO pseudo-element support at all (confirmed
// empirically: `getComputedStyle(el, '::before')` prints "Not implemented" and
// returns the same 'normal'/'auto' values whether or not a matching ::before
// rule exists — the request-2 assertion below does not use it for that reason).
// What jsdom's CSSOM DOES do honestly is parse the real stylesheet and let
// `Element.matches()` answer selector-matching questions. A `::before` rule only
// generates a pseudo-box on elements matching its BASE selector (the part before
// the `::before`), so stripping the pseudo and asking `matches()` answers the
// exact "does this rule still REACH the re-parented row" question directly —
// the same mechanism a browser uses to decide which elements get the box at all.
function _activeRailReaches(win, el) {
    for (const rule of win.document.styleSheets[0].cssRules) {
        if (!rule.selectorText || !rule.selectorText.includes('::before')) continue;
        for (const part of rule.selectorText.split(',').map(s => s.trim())) {
            if (!part.endsWith('::before')) continue;
            if (el.matches(part.slice(0, -'::before'.length).trim())) return true;
        }
    }
    return false;
}

// --- TYPE SCALE (ticket claim 1) — CMX-257 round 10 (human directive on PR
// #326, superseding round 9's jsdom-cascade framing): this used to assert the
// --wall-pane-*/--card-* tokens' RESOLVED font-size/line-height via jsdom's
// getComputedStyle. Findings 1/2/3 across rounds 3/6/9/10 are the same hole
// wearing different clothes — a CSS-VALUE assertion always has one more
// notation/selector-shape angle a text-and-DOM test cannot honestly close.
// Moved to the NOT GUARDED block at the top of this file; the acceptance
// check is the manual greyscale capture, not another resolver.

// --- CLAIM 2 (airy density), REVERTED — CMX-268 rework round 1 (human
// directive on PR #338): the deletion itself (body.wall-density-airy, its
// style.css rule, _setWallDensity and both call sites) is correct and
// untouched by this round. What a pure deletion leaves behind is a hole: no
// guard tells a future PR the treatment must not come back. This asserts the
// OPPOSITE of what the deleted WIRING tests used to prove — at the two
// densities that used to trigger it (1-up, 2-up, the boundary AND the shipped
// default), #term-stage resolves zero horizontal padding, and .grid-stack
// resolves max-width: none, i.e. no centred-column cap — asserted as ABSOLUTE
// values (round 2, see below), not a diff against a second fixture. This is
// the CSS-side half of the guard: the class is hardcoded onto
// <body> by hand (real code never sets it post-revert — see the WIRING test
// in tests/wallnav.test.mjs, which drives the real applyGridLayout and reads
// the class back), so a judge mutation that re-adds either deleted style.css
// rule trips THIS test on its own, independent of whether terminals.js's
// toggle line ever comes back. The class-half (does applyGridLayout ever
// ADD wall-density-airy for real) belongs next to what used to be test 3b in
// wallnav.test.mjs instead — that file has no real style.css mounted, so it
// cannot answer the CSS-side question, and this file has no real terminals.js
// import, so it cannot answer the JS-side one. Together they cover a
// half-restore of either piece alone (see wallnav.test.mjs's comment).
const WALL_FIXTURE = ups => `<div class="app"><main class="canvas" id="canvas"><div class="panel" id="panel-terminals">
  <div id="term-stage"><div class="grid-stack">${
    '<div class="grid-stack-item"><div class="grid-stack-item-content"></div></div>'.repeat(ups)
}</div></div>
</div></main></div>`;

// jsdom cannot resolve `vw` at all (confirmed empirically — same finding the
// removed WIRING test worked around, see the NOT GUARDED note at the top of
// this file): any `Nvw` term falls back to jsdom's initial-value 0px,
// indistinguishable from an honest 0px. CMX-268 rework round 2 (human
// directive on PR #338, finding 2): the original substitution string-matched
// the exact deleted literal `clamp(16px, 6vw, 64px)`, so a re-add that is
// byte-different but pixel-identical in a browser (different whitespace, a
// different clamp() shape, a bare `width: 6vw` instead of a clamp() term at
// all) would silently read 0px and never trip the guard below. Generalise
// instead: replace EVERY `Nvw` occurrence anywhere in the real stylesheet
// with its resolved pixel value at a 1920px desktop viewport (1vw = 19.2px)
// before mounting. This is a value swap on the unit itself, not a
// hand-rolled resolver, and it composes correctly with whatever CSS
// function the vw term sits inside (clamp() still computes its own
// min/max against the substituted literal), so it stays correct regardless
// of how a future vw-bearing rule is spelled or shaped.
const DESKTOP_CSS = CSS.replace(/(\d+(?:\.\d+)?)vw/g, (_, n) => `${Number(n) * 19.2}px`);

// CMX-268 rework round 3 (judge finding 1 on PR #338): jsdom's CSSOM PARSES
// `padding-inline` as a property (confirmed empirically —
// `getComputedStyle(el).getPropertyValue('padding-inline')` returns the raw
// declared value) but does NOT expand a CSS Logical Property into the
// physical paddingLeft/paddingRight longhands the guard below reads —
// confirmed empirically: `#term-stage { padding-inline: 64px; }` resolves
// paddingLeft/paddingRight to 0 in jsdom even though it is pixel-identical to
// `padding-left: 64px; padding-right: 64px;` in a real browser. That gap let
// the judge's mutation (the same ungated gutters spelled as padding-inline,
// the idiom someone reaching for logical properties in 2026 most plausibly
// writes) read 0px on both sides and stay invisible to the guard. Expand
// every logical padding-inline declaration in the mounted stylesheet to its
// physical LTR longhands before mounting — a text-level normalisation of the
// SPELLING, the same technique DESKTOP_CSS already uses for vw units above,
// not a hand-rolled cascade/logical-property resolver — so it composes
// correctly regardless of what selector or declaration order the logical
// spelling appears inside.
const LOGICAL_PROP_CSS = DESKTOP_CSS
    .replace(/padding-inline\s*:\s*([^;]+);/g, (_, v) => {
        const [left, right = left] = v.trim().split(/\s+/);
        return `padding-left: ${left}; padding-right: ${right};`;
    })
    .replace(/padding-inline-start\s*:\s*([^;]+);/g, 'padding-left: $1;')
    .replace(/padding-inline-end\s*:\s*([^;]+);/g, 'padding-right: $1;');

// CMX-268 rework round 2 (human directive on PR #338, finding 1): the
// original guard compared the "airy" fixture's resolved padding/max-width
// against a SEPARATE "base" (no-class) fixture mounted from the SAME
// stylesheet. Any horizontal padding added to #term-stage UNGATED (no class
// involved at all — the natural shape of a real regression, and exactly the
// judge's mutation) moves both fixtures equally, so the differential
// cancels and the assertion holds no matter how wide the gutters get. The
// fix is to assert the ABSOLUTE resolved value instead: #term-stage never
// carries horizontal padding and .grid-stack never carries a max-width,
// full stop — read off computed style, with no reference to
// wall-density-airy anywhere in the assertion itself. The class is still
// hardcoded onto <body> when mounting (real code never sets it post-revert
// — see the WIRING test in tests/wallnav.test.mjs) purely so a class-gated
// re-add of the deleted rule is ALSO exercised by this same fixture; an
// ungated re-add applies regardless of the class and is caught the same way.
for (const ups of [1, 2]) {
    test(`airy density (REVERTED, CMX-268): at ${ups}-up, #term-stage has no horizontal padding`, () => {
        const win = mountWithRealCss(WALL_FIXTURE(ups), ' class="wall-density-airy"', LOGICAL_PROP_CSS);
        const cs = win.getComputedStyle(win.document.getElementById('term-stage'));
        assert.equal(cs.paddingLeft, '0px',
            `#term-stage's padding-left at ${ups}-up is ${cs.paddingLeft}, not 0px — #term-stage has gained ` +
            'horizontal padding');
        assert.equal(cs.paddingRight, '0px',
            `#term-stage's padding-right at ${ups}-up is ${cs.paddingRight}, not 0px — #term-stage has gained ` +
            'horizontal padding');
    });

    test(`airy density (REVERTED, CMX-268): at ${ups}-up, .grid-stack resolves max-width: none — no centred column cap`, () => {
        const win = mountWithRealCss(WALL_FIXTURE(ups), ' class="wall-density-airy"', LOGICAL_PROP_CSS);
        const gridCs = win.getComputedStyle(win.document.querySelector('.grid-stack'));
        assert.equal(gridCs.maxWidth, 'none',
            `.grid-stack's max-width at ${ups}-up is ${gridCs.maxWidth}, not none — .grid-stack has gained a ` +
            'centred-column cap');
        // CMX-268 rework round 3 (judge finding 2): max-width: none rules out
        // only ONE spelling of a centred-column cap. The identical visual
        // outcome — the wall stops filling its stage, centred in a fixed
        // column — is equally reachable via an explicit `width` (confirmed
        // empirically: jsdom resolves an unset width to 'auto', and a
        // rule like `.grid-stack { width: 1400px; margin: 0 auto; }`
        // resolves width to the literal '1400px', invisible to a max-width-
        // only check). Assert width stays unconstrained too, so this guards
        // the OUTCOME ("no cap", by any property that can produce one) —
        // not just the single property the deleted rule happened to use.
        assert.equal(gridCs.width, 'auto',
            `.grid-stack's width at ${ups}-up is ${gridCs.width}, not auto — .grid-stack has gained a fixed/` +
            'capped width even though max-width is unset, producing the same centred-column regression');
    });
}

// --- GUARD 3: non-hue cue, per real state family — deleting the glyph/word
// span (or the text it carries) and leaving only the colour class must fail.

// 3a. The wall's .gs-state pill: tileState() (wallmodel.js, pure) is the ONE
// source both the initial paneHead markup and every live repaint
// (_applyWallTileFrame) draw from — see terminals.js below. Every state it can
// report must carry a non-empty glyph AND a non-empty word; blanking either
// for any one state (idle/working/needs-you/done/unknown) fails here.
test('non-hue cue — wall .gs-state pill: every tileState() result carries a glyph AND a word', () => {
    const cases = [
        { label: 'idle', agent: null, wants: false },
        { label: 'working', agent: { session_status: 'busy' }, wants: false },
        { label: 'needs-you (waiting)', agent: {}, wants: true },
        { label: 'done', agent: { session_status: 'idle', pr: { url: 'https://x' } }, wants: false },
        { label: 'unknown', agent: { claude_running: true, session_status: null }, wants: false },
    ];
    for (const c of cases) {
        const s = tileState(c.agent, c.wants);
        assert.ok(s.glyph && s.glyph.trim(), `${c.label}: tileState() glyph is empty — colour would be the only cue`);
        assert.ok(s.word && s.word.trim(), `${c.label}: tileState() word is empty — colour would be the only cue`);
    }
});

test('non-hue cue — wall .gs-state pill: both the initial paint AND every live repaint set the glyph + word text nodes', () => {
    // Initial paint: paneHead's own literal markup (terminals.js), before the
    // first live repaint ever runs.
    // GUARD 3a round 9: `[^<]*` matches ZERO characters too, so an emptied
    // glyph (colour-only) satisfies this — the same "blank a live-repainted
    // value" hole rounds 2-4 closed for _applyWallTileFrame's three statements,
    // one instance earlier in the initial markup. Require at least one
    // character between the tags.
    assert.match(TERMINALS, /gs-state-glyph[^>]*>[^<]+<\/span><span class="gs-state-word">idle<\/span>/,
        'paneHead\'s initial .gs-state markup no longer carries both a non-empty glyph and the word "idle"');
    // Live repaint: _applyWallTileFrame must write BOTH text nodes from
    // tileState()'s result, not just recolour the pill via className.
    const frame = TERMINALS.slice(TERMINALS.indexOf('function _applyWallTileFrame'));
    const body = frame.slice(0, frame.indexOf('\nfunction ', 10));
    assert.match(body, /el\.className\s*=\s*'gs-state gs-state-'\s*\+\s*s\.cls/,
        '_applyWallTileFrame must still recolour the pill from tileState().cls');
    assert.match(body, /g\.textContent\s*=\s*s\.glyph/,
        '_applyWallTileFrame no longer repaints the glyph text node — a live state change would go hue-only');
    assert.match(body, /w\.textContent\s*=\s*s\.word/,
        '_applyWallTileFrame no longer repaints the word text node — a live state change would go hue-only');
});

// 3b. The sidebar's .ar-state line: idle/working/waiting, each a real word
// (nav.js's _AGENT_STATUS_WORD), rendered as the span's TEXT content, not just
// its class.
test('non-hue cue — sidebar .ar-state: idle/working/waiting each map to a real word, not blank', () => {
    const m = NAV.match(/_AGENT_STATUS_WORD\s*=\s*\{([^}]*)\}/);
    assert.ok(m, '_AGENT_STATUS_WORD not found in nav.js');
    const map = Object.fromEntries(
        [...m[1].matchAll(/(\w+):\s*'([^']*)'/g)].map(mm => [mm[1], mm[2]]));
    for (const [color, want] of [['green', 'working'], ['yellow', 'waiting'], ['grey', 'idle']]) {
        assert.equal(map[color], want, `_AGENT_STATUS_WORD.${color} must be the word "${want}"`);
    }
});

test('non-hue cue — sidebar .ar-state: the rendered row interpolates the WORD into the element, not just a colour class', () => {
    assert.match(NAV, /<span class="ar-state \$\{stCls\}">\$\{stWord\}<\/span>/,
        '_agentRowHtml no longer renders stWord as the .ar-state text — a red-weak reader would see only the colour class');
});

// 3c. The Work board's closest real analogue of "error": a failed task. The
// WORD is what the ticket demands survive greyscale — kanban.js's STATUS_CHIPS
// already carries one per state; this pins 'failed' specifically, and that the
// card-level last_error message renders as real text, not colour-only.
test('non-hue cue — kanban "error" analogue (failed): the status chip carries a real word, not colour alone', () => {
    const m = KANBAN.match(/failed:\s*\{\s*label:\s*'([^']*)'/);
    assert.ok(m, 'STATUS_CHIPS.failed not found in kanban.js');
    assert.ok(m[1].trim().length > 0, 'STATUS_CHIPS.failed.label is empty — the failed chip would be colour-only');
    assert.match(m[1], /failed/i, 'STATUS_CHIPS.failed.label must literally say "failed"');
});

// CMX-257 round 9 (CLAIM 4's exception — stays fully guarded, gets the most
// direct proof available per the human directive): the test above only pins
// STATUS_CHIPS.failed's label CONSTANT — round 7's source-regex pair pinned
// the render call site's TEXT but not the VALUE travelling between them, and
// the judge blanked chipMeta.label right where it's looked up (leaving both
// regexes byte-identical). This drives the REAL renderKanban() into a REAL
// DOM (same jsdom-import technique as tests/wallnav.test.mjs/
// tests/sidebar.test.mjs: real main.js entry order, then the target module)
// and reads the rendered .kanban-state-chip/.kanban-card-error text nodes
// back — mirrors the wall .gs-state pill's WIRING test above and
// sidebar.test.mjs:447's real `.ar-state` read, the two members of this guard
// family that have never fallen to a judge mutation because they assert the
// rendered node, not source text.
test('WIRING: a REAL rendered kanban card shows its status chip word AND its error text — not colour-only', async () => {
    const BODY = '<div id="work-board"><div id="kanban-board"></div><div id="kanban-empty"></div><div id="kanban-filters"></div></div>';
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        // defineProperty, NOT assignment — globalThis.navigator is getter-only
        // from node 21 (see tests/wall.test.mjs's note).
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;

    // Browser-faithful import order: main.js is the entry (nav <-> main is a
    // cycle — import anything else first and its `let`s are in their TDZ).
    await import('../chela/dashboard/static/js/main.js');
    const kanban = await import('../chela/dashboard/static/js/kanban.js');

    kanban.renderKanban({
        configured: true,
        workflows: [{
            path: 'wf.yaml',
            recent_runs: [{ task_id: 'T1', status: 'failed', last_error: 'boom', ended_at: '2026-01-01' }],
        }],
    });

    const chip = document.querySelector('#kanban-board .kanban-state-chip');
    assert.ok(chip, 'no .kanban-state-chip rendered for a failed card');
    assert.match(chip.textContent, /failed/i,
        'the REAL rendered .kanban-state-chip has no "failed" text — only its st-failed colour class would remain, ' +
        'the exact hue-only regression this guard exists to prevent');

    const err = document.querySelector('#kanban-board .kanban-card-error');
    assert.ok(err, 'no .kanban-card-error rendered for a failed card with last_error set');
    assert.match(err.textContent, /boom/,
        'the REAL rendered .kanban-card-error has no error text — only its red colour would remain');
});

// --- GUARD 4: a context-% threshold is never colour-only — the exact number
// (and, on the wall, the raw token count too) renders as text alongside the
// warn/danger class.
test('context-% threshold non-hue cue — sidebar .ar-ctx renders the numeric percentage as text, class is reinforcement only', () => {
    assert.match(NAV, /class="ar-ctx \$\{cls\}"[^>]*>\$\{p\}%<\/span>/,
        'the sidebar context chip no longer interpolates the numeric percentage as text');
});

test('context-% threshold non-hue cue — the wall .gs-ctx chip renders "N% · used/total" as text, class is reinforcement only', () => {
    const fn = TERMINALS.slice(TERMINALS.indexOf('function _applyTermContext'));
    const body = fn.slice(0, fn.indexOf('\nfunction ', 10));
    assert.match(body, /ctxChip\.textContent\s*=\s*`\$\{pct\}%/,
        '.gs-ctx no longer sets its textContent from the numeric pct — a threshold crossing would be colour-only');
    assert.match(body, /ctxChip\.className\s*=\s*'gs-ctx'\s*\+\s*\(sev/,
        '.gs-ctx no longer applies the warn/danger class as a SEPARATE reinforcement on top of the text');
});

// --- GUARD 5: footer completeness — model, spend, branch, context% and tokens
// (the exact 5 fields the ticket says "this ticket does NOT trade away") must
// all still exist in the wall pane footer. Removing any one field's chip must
// fail here, NAMING the missing field.
test('wall pane footer completeness: model + spend + branch + context% + tokens all present', () => {
    const fn = TERMINALS.slice(TERMINALS.indexOf('function _ctxBarHTML'));
    const body = fn.slice(0, fn.indexOf('\nfunction ', 10));
    const FIELDS = {
        'model': /class="gs-model"/,
        'spend (cost)': /class="gs-cost"/,
        'branch': /class="gs-branch"/,
        // context% AND tokens both render inside the SAME .gs-ctx chip — see
        // _applyTermContext's `${pct}%...${counter}` template, asserted above.
        'context% / tokens (gs-ctx)': /class="gs-ctx"/,
    };
    for (const [name, re] of Object.entries(FIELDS)) {
        assert.match(body, re, `the wall pane footer is missing its ${name} field`);
    }
});

// --- GUARD 7: nav inventory — the primary rail is EXACTLY 3 domain objects
// (Feed/Wall/Work); Knowledge/Agents/Personas/Cost are demoted, not deleted
// (still enabled, still reachable — see primaryNavViews/secondaryNavViews's
// shared navViews() base). Promoting any demoted view back to `tier: 'primary'`
// — or forgetting to demote a new one — must fail here.
function shippedViewEntries() {
    const body = VIEWS_SRC.split('export const VIEWS')[1];
    const ids = [...body.matchAll(/^\s+id:\s*'([^']+)'/gm)];
    return ids.map((m, i) => {
        const start = m.index;
        const end = i + 1 < ids.length ? ids[i + 1].index : body.length;
        const block = body.slice(start, end);
        const tierM = block.match(/tier:\s*'([^']+)'/);
        return { id: m[1], virtual: /virtual:\s*true/.test(block), tier: tierM ? tierM[1] : undefined };
    });
}

test('nav inventory: the shipped primary rail is exactly Feed, Wall, Work — nothing more, nothing less', () => {
    const entries = shippedViewEntries().filter(v => !v.virtual);
    assert.ok(entries.length >= 7, 'view extraction from views.js found too few entries — did its shape change?');
    const primary = primaryNavViews(entries, { terminalsOn: true }).map(v => v.id);
    assert.deepEqual(primary, ['feed', 'terminals', 'work'],
        'the primary nav set drifted from the ticket\'s exact 3 domain objects');
});

// CMX-230 round 8: viewreg.js's own comment says primaryNavViews/secondaryNavViews
// share "the SAME... ENABLED/virtual filtering" as navViews() — but every fixture
// above runs with terminalsOn: true, so a mutation that drops JUST the isEnabled
// filter from primaryNavViews (keeping virtual + tier intact) stayed invisible: no
// test here ever renders a disabled entry through primaryNavViews. Mirrors the
// real 'terminals' entry (views.js: `enabled: ctx => !!ctx.terminalsOn`) — on a
// TERMINALS_ENABLED=false deployment this must vanish from the primary rail, not
// route to a #panel-terminals that was never rendered.
test('nav inventory: primaryNavViews drops a disabled view — the enabled filter, not just virtual/tier', () => {
    const entries = [{ id: 'terminals-like', tier: 'primary', enabled: ctx => !!ctx.terminalsOn }];
    assert.deepEqual(primaryNavViews(entries, { terminalsOn: false }).map(v => v.id), [],
        'primaryNavViews must drop a disabled view (enabled() false) — this is the ENABLED half of the shared navViews() filter');
    assert.deepEqual(secondaryNavViews(entries, { terminalsOn: false }).map(v => v.id), [],
        'a disabled view must not resurface in secondaryNavViews either — it is disabled, not demoted');
    assert.deepEqual(primaryNavViews(entries, { terminalsOn: true }).map(v => v.id), ['terminals-like'],
        'primaryNavViews must include the same view once its enabled() check passes');
});

// The 'disabled view must not resurface in secondaryNavViews either' assertion
// above is fed a tier: 'primary' fixture — a primary-tier entry can never
// appear in secondaryNavViews's output regardless of whether the shared
// enabled() filter runs, so that assertion can never fail and proves nothing
// about secondaryNavViews's own use of navViews(). This drives a tier:
// 'secondary' entry through secondaryNavViews directly, so a disabled
// secondary-tier view (the only shape that could actually resurface there)
// is the thing under test.
test('nav inventory: secondaryNavViews drops a disabled view — the enabled filter, not just tier', () => {
    const entries = [{ id: 'demoted-like', tier: 'secondary', enabled: ctx => !!ctx.terminalsOn }];
    assert.deepEqual(secondaryNavViews(entries, { terminalsOn: false }).map(v => v.id), [],
        'secondaryNavViews must drop a disabled secondary-tier view (enabled() false) — this is the ENABLED half of the shared navViews() filter');
    assert.deepEqual(secondaryNavViews(entries, { terminalsOn: true }).map(v => v.id), ['demoted-like'],
        'secondaryNavViews must include the same view once its enabled() check passes');

    // CMX-257 round 17: the VIRTUAL half of the same shared filter (judge
    // finding — round 16 above only closed the ENABLED half). `virtual: true`
    // means "reachable, but NOT a nav item" (viewreg.js's own comment) — that
    // must hold regardless of tier, so a virtual view tiered 'secondary' must
    // never surface as a #side-nav-more row. `enabled` is fixed `true` (not a
    // function of ctx) so this fixture can ONLY be reached by dropping the
    // virtual filter, not the enabled one.
    const virtualEntries = [{ id: 'virtual-secondary-like', tier: 'secondary', virtual: true, enabled: true }];
    assert.deepEqual(secondaryNavViews(virtualEntries, { terminalsOn: true }).map(v => v.id), [],
        'secondaryNavViews must drop a virtual secondary-tier view — this is the VIRTUAL half of the shared navViews() filter');

    // CMX-257 round 18: the mirror case for primaryNavViews. Every real VIEWS
    // entry GUARD 7 feeds primaryNavViews is pre-filtered with `.filter(v =>
    // !v.virtual)`, so dropping the virtual filter from primaryNavViews alone
    // passes every fixture above; today it's only caught, incidentally, by
    // sidebar.test.mjs's real render — because VIEWS happens to contain
    // exactly one virtual, untiered, enabled entry (agent-detail). That's a
    // coincidence of the registry, not a guaranteed catch. This synthetic
    // fixture makes it deliberate.
    const primaryVirtualEntries = [{ id: 'virtual-primary-like', tier: 'primary', virtual: true, enabled: true }];
    assert.deepEqual(primaryNavViews(primaryVirtualEntries, { terminalsOn: true }).map(v => v.id), [],
        'primaryNavViews must drop a virtual view — this is the VIRTUAL half of the shared navViews() filter');
});

test('nav inventory: Knowledge/Agents/Personas/Cost are demoted (secondary), not deleted', () => {
    const entries = shippedViewEntries().filter(v => !v.virtual);
    const secondary = secondaryNavViews(entries, { terminalsOn: true }).map(v => v.id);
    assert.deepEqual(secondary, ['knowledge', 'agents', 'personas', 'cost'],
        'the demoted set no longer matches the ticket\'s "attributes of things, not places to go" list');
});

test('nav inventory: demoting a view never removes it from the sidebar entirely — it still renders somewhere', () => {
    // primaryNavViews + secondaryNavViews must partition the SAME navViews() base
    // navViews() itself already filters (virtual, enabled) — every remaining view
    // lands in exactly one of the two groups; neither drops it.
    const entries = shippedViewEntries().filter(v => !v.virtual);
    const ctx = { terminalsOn: true };
    const all = [...primaryNavViews(entries, ctx), ...secondaryNavViews(entries, ctx)].map(v => v.id).sort();
    const expected = entries.map(v => v.id).sort();
    assert.deepEqual(all, expected, 'a non-virtual, enabled view fell out of BOTH the primary and secondary nav groups');
});

// viewreg.js's own comment states the must-never explicitly: "A view with no
// `tier` (or any value other than 'secondary') defaults to primary, so this is
// additive: forgetting to tier a new entry never silently hides it." Every
// SHIPPED entry happens to carry an explicit tier, so the test above (built
// from shippedViewEntries()) never exercises that default branch — a judge
// round flipped primaryNavViews's filter from `tier !== 'secondary'` to
// `tier === 'primary'` (byte-for-byte the failure the comment promises can't
// happen) and every shipped-entry test above stayed green because 'primary'
// was never actually asserted as the untiered default. This drives
// primaryNavViews/secondaryNavViews directly on a SYNTHETIC entry with no
// `tier` field at all, closing the untested branch.
test('nav inventory: a view with NO tier field defaults to primary, per viewreg.js\'s own must-never comment', () => {
    const ctx = { terminalsOn: true };
    const entries = [{ id: 'untiered-view' }];
    const primary = primaryNavViews(entries, ctx).map(v => v.id);
    const secondary = secondaryNavViews(entries, ctx).map(v => v.id);
    assert.deepEqual(primary, ['untiered-view'],
        'a view entry with no `tier` field must default into primaryNavViews — forgetting to tier a new entry must never silently hide it');
    assert.deepEqual(secondary, [],
        'an untiered view must NOT land in secondaryNavViews — only tier === \'secondary\' may demote it');
});

// --- CLAIM 3 (nav demotion), reduced to ONE structural fact — CMX-257 round
// 14 (human directive on PR #326, FINAL narrowing, superseding rounds 1-13):
// .side-subhead and the demoted group had drawn findings across TEN rounds —
// collapsed-rail hiding, text-present, opacity-exists, opacity-readable,
// position, renders-at-all, readable-heading, entity-blank, same-row-styling,
// and finally the round-13 human-authored enumeration itself turning out to
// have its own gap (a corrupted `color: transparent` satisfied every item on
// that list). Each round closed the SPECIFIC visual aspect named and the next
// round found another aspect of the SAME element — an unbounded surface, not
// a converging guard. The fix is not a longer enumeration, it's a shorter
// one: guard exactly the DOM fact views.js's own comment makes a claim
// about — "RE-PARENTING, NOT REMOVAL" — and move every other visual/
// positional aspect of .side-subhead and the demoted group (styling, weight,
// opacity, exact position, row-shape parity with the primary rail) to NOT
// GUARDED at the top of this file, with the manual greyscale capture at
// 1/2/4/6 densities as its acceptance check.
//
// Mounted against the REAL index.html nav section (not a hand-copied
// fixture — a hand fixture drifts from production silently, as the round-13
// non-blocking note found) with the REAL style.css cascade, so jsdom's own
// getComputedStyle decides what actually renders. Production ships
// #side-nav-more empty (renderNav populates it at runtime); one row shaped
// exactly as nav.js's _navItemHtml emits it (icon span + label span) is
// injected so this test can ask the one CSS-side question sidebar.test.mjs
// cannot: does real style.css hide what renderNav puts there. Whether
// renderNav puts the real Knowledge/Agents/Personas/Cost ids and label text
// there at all is already proven, with no stylesheet mounted, by
// sidebar.test.mjs's real-renderNav tests — that half is not re-proven here.
const NAV_SECTION_HTML = (() => {
    const html = src('templates/index.html');
    const start = html.indexOf('<section class="side-section">');
    const end = html.indexOf('</section>', start) + '</section>'.length;
    return html.slice(start, end);
})();

test('nav inventory (CLAIM 3): the demoted group still exists and renders under a heading — re-parenting, not removal', () => {
    const rowHtml = '<div class="side-item"><span class="side-item-icon"></span><span class="side-item-label">Knowledge</span></div>';
    const bodyHtml = `<div class="app"><aside class="sidebar">${NAV_SECTION_HTML}</aside></div>`
        .replace('id="side-nav-more"></div>', `id="side-nav-more">${rowHtml}</div>`);
    const win = mountWithRealCss(bodyHtml);

    const subhead = win.document.querySelector('.side-subhead');
    assert.ok(subhead, 'index.html no longer has a .side-subhead heading for the demoted nav group');
    assert.notEqual(subhead.textContent.trim(), '',
        'the .side-subhead renders with no visible text (its content trims to nothing, e.g. an &nbsp; entity) — ' +
        'the demoted nav group would render with no readable heading at all');
    const subheadCs = win.getComputedStyle(subhead);
    assert.notEqual(subheadCs.display, 'none', 'the .side-subhead heading has display: none — removed from the box tree entirely');
    assert.ok(!/^(hidden|collapse)$/.test(subheadCs.visibility),
        `the .side-subhead heading has visibility: ${subheadCs.visibility} — invisible but still occupying box-tree space`);

    const row = win.document.querySelector('#side-nav-more .side-item');
    assert.ok(row, 'index.html no longer has a #side-nav-more container for the demoted nav group to render into');

    // round 22 (judge finding 1 on PR #326): the per-node check below only
    // ever answered "does the ROW's own declaration say display: none" — it
    // stayed green when the judge hid an ANCESTOR instead (.side-list-
    // secondary, i.e. #side-nav-more itself), because an ancestor's
    // display: none does not touch a descendant's OWN computed value. The
    // chain walk is the actual guard; it answers "can the user see this row
    // at all", checking every ancestor up to the mount root, not just the
    // row's own declaration.
    assert.ok(_visibleInTree(win, row),
        'the demoted row is not visible in the tree — some ancestor between it and the mount root ' +
        '(the #side-nav-more container itself, .sidebar, or the nav section) has display: none or ' +
        'visibility: hidden/collapse, even though the row\'s OWN computed style looks fine — ' +
        '"RE-PARENTING, NOT REMOVAL" (views.js) means it must still render, not vanish');

    const rowCs = win.getComputedStyle(row);
    assert.notEqual(rowCs.display, 'none',
        'the demoted row has display: none — "RE-PARENTING, NOT REMOVAL" (views.js) means it must still render, not vanish');
    assert.ok(!/^(hidden|collapse)$/.test(rowCs.visibility),
        `the demoted row has visibility: ${rowCs.visibility} — invisible but still occupying box-tree space`);

    const label = row.querySelector('.side-item-label');
    assert.ok(label, 'the demoted row has no .side-item-label span at all — re-parenting has become removal of the accessibility cue');
    assert.notEqual(label.textContent.trim(), '',
        'the demoted row\'s label has no real text — re-parenting has become removal of the accessibility cue');
    const labelCs = win.getComputedStyle(label);
    assert.notEqual(labelCs.display, 'none',
        'the demoted row\'s label has display: none — re-parenting has become removal of the accessibility cue');
    assert.ok(!/^(hidden|collapse)$/.test(labelCs.visibility),
        `the demoted row's label has visibility: ${labelCs.visibility} — invisible but still occupying box-tree space`);

    // round 21 (judge finding 2 on PR #326): this test asserted display/visibility
    // on the ROW and the LABEL but never on the .side-item-icon span its own
    // fixture renders, so `.side-list-secondary .side-item-icon { display: none; }`
    // survived — the identical shape as the already-guarded `.side-item { display:
    // none; }` one child up, and round 14 kept exactly this `display: none` /
    // `visibility` pair guarded ("beyond 'not display:none, not visibility:hidden'"
    // is what moved to NOT GUARDED, not display:none itself).
    const icon = row.querySelector('.side-item-icon');
    assert.ok(icon, 'the demoted row has no .side-item-icon span at all — re-parenting has become removal of the accessibility cue');
    const iconCs = win.getComputedStyle(icon);
    assert.notEqual(iconCs.display, 'none',
        'the demoted row\'s icon has display: none — re-parenting has become removal of the accessibility cue');
    assert.ok(!/^(hidden|collapse)$/.test(iconCs.visibility),
        `the demoted row's icon has visibility: ${iconCs.visibility} — invisible but still occupying box-tree space`);
});

// --- CLAIM 3, round 21 (judge finding 2 on PR #326): the icon check above,
// repeated with the sidebar COLLAPSED. `body.sidebar-collapsed .side-item-label`
// is ALREADY display: none (style.css:3044) in that state, so the icon is the
// ONLY remaining cue a demoted row has — if `.side-list-secondary .side-item-icon`
// also goes display: none there, Knowledge/Agents/Personas/Cost render as four
// blank clickable strips with no cue of any kind. This must redden on its own,
// independent of the expanded-state assertion above.
test('nav inventory (CLAIM 3): the demoted row\'s icon survives the COLLAPSED rail, where the label cannot compensate', () => {
    const rowHtml = '<div class="side-item"><span class="side-item-icon"></span><span class="side-item-label">Knowledge</span></div>';
    const bodyHtml = `<div class="app"><aside class="sidebar">${NAV_SECTION_HTML}</aside></div>`
        .replace('id="side-nav-more"></div>', `id="side-nav-more">${rowHtml}</div>`);
    const win = mountWithRealCss(bodyHtml, ' class="sidebar-collapsed"');

    // round 22 (judge finding 2 on PR #326): this test used to check only the
    // ICON's own display, never the ROW containing it — so hiding the
    // demoted ROWS in the collapsed rail (`body.sidebar-collapsed
    // .side-list-secondary .side-item { display: none; }`) left the icon's
    // own computed display at 'inline' and this test green, while all four
    // demoted views vanished from the 48px rail completely. The chain walk
    // from the row up catches that, the row's own display: none, AND any
    // ancestor (#side-nav-more/.side-list-secondary, .sidebar, the nav
    // section) hidden in this state — same guard as the expanded test above.
    const row = win.document.querySelector('#side-nav-more .side-item');
    assert.ok(row, 'the demoted row does not exist in the collapsed rail');
    assert.ok(_visibleInTree(win, row),
        'the demoted row is not visible in the collapsed rail — either the row itself or some ancestor ' +
        '(#side-nav-more/.side-list-secondary, .sidebar, the nav section) has display: none or ' +
        'visibility: hidden/collapse — with the label already hidden in this state, the row would vanish ' +
        'from the rail completely, not degrade to a blank clickable strip');

    const icon = row.querySelector('.side-item-icon');
    assert.ok(icon, 'the demoted row has no .side-item-icon span at all in the collapsed rail');
    const iconCs = win.getComputedStyle(icon);
    assert.notEqual(iconCs.display, 'none',
        'the demoted row\'s icon has display: none while collapsed — with the label already hidden in this state, ' +
        'the row becomes a blank clickable strip with no cue of any kind');
    assert.ok(!/^(hidden|collapse)$/.test(iconCs.visibility),
        `the demoted row's icon has visibility: ${iconCs.visibility} while collapsed — invisible but still occupying box-tree space`);
});

// --- CLAIM 3, round 20 (human directive on PR #326, judge finding 2): the
// active-row cue must actually PAINT on a demoted row, not just carry the
// class. Round 19 closed the JS half (_syncSidebarActive's sweep, guarded in
// tests/sidebar.test.mjs by reading `.active` off the rendered node with NO
// stylesheet mounted) — this is the CSS half of the same claim. The three
// rules that turn `.active` into a visible cue (accent fill, the accent rail
// ::before, the accent label colour) were all unscoped before this PR, which
// was a no-op while every nav row lived in #side-nav; scoping any of them to
// `#side-nav` after the re-parenting is a silent regression — selecting
// Knowledge/Agents/Personas/Cost would leave the sidebar with an active CLASS
// but no lit row, degrading to a hue-only cue on the icon tint alone (the
// exact a11y regression this ticket exists to prevent). Mounted against the
// REAL index.html nav section + REAL style.css, with one `.active` row shaped
// exactly as nav.js's _navItemHtml emits it in EACH host (#side-nav-more and,
// as a live control, #side-nav) plus one inactive row as a second control —
// three checks account for the whole diff a single "scope to #side-nav"
// mutation makes in one hunk (background, ::before rail, label colour), so
// catching any one of them reddens that mutation.
test('CLAIM 3: the active-row cue actually PAINTS on a demoted row, not just the class', () => {
    const demotedActive = '<div class="side-item active" data-view="agents"><span class="side-item-icon"></span><span class="side-item-label">Agents</span></div>';
    const primaryActive = '<div class="side-item active" data-view="work"><span class="side-item-icon"></span><span class="side-item-label">Work</span></div>';
    const primaryInactive = '<div class="side-item" data-view="feed"><span class="side-item-icon"></span><span class="side-item-label">Feed</span></div>';
    const bodyHtml = `<div class="app"><aside class="sidebar">${NAV_SECTION_HTML}</aside></div>`
        .replace('id="side-nav-more"></div>', `id="side-nav-more">${demotedActive}</div>`)
        .replace('id="side-nav"></div>', `id="side-nav">${primaryActive}${primaryInactive}</div>`);
    const win = mountWithRealCss(bodyHtml);

    const demoted = win.document.querySelector('#side-nav-more .side-item.active');
    const primary = win.document.querySelector('#side-nav .side-item.active');
    const inactive = win.document.querySelector('#side-nav .side-item:not(.active)');
    assert.ok(demoted && primary && inactive, 'the fixture is missing one of its three rows');

    // 1. the accent FILL: an active demoted row must resolve the same background
    // as an active primary row, and neither may match a merely-present row's.
    const demotedBg = win.getComputedStyle(demoted).background;
    const primaryBg = win.getComputedStyle(primary).background;
    const inactiveBg = win.getComputedStyle(inactive).background;
    assert.notEqual(demotedBg, inactiveBg,
        'the demoted active row has no accent fill distinguishing it from an unselected row — scoping ' +
        '`.side-item.active` to #side-nav leaves every demoted selection dark');
    assert.equal(demotedBg, primaryBg,
        "the demoted active row's accent fill does not match the primary active row's — the active-fill rule " +
        'no longer reaches #side-nav-more');

    // 2. the accent RAIL (::before): jsdom cannot compute a pseudo-element's
    // style (see _activeRailReaches above), so this reads the cascade directly —
    // does the ::before rule's base selector still match the demoted row.
    assert.ok(_activeRailReaches(win, demoted),
        "the demoted active row's accent rail (::before) selector no longer matches it — scoping " +
        '`.side-item.active::before` to #side-nav removes the rail from every demoted selection');
    assert.ok(_activeRailReaches(win, primary),
        'sanity: the primary active row should still carry the accent rail (control)');

    // 3. the accent LABEL colour: same shape as the fill check, on the label span.
    const demotedLabelColor = win.getComputedStyle(demoted.querySelector('.side-item-label')).color;
    const primaryLabelColor = win.getComputedStyle(primary.querySelector('.side-item-label')).color;
    const inactiveLabelColor = win.getComputedStyle(inactive.querySelector('.side-item-label')).color;
    assert.notEqual(demotedLabelColor, inactiveLabelColor,
        "the demoted active row's label is not accent-coloured — its active state degrades to a hue-only icon " +
        'tint, the exact cue this ticket\'s a11y claim says must never happen');
    assert.equal(demotedLabelColor, primaryLabelColor,
        "the demoted active row's label colour does not match the primary active row's — the active-label rule " +
        'no longer reaches #side-nav-more');
});

// --- CMX-257 round 10 (human directive on PR #326, superseding round 8/9):
// this file used to carry a hand-rolled cascade/specificity resolver
// (resolveAllForContext/selectorSpecificity, ~150 lines) to assert
// ".side-list-secondary actually renders lighter than the primary row" —
// finding 2 across rounds 1/8/10, always the same shape: a higher-specificity
// override written under a differently-spelled selector wins the real cascade
// but is invisible to a lookup keyed on selector text. A font-weight/size
// COMPARISON like this is a CSS-VALUE assertion the way findings 1 and 3 are,
// and jsdom cannot resolve it honestly either (round 9's own mount-based
// technique answers "which declaration wins", not "what does it look like
// relative to its sibling"). Moved to the NOT GUARDED block at the top of
// this file; the acceptance check is the manual greyscale capture, not
// another resolver. The WIRING test below (that nav.js emits the class names
// this now-removed guard depended on) stays — it is markup/source-text, not
// a CSS-value comparison, and it is what keeps the greyscale capture's
// premise ("this markup even has a side-list-secondary row to look at")
// honest.

// --- WIRING (CMX-257 round 2): every one of the checks the removed
// ".side-list-secondary actually renders lighter" guard made hung off the
// CLASS NAMES the CSS selectors name, never off the markup that actually
// has to emit them. A judge round renamed the label span nav.js's
// _navItemHtml emits from `side-item-label` to `side-item-name` (leaving
// style.css's `.side-list-secondary .side-item-label` selector, and every
// other guard in this file, untouched) — every demoted row's label span
// stops matching that selector (and the primary row's own `.side-item`
// font-size fallback, since it no longer carries a recognised label class
// either), so the "renders lighter" contract silently stops applying to any
// real DOM, while a resolver-based test would keep comparing two CSS rules
// that no longer style anything a browser renders. Pin the two class names
// style.css's demotion rule depends on directly against the markup that has
// to emit them.
//
// --- WIRING (CMX-257 round 15, judge finding 1 on PR #326): pinning
// nav.js's two item-level classes is only half the selector.
// `.side-list-secondary .side-item-icon`/`.side-item-label` also needs
// index.html's #side-nav-more CONTAINER to actually carry
// `side-list-secondary` — nothing above asserted that (sidebar.test.mjs's
// fixture hard-codes `class="side-list"` without it, and CLAIM 3 above
// mounts the real markup but only reads display/visibility/text, never the
// container's class attribute). Dropping `side-list-secondary` from
// #side-nav-more silently un-demotes the whole group — identical in kind to
// the round-2 side-item-label rename this file already guards against, just
// from the container end of the selector instead of the item end.
test('WIRING: index.html\'s #side-nav-more container carries the side-list-secondary class style.css\'s demotion rule depends on', () => {
    const html = src('templates/index.html');
    const start = html.indexOf('id="side-nav-more"');
    assert.notEqual(start, -1, 'index.html no longer has a #side-nav-more container for the demoted nav group');
    const tagStart = html.lastIndexOf('<div', start);
    const tagEnd = html.indexOf('>', start);
    const openTag = html.slice(tagStart, tagEnd + 1);
    assert.match(openTag, /class="[^"]*\bside-list-secondary\b[^"]*"/,
        '#side-nav-more no longer carries class="side-list-secondary" — style.css\'s ' +
        '`.side-list-secondary .side-item-icon`/`.side-item-label` demotion rule would no longer match this ' +
        'container at all, silently un-demoting the whole group back to full prominence');
});

test('WIRING: nav.js emits the exact .side-item-icon / .side-item-label classes style.css\'s demotion rule depends on', () => {
    const fn = NAV.slice(NAV.indexOf('function _navItemHtml'));
    const body = fn.slice(0, fn.indexOf('\nfunction ', 10));
    assert.match(body, /class="side-item-icon"/,
        '_navItemHtml no longer emits class="side-item-icon" — style.css\'s ' +
        '`.side-list-secondary .side-item-icon` demotion rule (and the primary .side-item-icon rule it is compared ' +
        'against above) would no longer match any rendered nav row');
    assert.match(body, /class="side-item-label"/,
        '_navItemHtml no longer emits class="side-item-label" — style.css\'s ' +
        '`.side-list-secondary .side-item-label` demotion rule (and the primary .side-item font-size it is compared ' +
        'against above) would no longer match any rendered nav row, so the "renders lighter" guard above would be ' +
        'comparing two CSS rules that style nothing real');
});
