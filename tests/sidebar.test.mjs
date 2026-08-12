// THE SIDEBAR, IN A REAL DOM — two sections, zero filter chips, collapsible.
//
// The first cut of this suite was `readFileSync` + `String.includes` over the
// sources. It asserted the artifact that was WRITTEN, never the one that RUNS, and
// three of the four invariants it claimed to lock survived deliberate corruption
// with a fully green suite: the colourblind cue was "a const named _TYPE_GLYPH
// exists" (empty the span that RENDERS it and nothing failed), and persistence was
// "the source says setItem" (delete the restore half and nothing failed). That is
// the exact pattern tests/wall.test.mjs's header says this repo abolished.
//
// So the behavioural invariants below run the REAL nav.js in a REAL DOM (jsdom):
// the real `renderSidebarAgents` into a real `#sidebar-agents`, the real
// `toggleSidebar`, the real module-load restore off a real `localStorage`.
//
//   1. THE CUE IS NEVER HUE-ALONE. Three coloured dots would encode the window type
//      in colour only — unreadable for a red-weak viewer, invisible in greyscale.
//      A GLYPH (C / $ / ⚙) must be RENDERED into every row; the Okabe-Ito tint only
//      reinforces it. Asserted on the rendered node's textContent, not on a const.
//   2. ONE CONTROL, TWO BEHAVIOURS. #btn-menu / toggleSidebar drives both the phone
//      drawer and the desktop rail, and the desktop state SURVIVES A RELOAD — which
//      means the restore half (getItem at module load), not just the write half.
//   3. THE LAUNCH MENU STAYS ON SCREEN. It right-aligns to a button that sits ~55px
//      from the viewport edge, so it must measure itself, not guess.
//
// The fleet-reload trap (#3 in the task: sidebar state must never reach the wall's
// `_termSig` render-cache key, or collapsing reloads EVERY LIVE TERMINAL) is NOT
// here: it belongs where the real wall is built, and lives in tests/wall.test.mjs
// as "collapsing the sidebar re-fits the wall — it does NOT rebuild it", which
// compares real <iframe> node identity across a real collapse. A grep for the string
// 'sidebar-collapsed' in terminals.js — which is what used to stand in for it — stays
// green while the fleet reloads on every toggle. It is measured there, not here.
//
// Run: node --test tests/sidebar.test.mjs  (pytest runs it via tests/test_js_suites.py;
// it needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const src = p => readFileSync(join(ROOT, p), 'utf8');

const NAV = src('static/js/nav.js');
const LAUNCHER = src('static/js/launcher.js');
const HTML = src('templates/index.html');
const CSS = src('static/style.css');

// The sidebar chrome, as index.html emits it (only the ids nav.js reaches for).
const BODY = `
<header class="topbar">
  <button class="icon-btn sidebar-toggle" id="btn-menu" aria-expanded="true"
          onclick="chela.toggleSidebar()"></button>
  <button class="icon-btn" id="btn-new" onclick="chela.openNewMenu(event)"></button>
</header>
<div class="app">
  <aside class="sidebar">
    <section class="side-section">
      <div class="side-list" id="side-nav"></div>
      <div class="side-list" id="side-nav-more"></div>
    </section>
    <section class="side-section">
      <span class="side-count" id="hdr-agents">-/-</span>
      <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
    </section>
  </aside>
</div>
<div class="drawer-scrim" id="sidebar-scrim" onclick="chela.closeSidebar()"></div>
<div class="popover launch-menu" id="new-menu" style="display:none;">
  <div id="new-menu-launch"></div>
</div>`;

const SIDEBAR_COLLAPSED_KEY = 'chela_sidebar_collapsed';

// The phone/desktop split is `matchMedia('(max-width: 768px)')`. Make it steerable.
let PHONE = false;

let nav, util;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        // defineProperty, NOT assignment — see the note in tests/wall.test.mjs:
        // `globalThis.navigator` is getter-only from node 21 and assignment THROWS.
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: PHONE && /max-width:\s*768px/.test(q),
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    // jsdom ships no canvas, and `getContext('2d')` returns null. The tab-signal
    // badge (util.js::_drawFavicon) paints one whenever the "needs you" count goes
    // ABOVE ZERO — which a waiting/yellow agent row below now exercises. A no-op 2D
    // context keeps the assertions about the SIDEBAR rather than a canvas polyfill
    // (same stub as tests/walldock.test.mjs).
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;    // main.js arms poll timers a test has no use for

    // THE RESTORE HALF, ARMED BEFORE THE MODULE LOADS. nav.js reads this key at
    // module scope (that IS the restore), so the only honest way to test it is to
    // seed the storage a reload would have left behind and then load the module —
    // exactly the order a browser does it in.
    dom.window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '1');

    // The dashboard's modules are a cycle (nav ↔ main), so evaluation ORDER is the
    // browser's: main.js is the entry and everything else is pulled in behind it.
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    nav = await import('../chela/dashboard/static/js/nav.js');
});

// --- 1. 🔴 the type cue is a GLYPH, rendered — not a colour, and not a const -----

const agent = (name, over = {}) => ({
    name, window_id: '@1', online: true, session_status: 'idle', ...over,
});
const rowFor = name => document.querySelector(`#sidebar-agents .agent-row[data-agent="${name}"]`);

test('every row RENDERS its type as a glyph — the cue survives with the colour taken away', () => {
    nav.renderSidebarAgents([
        agent('bot', { window_type: 'claude' }),
        agent('sh', { window_type: 'shell' }),
        agent('api', { window_type: 'server' }),
    ]);
    // The rendered node, not the source: empty the span in _agentRowHtml and this
    // goes red. That is the deuteranomaly failure mode this cue exists to prevent.
    assert.equal(rowFor('bot').querySelector('.ar-type').textContent, 'C');
    assert.equal(rowFor('sh').querySelector('.ar-type').textContent, '$');
    assert.equal(rowFor('api').querySelector('.ar-type').textContent, '⚙');
    // …and the glyph is not the ONLY thing: the type is on the node for CSS to tint.
    assert.ok(rowFor('bot').querySelector('.ar-type').classList.contains('claude'));
});

test('a window with no declared type still gets a glyph — never a blank cue', () => {
    nav.renderSidebarAgents([
        agent('running', { claude_running: true }),   // inferred: claude
        agent('bare', {}),                            // inferred: shell
    ]);
    assert.equal(rowFor('running').querySelector('.ar-type').textContent, 'C');
    assert.equal(rowFor('bare').querySelector('.ar-type').textContent, '$');
});

// --- 1b. a session reads as a REAL name, not the generic "claude window" ---------
//
// _agentLabel (nav.js) → _displayLabel (terminals.js) is the shared formatter for
// both the sidebar row and the wall pane title. A name a human chose is shown
// verbatim; a generic one (`shell-2`, or the bare `claude` tmux follows) is a blank
// filled with the most meaningful thing known: the Claude session name, then the
// repo, then the raw name. Driven through the REAL render into the REAL node.
const nameFor = name => rowFor(name).querySelector('.agent-row-name').textContent;

test('a generic "claude" window shows its Claude session name, not "claude"', () => {
    const rows = [
        // human-chosen name — intent, shown verbatim even though a session name exists
        { name: 'reviewer', window_id: '@1', session_name: 'ignore me', online: true },
        // generic tmux-followed `claude` — the session name fills the blank
        { name: 'claude', window_id: '@2', session_name: 'porting the wall', cwd: '/x/chelamux', online: true },
        // generic, no session name — falls through to the repo (cwd basename)
        { name: 'claude', window_id: '@3', cwd: '/home/u/projects/ccbot', online: true },
        // generic shell in a repo — the pre-existing repo-basename behaviour holds
        { name: 'shell-1', window_id: '@4', cwd: '/home/u/projects/nautilus', online: true },
        // generic, nothing to fill it with — the raw name, never a blank
        { name: 'claude', window_id: '@5', online: true },
    ];
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);

    assert.equal(nameFor('reviewer'), 'reviewer');       // chosen name wins over session_name
    // The three 'claude' windows share a data-agent, so assert their resolved labels
    // over the rendered nodes — each fell back a different rung of the same ladder.
    const labels = [...document.querySelectorAll('#sidebar-agents .agent-row-name')].map(n => n.textContent);
    assert.ok(labels.includes('porting the wall'), 'a generic claude window did not use its session name');
    assert.ok(labels.includes('ccbot'), 'a generic claude window did not fall back to its repo');
    assert.ok(labels.includes('nautilus'), 'a generic shell did not fall back to its repo');
    assert.ok(labels.includes('claude'), 'a generic window with nothing to resolve lost its raw name');
});

test('a session name is ESCAPED — it is tmux/user-derived, never trusted into the DOM', () => {
    const rows = [{ name: 'claude', window_id: '@9', session_name: '<img src=x>', online: true }];
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
    const span = document.querySelector('#sidebar-agents .agent-row-name');
    assert.equal(span.textContent, '<img src=x>');           // shown as text…
    assert.equal(span.querySelector('img'), null);           // …never parsed as markup
});

test('colour is the SECOND channel, and it is colourblind-safe (Okabe-Ito)', () => {
    // The palette itself is a CSS fact — there is no computed style in jsdom to read
    // it back from, so this one is honestly a source assertion, and says so.
    ['#56B4E9', '#009E73', '#E69F00'].forEach(c =>
        assert.ok(CSS.includes(c), `the type cue dropped the Okabe-Ito colour ${c}`));
});

// --- 1c. 🔴 the Feed nav icon is the lucide `rss` mark, not the ≡ glyph ----------
//
// The old ≡ read exactly like the sidebar toggle. views.js now carries `lucide: 'rss'`
// and _navItemHtml renders it through util.js's vendored SVG set. Driven through the
// REAL renderNav() into the REAL #side-nav: revert the view to a glyph and no <svg>
// is emitted; drop 'rss' from util.js's _LUCIDE and the <svg> comes out empty. Either
// reddens this — it asserts the mark that RENDERS, not the string in the source.
test('the Feed nav item renders the lucide rss SVG — not a glyph that apes the toggle', () => {
    nav.renderNav();
    // CMX-230: Feed is a primary-tier view, so it's still in #side-nav specifically
    // (not the demoted #side-nav-more) — see the nav-inventory guard in
    // tests/dashboard_scale_nav_a11y.test.mjs, which pins that partition.
    const icon = document.querySelector('#side-nav .side-item[data-view="feed"] .side-item-icon');
    assert.ok(icon, 'the Feed nav item is missing');
    const svg = icon.querySelector('svg');
    assert.ok(svg, 'the Feed icon is not an SVG — it fell back to a text glyph');
    // The distinctive rss arc — present iff util.js still carries the `rss` paths.
    assert.ok(/M4 11a9 9 0 0 1 9 9/.test(svg.innerHTML),
        'the Feed icon SVG is empty — `rss` is not in util.js _LUCIDE');
    assert.ok(!icon.textContent.includes('≡'), 'the old ≡ glyph is still rendered');
});

// --- 1c². 🔴 ALL FIVE nav icons are lucide SVGs — one uniform box, no stray glyph --
//
// CMX-86 made Feed a lucide mark; CMX-87 converts the other four (Wall/Work/Knowledge/
// Agents) so every nav icon shares the same fixed 24×24 box instead of unicode glyphs
// whose metrics differ. This drives the REAL renderNav() into the REAL #side-nav and
// asserts what RENDERS: each of the five nav items carries a non-empty <svg> and none
// leaks an old glyph. Revert any one view to `icon: '…'` and that item's <svg> vanishes
// (red); drop its name from util.js _LUCIDE and its <svg> comes out empty (red).
test('every nav item renders a non-empty lucide SVG — no unicode glyph survives', () => {
    nav.renderNav();
    const OLD_GLYPHS = ['▦', '▤', '◆', '▢', '≡'];
    // CMX-230: this asserts the ICON quality (real SVG, no glyph fallback) on every
    // registered view regardless of which nav list it renders into — Knowledge/
    // Agents/Personas/Cost moved to #side-nav-more (demoted), Feed/Wall/Work stay
    // in #side-nav (primary). The partition itself is pinned by
    // tests/dashboard_scale_nav_a11y.test.mjs's nav-inventory guard, not here.
    for (const id of ['feed', 'terminals', 'work', 'knowledge', 'agents', 'personas', 'cost']) {
        const icon = document.querySelector(`#side-nav .side-item[data-view="${id}"] .side-item-icon, #side-nav-more .side-item[data-view="${id}"] .side-item-icon`);
        assert.ok(icon, `the ${id} nav item is missing`);
        const svg = icon.querySelector('svg');
        assert.ok(svg, `the ${id} nav icon is not an SVG — it fell back to a text glyph`);
        assert.ok(svg.children.length > 0,
            `the ${id} nav icon SVG is empty — its lucide name is not in util.js _LUCIDE`);
        for (const g of OLD_GLYPHS)
            assert.ok(!icon.textContent.includes(g), `the old ${g} glyph is still rendered on ${id}`);
    }
});

// --- 1c^b. 🔴 the LABEL is real text on every rendered row, primary AND demoted --
//
// style.css's own must-never for the demoted group: "Same .side-item row underneath
// (icon still a real lucide mark, LABEL STILL REAL TEXT — re-parenting must not cost
// the accessibility cue)". The only prior guard pointed at the label was a WIRING
// test (dashboard_scale_nav_a11y.test.mjs) matching the CLASS STRING inside
// _navItemHtml's template — never the text it wraps — so emptying the label span
// left every nav row (primary and demoted alike) icon-only, and every guard stayed
// green. This drives the REAL renderNav() and reads .side-item-label.textContent
// back off the REAL rendered node for every id in both #side-nav and #side-nav-more.
test('CMX-257: every nav item renders its REAL label as text — re-parenting into the demoted group must not cost it', () => {
    nav.renderNav();
    const LABELS = {
        feed: 'Feed', terminals: 'Wall', work: 'Work',
        knowledge: 'Knowledge', agents: 'Agents', personas: 'Personas', cost: 'Cost',
    };
    for (const [id, label] of Object.entries(LABELS)) {
        const row = document.querySelector(
            `#side-nav .side-item[data-view="${id}"], #side-nav-more .side-item[data-view="${id}"]`);
        assert.ok(row, `the ${id} nav item is missing`);
        assert.equal(row.querySelector('.side-item-label').textContent, label,
            `${id}'s .side-item-label lost its real text — icon-only nav rows are exactly the ` +
            'hue-free-cue regression this ticket exists to protect against');
    }
});

// --- 1c³. 🔴 renderNav ACTUALLY SPLITS the sidebar: primary → #side-nav, demoted →
// #side-nav-more — not just "somewhere in either list" (the test above, by design,
// is blind to which host an item lands in). tests/dashboard_scale_nav_a11y.test.mjs's
// GUARD 7 only calls primaryNavViews/secondaryNavViews directly on entries scraped
// out of views.js source text — it never asserts the RENDERED sidebar, so reverting
// renderNav to dump every item into #side-nav (leaving #side-nav-more empty, the
// registry, `tier` fields and both selector functions all untouched) stayed green. This
// drives the REAL renderNav() into the REAL #side-nav / #side-nav-more and reads the
// partition back off the rendered nodes.
test('CMX-230: renderNav actually SPLITS the sidebar — primary views in #side-nav, demoted views in #side-nav-more', () => {
    nav.renderNav();
    const idsIn = sel => [...document.querySelectorAll(`${sel} .side-item`)].map(el => el.dataset.view);
    const primaryIds = idsIn('#side-nav');
    const secondaryIds = idsIn('#side-nav-more');

    // CMX-230 round 11: this used to compare primaryIds.sort()/secondaryIds.sort()
    // against the expected set — order-blind by construction, so a mutation at the
    // RENDER call site (nav.js: `primaryNavViews(VIEWS, ctx).reverse().map(...)`)
    // left the shipped rail reading Work/Wall/Feed while every guard, including
    // tests/dashboard_scale_nav_a11y.test.mjs's GUARD 7 (which only ever checks
    // primaryNavViews()'s own return order, never the rendered DOM), stayed green.
    // primaryIds/secondaryIds already come from the REAL rendered DOM in document
    // order — asserting them UNSORTED closes that hole directly, no separate
    // render-order guard needed.
    assert.deepEqual(primaryIds, ['feed', 'terminals', 'work'],
        '#side-nav must render exactly the 3 primary views, IN ORDER — a full un-split dump, an empty split, or ' +
        'a reorder at the render call site (e.g. .reverse()) breaks this');
    assert.deepEqual(secondaryIds, ['knowledge', 'agents', 'personas', 'cost'],
        '#side-nav-more must render exactly the 4 demoted views, IN ORDER — an empty #side-nav-more means the split never ran');

    for (const id of secondaryIds) assert.ok(!primaryIds.includes(id), `${id} must not ALSO render into #side-nav`);
    for (const id of primaryIds) assert.ok(!secondaryIds.includes(id), `${id} must not ALSO render into #side-nav-more`);
});

// --- 1d. 🔴 the EXPANDED sidebar icons are sized to MATCH the collapsed rail ------
//
// CMX-85 enlarged the collapsed-rail glyphs; CMX-86 brings the expanded ones up to
// the same size so folding the sidebar never resizes an icon. jsdom does no layout,
// so this reads the two rules off the CSS source and asserts they AGREE — which is
// the actual requirement ("match the collapsed ones"), not a magic number. Change
// one side and not the other and the equality breaks. (A bare `.side-item-icon`
// declaration also feeds `.side-item.active .side-item-icon`, so anchor to the rule
// that opens the property block.)
function _ruleBody(selectorSource) {
    const m = CSS.match(new RegExp(selectorSource + '\\s*\\{([^}]*)\\}'));
    assert.ok(m, `CSS rule not found: ${selectorSource}`);
    return m[1];
}
const _prop = (body, prop) => {
    const m = body.match(new RegExp('(?:^|[;{\\s])' + prop + '\\s*:[^;]*?(\\d+)px'));
    return m ? Number(m[1]) : null;
};

test('the expanded nav glyph is the SAME font-size as the collapsed rail', () => {
    const expanded = _prop(_ruleBody('\\n\\.side-item-icon'), 'font-size');
    const collapsed = _prop(_ruleBody('body\\.sidebar-collapsed \\.side-item-icon'), 'font-size');
    assert.ok(expanded && collapsed, 'a nav-icon font-size is missing');
    assert.equal(expanded, collapsed,
        `expanded nav glyph (${expanded}px) does not match the collapsed rail (${collapsed}px)`);
});

test('the expanded type badge (.ar-type) is the SAME size as the collapsed rail', () => {
    const exp = _ruleBody('\\n\\.ar-type');
    const col = _ruleBody('body\\.sidebar-collapsed \\.ar-type');
    assert.equal(_prop(exp, 'height'), _prop(col, 'height'), '.ar-type height differs from the collapsed rail');
    assert.equal(_prop(exp, 'font-size'), _prop(col, 'font-size'), '.ar-type font-size differs from the collapsed rail');
});

test('the expanded status dot is the SAME size as the collapsed rail', () => {
    const exp = _ruleBody('\\.agent-row\\.rich \\.term-status-dot');
    const col = _ruleBody('body\\.sidebar-collapsed \\.agent-row\\.rich \\.term-status-dot');
    assert.equal(_prop(exp, 'width'), _prop(col, 'width'), 'the status-dot width differs from the collapsed rail');
    assert.equal(_prop(exp, 'height'), _prop(col, 'height'), 'the status-dot height differs from the collapsed rail');
});

// --- 2. 🔴 one control, two behaviours — and the desktop state survives a reload --

test('the desktop rail RESTORES itself from the last session', () => {
    // localStorage said '1' before nav.js loaded (see `before`). Delete nav.js's
    // getItem block and the class is absent here — a collapse that forgets itself
    // on reload, which is precisely what the requirement forbids.
    assert.ok(document.body.classList.contains('sidebar-collapsed'),
        'the persisted collapsed state was NOT restored at module load');
    assert.equal(document.getElementById('btn-menu').getAttribute('aria-expanded'), 'false');
});

test('on a desktop the control collapses the rail, and persists it', () => {
    PHONE = false;
    window.chela.toggleSidebar();       // restored collapsed -> expand
    assert.equal(document.body.classList.contains('sidebar-collapsed'), false);
    assert.equal(localStorage.getItem(SIDEBAR_COLLAPSED_KEY), '0');
    assert.equal(document.getElementById('btn-menu').getAttribute('aria-expanded'), 'true');

    window.chela.toggleSidebar();       // and back
    assert.ok(document.body.classList.contains('sidebar-collapsed'));
    assert.equal(localStorage.getItem(SIDEBAR_COLLAPSED_KEY), '1',
        'the collapsed state is not written — it would forget itself on reload');

    // The rail is a body class and nothing else: no drawer was opened underneath.
    assert.equal(document.querySelector('.sidebar').classList.contains('open'), false);
});

test('navigating away closes the phone drawer but NEVER folds the desktop rail', () => {
    PHONE = false;
    window.chela.toggleSidebar();                  // expand it
    assert.equal(document.body.classList.contains('sidebar-collapsed'), false);
    window.chela.closeSidebar();                   // selectView() calls this on EVERY click
    assert.equal(document.body.classList.contains('sidebar-collapsed'), false,
        'a sidebar that folds itself away whenever you use it is not a sidebar');
});

test('on a phone the SAME control slides the drawer, and leaves the rail class alone', () => {
    PHONE = true;
    const sb = document.querySelector('.sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    const railBefore = document.body.classList.contains('sidebar-collapsed');

    window.chela.toggleSidebar();
    assert.ok(sb.classList.contains('open'), 'the drawer did not slide in');
    assert.ok(scrim.classList.contains('open'), 'the drawer has no scrim behind it');
    assert.equal(document.body.classList.contains('sidebar-collapsed'), railBefore,
        'the phone drawer moved the DESKTOP rail state');

    window.chela.closeSidebar();                   // tapping a row dismisses it
    assert.equal(sb.classList.contains('open'), false);
    assert.equal(scrim.classList.contains('open'), false);
    PHONE = false;
});

// --- 3. 🟠 the launch menu must not run off the right edge -----------------------

test('the launch menu right-aligns off its MEASURED width — it stays on screen', () => {
    const m = document.getElementById('new-menu');
    const anchor = document.getElementById('btn-new');
    // #btn-new sits near the right edge of the topbar, as it does in the live app.
    anchor.getBoundingClientRect = () => ({
        top: 8, bottom: 40, left: 969, right: 1001, width: 32, height: 32,
    });
    // jsdom does no layout, so hand it the width the CSS gives the menu.
    const width = Number(CSS.match(/\.launch-menu\s*{[^}]*min-width:\s*(\d+)px/)[1]);
    assert.equal(width, 232, 'the launch menu CSS width moved — retune this fixture');
    Object.defineProperty(m, 'offsetWidth', { value: width, configurable: true });

    window.chela.openNewMenu({ stopPropagation() {}, currentTarget: anchor });

    assert.equal(m.style.display, 'block');
    const left = parseFloat(m.style.left);
    assert.equal(left + width, 1001, 'the menu is not right-aligned to the button');
    assert.ok(left + width <= window.innerWidth,
        `the launch menu runs off the RIGHT edge (${left}+${width} > ${window.innerWidth}) — `
        + 'it is right-aligned off a hardcoded width, not its real one');
    window.chela.hideNewMenu();
});

test('…and it never runs off the LEFT edge either (a narrow phone)', () => {
    const m = document.getElementById('new-menu');
    const anchor = document.getElementById('btn-new');
    anchor.getBoundingClientRect = () => ({ top: 8, bottom: 40, left: 68, right: 100, width: 32, height: 32 });
    Object.defineProperty(m, 'offsetWidth', { value: 232, configurable: true });   // wider than the button's offset

    window.chela.openNewMenu({ stopPropagation() {}, currentTarget: anchor });
    assert.equal(parseFloat(m.style.left), 8, 'the menu was not clamped to the left edge');
    window.chela.hideNewMenu();
});

// --- 4. what was DELETED ---------------------------------------------------------
//
// These are absence-of-code assertions, and a grep is the honest tool for one: the
// property is "this code no longer exists", so there is no behaviour left to drive.
// (Everything above this line runs the real module instead.)

test('the type filter is gone — markup, handler and state', () => {
    assert.ok(!HTML.includes('agent-filter'), 'the filter chip row is still in index.html');
    assert.ok(!NAV.includes('setAgentFilter'), 'setAgentFilter still exists');
    assert.ok(!NAV.includes('_agentFilter'), 'the filter state still exists');
    assert.ok(!CSS.includes('.agent-filter'), 'the filter chips still have styling');
});

test('the sidebar is two sections — Launch folded into the launch menu', () => {
    assert.ok(!HTML.includes('launcher-section'), 'the Launch sidebar section is still there');
    assert.ok(HTML.includes('new-menu-launch'), 'the launch menu has no Favorites/Recent host');
    // Every launch behaviour moved WITH the rows: click-to-launch, pin, unpin,
    // forget-a-recent, add-a-favourite.
    ['launchProject', 'pinFav', 'unpinFav', 'forgetRecent', 'openFavAdd'].forEach(fn =>
        assert.ok(LAUNCHER.includes(fn), `${fn} was lost in the move`));
    assert.ok(LAUNCHER.includes("getElementById('new-menu-launch')"),
        'the launcher does not render into the launch menu');
    // One toggle in the markup, not two.
    assert.equal(HTML.match(/toggleSidebar\(\)/g).length, 1, 'a second sidebar toggle appeared');
});

// --- CMX-230, round 2: GUARD 3b / GUARD 4 in tests/dashboard_scale_nav_a11y.test.mjs
// only source-text-match nav.js's templates — `_AGENT_STATUS_WORD`'s literal map
// and the `<span class="ar-state ${stCls}">${stWord}</span>` / `${p}%` template
// strings. Neither renders a row, so a judge round blanked the VALUE that feeds
// each template (`const stWord = '';` / `const p = '';`) and both regexes still
// matched the untouched template shape byte-for-byte, green. These drive the REAL
// `_agentRowHtml` (via `renderSidebarAgents`) into a REAL row and read `.ar-state`
// / `.ar-ctx` back off the rendered node — blanking either value now shows up as
// an empty text node, not a passing regex.
// CMX-257 round 12: the two rows above were busy/idle only — the yellow/waiting
// row (wantsHuman: this codebase's "needs you", the one state a red-weak operator
// most needs a word for) was never driven through a real render, so blanking
// `stWord` for `dot === 'yellow'` alone left every waiting row's .ar-state an
// empty span with only its .waiting colour class, and this test — plus GUARD 3b's
// source-text match on the untouched _AGENT_STATUS_WORD constant — stayed green.
// A waiting agent is also rendered inside `.side-triage` (the "Needs you" cluster,
// see renderSidebarAgents), not the plain project-grouped rows — rowFor() finds it
// either way since `_agentRowHtml` is the same template for both.
test('CMX-230: the sidebar row\'s .ar-state renders the real status word, not blank — colour is not the only cue', () => {
    nav.renderSidebarAgents([
        agent('working-one', { session_status: 'busy' }),
        agent('idle-one', { session_status: 'idle' }),
        agent('waiting-one', { session_status: 'waiting' }),
    ]);
    assert.equal(rowFor('working-one').querySelector('.ar-state').textContent, 'working',
        '.ar-state must carry the real status word, not an empty span the colour class alone would leave');
    assert.equal(rowFor('idle-one').querySelector('.ar-state').textContent, 'idle');
    assert.equal(rowFor('waiting-one').querySelector('.ar-state').textContent, 'waiting',
        '.ar-state must carry the real status word for the waiting/yellow row too — leaving it blank for ' +
        'exactly this state is unreadable to a red-weak viewer who needs the word most');
});

test('CMX-230: the sidebar row\'s .ar-ctx renders the real percentage number, not blank — colour is not the only cue', () => {
    nav.updateCtxCache([{ window_id: '@1', used_pct: 87 }]);
    nav.renderSidebarAgents([agent('ctx-one', { window_id: '@1' })]);
    const chip = rowFor('ctx-one').querySelector('.ar-ctx');
    assert.ok(chip, '.ar-ctx chip did not render for an agent with a cached context %');
    assert.equal(chip.textContent, '87%',
        '.ar-ctx must carry the real percentage number, not a bare "%" the warn/danger class alone would leave');
    assert.ok(chip.classList.contains('danger'), 'a used_pct > 80 must still carry the danger class alongside the number');
});
