// THE ORCHESTRATOR + DISPATCHED ROLE BADGES' CSS CONTRACT (CMX-302, PR #376) — pure
// cascade properties tests/sidebar.test.mjs cannot see, because bootDashboardDom never
// loads style.css into its jsdom (0 <style>/<link> in that helper — it boots the module
// graph only). Each mutation below was applied live by the judge, in isolation, and the
// whole suite (3200+ tests) stayed green:
//   1. `.ar-role.orchestrator { display: none }` — the badge's own visible text was
//      removed by CMX-302 (the crown icon + title/aria-label replaced it), so CMX-300's
//      colourblind-safe shape cue now lives ENTIRELY in this block rendering at all.
//      sidebar.test.mjs's `assert.ok(badge, ...)` only proves the <span> exists in the
//      DOM tree — jsdom builds DOM nodes for `display:none` elements exactly like any
//      other, so that assertion cannot see a collapsed rule.
//   2. `.ar-role.orchestrator { width: 180px }` — CMX-302's stated objective is that the
//      badge stop being wide enough to truncate the session name next to it (the old
//      "Orchestrator" text pill was the reported bug).
//   3. round 6 (judge, PR #376): `.ar-role.orchestrator { visibility: hidden }` and
//      `{ opacity: 0 }`, found separately — display's direct siblings on the CSS "off
//      switch" spectrum (docs/defeat_shapes/67). Neither touches `display`, `width`,
//      `padding`, `min-width` or `max-width`, so every assertion this file had at the
//      time stayed green while the badge painted nothing.
//
// This runs the REAL style.css through the REAL dashboard boot (bootDashboardDom, the
// same real orchestratorSubscribe() round trip tests/sidebar.test.mjs drives), then
// injects style.css into that same jsdom document afterward and reads the CASCADED
// value with getComputedStyle on the badges nav.js actually rendered — not a hand-typed
// fixture that could drift from _agentRowHtml's real class names. Same recipe as
// tests/decisions_modal_css.test.mjs / tests/gs_files_pointer_events_css.test.mjs.
//
// CMX-302 rework round 7/8 — CLOSE THE CASCADE SET, THEN STOP ENUMERATING (human
// directive on PR #376, 2026-08-17, superseding round 6's per-property framing): round 6
// found `visibility`/`opacity`/`margin` the same way rounds 2/4/5 found `display`/
// `padding`/`min-width`/`max-width` — one property at a time, with no bottom in
// PRINCIPLE (this repo already settled that in CMX-273, see
// tests/dashboard_scale_nav_a11y.test.mjs:162 and docs/SPIKE_WALL_FILLS_STAGE.md, and
// CMX-298 closed the identical class the same way the same day — see
// tests/kanban_flatten.test.mjs's own "NOT GUARDED" section). The dividing line
// CMX-273/CMX-298 both settled on is CASCADE vs LAYOUT — a DECLARED value jsdom's CSSOM
// resolves truthfully (whatever the property) vs a RESOLVED/composited geometry value
// that needs an actual layout engine, which jsdom has none of:
//   - `display`/`visibility`/`opacity` are CASCADE. Closed below as one assertion group
//     per property (the trio CMX-298 settled on for `.kanban-card-parked`), not
//     re-narrowed to a fourth/fifth name.
//   - round 6's third finding, `.ar-role.orchestrator { margin: 0 80px }`, was FIRST
//     assumed to belong in the same LAYOUT bucket as `transform`/`clip-path`/off-screen
//     positioning (margin sits outside the border box, so it "sounds like" a layout
//     property) — that assumption was wrong, and was corrected by actually testing it
//     rather than reasoning from the property's name: `getComputedStyle(el).marginLeft`
//     resolves the literal declared length exactly the same way `width`/`padding`/
//     `min-width`/`max-width` already do (verified directly against this repo's real
//     style.css — see the round 6/8 test below). `margin` IS CASCADE, closed below,
//     same as the others.
//   - The genuine LAYOUT bucket — the thing jsdom truly cannot resolve, verified the
//     same way — is RESOLVED GEOMETRY: `getBoundingClientRect`/`offsetWidth` are always
//     zero in jsdom, and percentage/`vw`/flex-distributed widths never resolve against a
//     real parent (the CMX-273/CMX-298 empirical finding, unchanged). "Is the badge
//     actually N pixels wide, at this position, on screen" as an OUTCOME is in that
//     bucket and stays NOT GUARDED here — verified BY CAPTURE instead, at the bottom of
//     this file.
//
// NOTE: jsdom performs no layout, so getComputedStyle below resolves the CASCADE (which
// declaration wins) — not the real rendered BOX. A property this file never reads (e.g. a
// wider `.ar-role` base-rule flex-grow, or an overflowing child) could still widen the
// real box without changing any declaration read here. Read the declarations most likely
// to move (width/padding/min-width/max-width/margin on this exact rule), not layout itself.
//
// Run: node --test tests/sidebar_role_badge_css.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery; needs `npm ci` for jsdom).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it
import { bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const styleCss = readFileSync(join(ROOT, 'static', 'style.css'), 'utf8');

const BODY = `
<div class="app">
  <aside class="sidebar">
    <section class="side-section">
      <span class="side-count" id="hdr-agents">-/-</span>
      <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
    </section>
  </aside>
</div>`;

const agent = (name, over = {}) => ({
    name, window_id: '@1', online: true, session_status: 'idle', ...over,
});

let dom, nav, util, orchestrator, badge, dispatchedBadge;

before(async () => {
    ({ dom, modules: { nav, util, orchestrator } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        // Same real subscribe round trip as tests/sidebar.test.mjs — the badge only
        // exists once onOrchestratorChange redraws the row off a real /api/orchestrator/
        // subscribe response.
        fetchImpl: (url, opts) => {
            const u = String(url);
            if (u.includes('/api/orchestrator/subscribe')) {
                const body = opts && opts.body ? JSON.parse(opts.body) : {};
                return Promise.resolve({
                    ok: true, status: 200,
                    json: () => Promise.resolve({
                        ok: true, wid: body.wid, name: `${body.wid}-tmux-name`, state: 'registered', why: '', queued: 0,
                    }),
                });
            }
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        },
        extraModules: ['util.js', 'nav.js', 'orchestrator.js'],
    }));
    // main.js fires an unawaited initial refresh() on load, which would otherwise
    // overwrite the manual renderSidebarAgents() call below once its stubbed fetch
    // settles (DEFEAT_SHAPES: a race window, not a code bug — same fix as
    // tests/dashboard_default_view.test.mjs / tests/sidebar_agent_detail_orchestrator_wiring.test.mjs).
    await flush();
    await flush();

    // bootDashboardDom builds no <style> at all — inject the REAL style.css into the
    // SAME document the sidebar was rendered into, after the fact, exactly like a
    // browser's cascade applies regardless of DOM-vs-CSS load order.
    const styleEl = dom.window.document.createElement('style');
    styleEl.textContent = styleCss;
    dom.window.document.head.appendChild(styleEl);

    // CMX-302 rework round 7, item 2: a dispatched row alongside the orchestrator one,
    // so this file can apply the SAME cascade guards to the bot badge, not just the
    // crown — "do not guard one badge and leave the other" (Liav, 2026-08-17), the
    // one-of-N shape this ticket has already been bitten by twice.
    const rows = [
        agent('orch', { window_id: '@1' }),
        agent('worker', { window_id: '@2', dispatched: true }),
    ];
    // onOrchestratorChange's listener (nav.js) redraws off `_agentsCache`, not the
    // rows array passed to renderSidebarAgents — real callers reach it through
    // util.setAgentsCache too (see tests/sidebar.test.mjs's identical setup).
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
    await orchestrator.orchestratorSubscribe('@1');
    badge = dom.window.document.querySelector('#sidebar-agents .agent-row[data-agent="orch"] .ar-role');
    assert.ok(badge, 'sanity: the orchestrator role badge did not render at all');
    dispatchedBadge = dom.window.document.querySelector('#sidebar-agents .agent-row[data-agent="worker"] .ar-role');
    assert.ok(dispatchedBadge, 'sanity: the dispatched role badge did not render at all');
});

test('🔴 GUARD: the orchestrator role badge is actually VISIBLE — .ar-role.orchestrator renders display:flex under the REAL stylesheet', () => {
    assert.equal(dom.window.getComputedStyle(badge).display, 'flex',
        '.ar-role.orchestrator must render display:flex. CMX-300\'s colourblind-safe shape ' +
        'cue now lives entirely in this element (its own text was removed by CMX-302 in favour ' +
        'of a crown icon + title tooltip) — display:none collapses the whole cue to nothing ' +
        'while every DOM-only assertion (element exists, has the right class) stays green');
});

test('🔴 GUARD: the orchestrator role badge stays ICON-NARROW — .ar-role.orchestrator renders width:18px under the REAL stylesheet', () => {
    assert.equal(dom.window.getComputedStyle(badge).width, '18px',
        '.ar-role.orchestrator must render width:18px. CMX-302\'s stated objective is that the ' +
        'badge stop being wide enough to truncate the session name next to it — a wider computed ' +
        'width reproduces the exact bug this ticket fixed, and no DOM-only assertion can see it');
});

test('🔴 GUARD (CMX-302 rework round 4): the badge stays ICON-NARROW through padding too — .ar-role.orchestrator renders padding:0 under the REAL stylesheet', () => {
    // `width` is only one half of the box. box-sizing:border-box clamps CONTENT to the
    // declared width but not padding, so `padding: 0 80px` regrows the exact same
    // box — a badge wide enough to truncate the session name beside it, this ticket's
    // reported bug — while getComputedStyle(badge).width above still reads 18px.
    const style = dom.window.getComputedStyle(badge);
    assert.equal(style.paddingLeft, '0px',
        '.ar-role.orchestrator must render zero left padding — nonzero padding regrows the ' +
        'badge\'s box exactly like the wide `width` this ticket fixed, invisible to a width-only check');
    assert.equal(style.paddingRight, '0px',
        '.ar-role.orchestrator must render zero right padding — see the left-padding assertion above');
});

test('🔴 GUARD (CMX-302 rework round 5): the badge stays ICON-NARROW through min-width/max-width too — the properties that OVERRIDE `width` outright', () => {
    // `width`, `padding`, `min-width` and `max-width` are four independent ways to
    // regrow the exact same box, and this file previously read only the first two.
    // Unlike padding, `min-width` beats a `width` declaration outright under real
    // layout (and `max-width` would clamp a width raised the same way from the other
    // rule in the cascade) — `min-width: 180px` reproduces the reported bug (a badge
    // wide enough to truncate the session name beside it) in its purest form, while
    // getComputedStyle(badge).width above keeps reporting the untouched declared
    // value, because `width` and `min-width` are separate CSS properties that jsdom's
    // cascade resolver reads back independently.
    const style = dom.window.getComputedStyle(badge);
    assert.equal(style.minWidth, '18px',
        '.ar-role.orchestrator must render min-width:18px — a wider min-width regrows the ' +
        'badge\'s real-layout box exactly like the reported bug, invisible to a width-only check');
    assert.equal(style.maxWidth, '18px',
        '.ar-role.orchestrator must render max-width:18px — the sibling clamp that keeps the ' +
        'box from growing via any other widened declaration in the same family');
});

test('🔴 GUARD (CMX-302 rework round 6/8): the badge stays ICON-NARROW through margin too — the one box property OUTSIDE the border box', () => {
    // round 6 (judge, PR #376): `margin: 0 80px` on `.ar-role.orchestrator` regrows the
    // badge's real box by 160px — the exact reported bug — while `width`/`padding`/
    // `min-width`/`max-width` all read exactly as asserted above, because `margin` sits
    // outside the border box and none of those properties account for it.
    //
    // This was first proposed as belonging to the un-observable LAYOUT tail alongside
    // `transform`/`clip-path`/off-screen positioning (see this file's "NOT GUARDED"
    // section) — CORRECTED after actually testing it, not assuming: jsdom's
    // `getComputedStyle` resolves `margin-left`/`margin-right` the exact same way it
    // resolves `width`/`padding`/`min-width`/`max-width` above — as the winning
    // CASCADE declaration for a literal, non-percentage length, with no layout required
    // to know it. Verified directly against this rule (`node -e` against a real jsdom +
    // this repo's real style.css): `margin: 0 80px` reads back as
    // `marginLeft`/`marginRight` === `'80px'`. It is NOT in the same bucket as
    // `getBoundingClientRect`-only geometry (always zero in jsdom) or a percentage/`vw`
    // value (never resolves against a real parent) — see the "NOT GUARDED" section for
    // what actually is.
    const style = dom.window.getComputedStyle(badge);
    assert.equal(style.marginLeft, '0px',
        '.ar-role.orchestrator must render zero left margin — nonzero margin regrows the ' +
        'badge\'s box from OUTSIDE the border box, invisible to every width/padding/min-width/' +
        'max-width check above (none of them account for margin)');
    assert.equal(style.marginRight, '0px',
        '.ar-role.orchestrator must render zero right margin — see the left-margin assertion above');
});

test('🔴 GUARD (CMX-302 rework round 7): the orchestrator badge is not COLLAPSED via visibility or opacity — the two remaining cascade "off switches" display has as siblings', () => {
    // round 6 (judge, PR #376) found `visibility: hidden` and `opacity: 0` independently
    // — neither touches `display`, `width`, `padding`, `min-width` or `max-width`, so
    // every assertion above stayed green while the badge painted nothing. This closes
    // the CASCADE set (display/visibility/opacity) as ONE assertion group, per round 7's
    // human directive reusing the CMX-298 `.kanban-card-parked` precedent — see the file
    // header for the CASCADE-vs-LAYOUT split this reuses (and the round 6/8 test below
    // for `margin`, initially assumed LAYOUT and corrected after actually testing it).
    const style = dom.window.getComputedStyle(badge);
    assert.notEqual(style.visibility, 'hidden',
        '.ar-role.orchestrator must not render visibility:hidden — this erases the badge ' +
        'entirely while display still cascades to "flex" and every DOM-only assertion ' +
        '(element exists, has the crown\'s exact path data, right class) stays green');
    assert.notEqual(style.opacity, '0',
        '.ar-role.orchestrator must not render opacity:0 — this paints the badge fully ' +
        'transparent while display/visibility/width/padding/min-width/max-width and every ' +
        'SVG attribute all read exactly as asserted');
});

// --- The DISPATCHED badge gets the SAME guard set as the orchestrator badge above ----
//
// CMX-302 rework round 7, item 2 (Liav, approved 2026-08-17): the dispatched badge is
// now a bare BOT icon in the same 18px icon-narrow box as the crown (previously a
// "Dispatched" text pill wide enough to truncate the session name next to it — the same
// bug the crown fixed). Every guard the crown badge earned across six rounds — present-
// but-empty icon (tests/sidebar.test.mjs), accessible name, and this file's cascade/box
// set — applies here too, in one pass rather than six, so this badge does not repeat the
// crown's own one-property-per-round history.
test('🔴 GUARD: the dispatched role badge is actually VISIBLE — .ar-role.dispatched renders display:flex, not hidden via visibility or opacity, under the REAL stylesheet', () => {
    const style = dom.window.getComputedStyle(dispatchedBadge);
    assert.equal(style.display, 'flex',
        '.ar-role.dispatched must render display:flex — display:none collapses the badge to ' +
        'nothing while every DOM-only assertion (element exists, has the right class) stays green');
    assert.notEqual(style.visibility, 'hidden',
        '.ar-role.dispatched must not render visibility:hidden — see the orchestrator badge\'s ' +
        'identical assertion above for why this is a separate, independently-defeatable axis');
    assert.notEqual(style.opacity, '0',
        '.ar-role.dispatched must not render opacity:0 — see the orchestrator badge\'s identical ' +
        'assertion above');
});

test('🔴 GUARD: the dispatched role badge stays ICON-NARROW — .ar-role.dispatched renders width/padding/min-width/max-width/margin:18px/0 under the REAL stylesheet', () => {
    // Mirrors the orchestrator badge's width/padding/min-width/max-width/margin guards
    // above — five independent ways the same "wide badge truncates the session name" bug
    // could reappear on THIS badge, which is a separate CSS rule (`.ar-role.dispatched`,
    // not `.ar-role.orchestrator`) and was not covered by any of those tests.
    const style = dom.window.getComputedStyle(dispatchedBadge);
    assert.equal(style.width, '18px',
        '.ar-role.dispatched must render width:18px — a wider computed width reproduces the ' +
        'exact bug this ticket fixed');
    assert.equal(style.paddingLeft, '0px',
        '.ar-role.dispatched must render zero left padding — nonzero padding regrows the ' +
        'badge\'s box exactly like a wide `width` would');
    assert.equal(style.paddingRight, '0px',
        '.ar-role.dispatched must render zero right padding — see the left-padding assertion above');
    assert.equal(style.minWidth, '18px',
        '.ar-role.dispatched must render min-width:18px — min-width overrides `width` outright ' +
        'under real layout, reproducing the reported bug in its purest form');
    assert.equal(style.maxWidth, '18px',
        '.ar-role.dispatched must render max-width:18px — the sibling clamp that keeps the box ' +
        'from growing via any other widened declaration in the same family');
    assert.equal(style.marginLeft, '0px',
        '.ar-role.dispatched must render zero left margin — nonzero margin regrows the badge\'s ' +
        'box from OUTSIDE the border box, invisible to every width/padding/min-width/max-width check');
    assert.equal(style.marginRight, '0px',
        '.ar-role.dispatched must render zero right margin — see the left-margin assertion above');
});

// --- NOT GUARDED: "the badge occupies N screen pixels, at this position" as an OUTCOME -
//
// The assertions above are cheap tripwires against every CSS collapse actually proposed
// against these two rules so far, cascade or box-model alike: display/visibility/opacity,
// and the width/padding/min-width/max-width/margin family. That family grew past its
// original width/padding pair three times (min/max-width, then visibility/opacity, then
// margin) — the same "one more spelling" shape CMX-273/CMX-298 already hit — but unlike
// those two spikes, every one of THIS rule's box-model properties turned out to be
// individually testable: jsdom's `getComputedStyle` resolves ANY property's winning
// CASCADE declaration (a literal, non-percentage value) without needing real layout,
// which is why `margin` closed above instead of joining this section — see the file
// header for the corrected CASCADE-vs-LAYOUT boundary and how it was verified rather
// than assumed.
//
// What is NOT guarded, and genuinely can't be by any property-level assertion in this
// harness, is the OUTCOME those declarations are supposed to add up to: does the badge
// actually occupy an 18x18px box, at the position the row's real flex layout puts it, on
// a real screen. jsdom has no layout engine — `getBoundingClientRect`/`offsetWidth` are
// always zero, and percentage/`vw`/flex-distributed widths never resolve against a real
// parent (the CMX-273/CMX-298 empirical finding — see
// tests/dashboard_scale_nav_a11y.test.mjs:162, docs/SPIKE_WALL_FILLS_STAGE.md, and
// tests/kanban_flatten.test.mjs's own "NOT GUARDED" section for `.kanban-card-parked`).
// A sibling rule this file never reads (e.g. a wider `.ar-role` base-rule flex-grow, or
// an overflowing sibling text node) could still widen the real rendered box without
// changing any declaration pinned above — the same gap this file's top NOTE has flagged
// since round 2.
//
// ⛔ Do not "fix" this by adding a property assertion for whatever regression is found
// next UNLESS you first verify — the way the `margin` finding above was corrected —
// that the property is actually a declared-value CASCADE read and not a resolved-geometry
// LAYOUT one. If it genuinely needs real layout to observe, the honest project is a
// Playwright-sized one, exactly as docs/SPIKE_WALL_FILLS_STAGE.md sizes it — not another
// line here.
//
// The outcome is therefore verified the way CMX-273/CMX-298 verified their own: BY
// CAPTURE, in a real browser with a real layout engine — not jsdom. Done 2026-08-17
// against this exact head, with the real style.css and the real nav.js/util.js/
// orchestrator.js module graph (the exact markup bootDashboardDom renders for this
// file's own fixture, re-mounted in a static page and opened in headless Chromium
// 150.0.7871.128, `--dump-dom` reading back `getBoundingClientRect`/`getComputedStyle`
// off the real, laid-out DOM):
//
//   orchestrator  box 18x18px  svg 12x12px  display flex  visibility visible  opacity 1
//                 width/min-width/max-width 18px  padding 0/0  margin 0/0 (round 8)
//                 title="Orchestrator session"  aria-label="Orchestrator session"
//   dispatched    box 18x18px  svg 12x12px  display flex  visibility visible  opacity 1
//                 width/min-width/max-width 18px  padding 0/0  margin 0/0 (round 8)
//                 title="Dispatched session"  aria-label="Dispatched session"
//
// — all non-zero under real layout, matching the CASCADE assertions above exactly, and a
// full-row screenshot at that same head shows the pink crown and the blue bot as two
// visually distinct glyphs (not blank gaps, not colour-only dots) sitting immediately
// left of the fully-legible, un-truncated "orch"/"worker" session names — the reported
// bug this ticket exists to fix. Both cues are non-hue (a crown vs. a bot, not merely
// pink vs. blue), so the distinction survives greyscale (Liav is red-weak; hue as the
// sole encoding of a badge's meaning would be a defect in this repo, not a preference).
