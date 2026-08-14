// CMX-230 — "the dashboard reads as a dev tool because of TYPE SCALE, NAV COUNT
// and HUE COUNT" (Liav's Xirp comparison, 2026-08-11). This is the ticket's own
// GUARDS list, each written to go RED under the specific corruption it names —
// same discipline as tests/kanban_lane_model.test.mjs: assert a precise value
// against the REAL source/module, not "some rule exists".
//
// CMX-279 SUPERSESSION (2026-08-13, measured not assumed — asked which of the
// seven views he actually opens, Liav named exactly two): claim 3 below ("nav
// inventory") assumed CMX-230's call that the four demoted views stay reachable,
// re-parented under a quieter "More" group rather than deleted. CMX-279 reverses
// that call outright — Feed, Knowledge, Agents, Personas and Cost are DELETED,
// not demoted, along with the `tier` field, viewreg.js's primaryNavViews/
// secondaryNavViews split, and index.html's #side-nav-more/.side-subhead group
// that rendered them. Every test below that asserted the demoted group RENDERS
// (GUARD 7 / "CLAIM 3" and their sidebar.test.mjs companions) tested a feature
// that no longer exists and is replaced by a single, much smaller "nav inventory"
// guard further down. Claims 1/2/4 (type scale, airy density, non-hue cue) are
// untouched by this — see their own sections below.
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
//   3. nav inventory — CMX-279 (below) supersedes this claim as originally
//      written: the ticket's own "demoted, not deleted" call is reversed, so
//      what's guarded now is that the shipped nav is exactly Wall and Work
//      (see "nav inventory" below).
//   4. non-hue cue — every real status family carries a glyph/word, not just
//      colour; this is the accessibility hard requirement and the one claim
//      here that must never regress (see "GUARD 3" below).
// A handful of adjacent, already-converged guards (GUARD 4/5/6) are kept
// as-is: they weren't the source of the recurring findings and already
// assert a resolved value or a real function's behaviour, not a
// property-presence scan. GUARD 7's own .side-list-secondary weight/wiring
// pair is gone with the group it styled — see the CMX-279 note above.
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
// (ii) — CMX-279: MOOT, not just removed. The demoted nav group
// (.side-list-secondary) this item described no longer exists — CMX-279
// deleted the five views it held rather than keeping CMX-230's demotion, so
// there is no group left to render lighter than the primary rail, and the
// WIRING test this item pointed at (nav.js emitting the classes the demotion
// rule depended on) is deleted along with it;
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
// — round 14, FINAL narrowing of CLAIM 3; CMX-279 (below) supersedes this
// entirely, kept for the historical record only —
// (v) — CMX-279: MOOT. .side-subhead and the demoted group it styled are
// deleted, not just unguarded — there is no heading, no #side-nav-more, and
// no "RE-PARENTING, NOT REMOVAL" claim left to hold (views.js's own comment
// now says the opposite: the five views ARE deleted). CLAIM 3's replacement,
// "nav inventory" below, guards the new shape directly: the shipped nav is
// exactly Wall and Work.
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
import { navViews } from '../chela/dashboard/static/js/viewreg.js';

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

// --- GUARD 7: nav inventory — CMX-279 (measured, not assumed: asked which of
// the seven views he actually opens, Liav named exactly two) supersedes
// CMX-230's "demoted, not deleted" call from here on. Feed, Knowledge, Agents,
// Personas and Cost are gone — not re-parented into a quieter group — along
// with the `tier` field, viewreg.js's primaryNavViews/secondaryNavViews split,
// and index.html's #side-nav-more/.side-subhead markup that rendered them.
// This replaces the old ~400-line GUARD 7 / "CLAIM 3" section (nav-inventory
// pin + the #side-nav-more WIRING tests) wholesale: there is no longer a
// second nav group, a demotion, or a re-parenting claim to guard piece by
// piece — the whole shape collapses to one fact, asserted directly against
// the REAL views.js source and the REAL rendered sidebar.
function shippedViewIds() {
    const body = VIEWS_SRC.split('export const VIEWS')[1];
    const ids = [...body.matchAll(/^\s+id:\s*'([^']+)'/gm)];
    return ids.map((m, i) => {
        const start = m.index;
        const end = i + 1 < ids.length ? ids[i + 1].index : body.length;
        const block = body.slice(start, end);
        return { id: m[1], virtual: /virtual:\s*true/.test(block) };
    });
}

test('nav inventory: the shipped nav is exactly Wall and Work — the other five views are DELETED, not demoted', () => {
    const entries = shippedViewIds().filter(v => !v.virtual);
    const shipped = navViews(entries, { terminalsOn: true }).map(v => v.id);
    assert.deepEqual(shipped, ['terminals', 'work'],
        'the shipped nav drifted from CMX-279\'s exact 2 views — if this includes feed/knowledge/agents/personas/cost, ' +
        'one of the five deleted views came back; if it is missing terminals or work, one of the two kept views broke');
});

test('nav inventory: the five deleted views leave no trace — no panel, no tier field, no secondary nav group', () => {
    const html = src('templates/index.html');
    for (const id of ['feed', 'knowledge', 'agents', 'personas', 'cost']) {
        assert.ok(!html.includes(`id="panel-${id}"`),
            `index.html still has a panel-${id} div — CMX-279 deleted this view's panel along with its nav entry`);
    }
    assert.ok(!VIEWS_SRC.includes('tier:'),
        'views.js still carries a `tier` field — CMX-279 removed the primary/secondary split along with the ' +
        'five demoted-then-deleted views (nothing left needs a second nav group)');
    assert.ok(!html.includes('id="side-nav-more"'),
        'index.html still has a #side-nav-more container — CMX-279 removed the secondary nav group entirely, ' +
        'it did not just empty it');
});

// WIRING: the REAL renderNav() renders exactly Wall and Work into the REAL
// #side-nav — not just "the registry says two ids" (the source-level test
// above, by design, never touches the DOM). A judge round could leave
// #side-nav-more in nav.js's renderNav (harmless once the container is gone
// from index.html, `if (more)` guards it — but a silent no-op is still worth
// a green test naming it) or scope renderNav to drop an id silently; this
// drives nav.js's own renderNav against a minimal real sidebar fixture and
// reads the rendered rows back.
test('WIRING: the REAL renderNav() renders exactly Wall and Work into #side-nav, in order', async () => {
    const BODY = '<div class="app"><aside class="sidebar"><section class="side-section">' +
        '<div class="side-list" id="side-nav"></div></section></aside></div>';
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
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
    const nav = await import('../chela/dashboard/static/js/nav.js');

    nav.renderNav();
    const ids = [...document.querySelectorAll('#side-nav .side-item')].map(el => el.dataset.view);
    assert.deepEqual(ids, ['terminals', 'work'],
        'the REAL rendered #side-nav no longer matches Wall·Work — either a deleted view resurfaced or one of ' +
        'the two kept views failed to render');
});
