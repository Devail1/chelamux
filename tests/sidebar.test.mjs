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
    <section class="side-section"><div class="side-list" id="side-nav"></div></section>
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
    const icon = document.querySelector('#side-nav .side-item[data-view="feed"] .side-item-icon');
    assert.ok(icon, 'the Feed nav item is missing');
    const svg = icon.querySelector('svg');
    assert.ok(svg, 'the Feed icon is not an SVG — it fell back to a text glyph');
    // The distinctive rss arc — present iff util.js still carries the `rss` paths.
    assert.ok(/M4 11a9 9 0 0 1 9 9/.test(svg.innerHTML),
        'the Feed icon SVG is empty — `rss` is not in util.js _LUCIDE');
    assert.ok(!icon.textContent.includes('≡'), 'the old ≡ glyph is still rendered');
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
