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
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

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
    // The dashboard's modules are a cycle (nav ↔ main), so evaluation ORDER is the
    // browser's: main.js is the entry and everything else is pulled in behind it.
    ({ modules: { util, nav } } = await bootDashboardDom({
        body: BODY,
        // jsdom ships no canvas, and `getContext('2d')` returns null. The tab-signal
        // badge (util.js::_drawFavicon) paints one whenever the "needs you" count goes
        // ABOVE ZERO — which a waiting/yellow agent row below now exercises. A no-op 2D
        // context keeps the assertions about the SIDEBAR rather than a canvas polyfill
        // (same stub as tests/walldock.test.mjs).
        canvasStub: true,
        // `PHONE` is read LIVE (not captured at boot) — tests below flip it mid-suite
        // to move between phone/desktop mode without a re-import.
        phone: () => PHONE,
        // THE RESTORE HALF, ARMED BEFORE THE MODULE LOADS. nav.js reads this key at
        // module scope (that IS the restore), so the only honest way to test it is to
        // seed the storage a reload would have left behind and then load the module —
        // exactly the order a browser does it in.
        seedLocalStorage: { [SIDEBAR_COLLAPSED_KEY]: '1' },
        extraModules: ['util.js', 'nav.js'],
    }));
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

// --- 1c. 🔴 every nav icon is a lucide SVG — one uniform box, no stray glyph --
//
// CMX-86/87 converted every nav icon to a lucide mark sharing the same fixed
// 24×24 box instead of unicode glyphs whose metrics differ. CMX-279 (measured,
// not assumed — Liav named exactly two of the seven views he actually opens)
// deleted Feed, Knowledge, Agents, Personas and Cost outright (CMX-230 had only
// demoted them into a quieter #side-nav-more group; that group is gone too) —
// what's left to guard is Wall and Work. This drives the REAL renderNav() into
// the REAL #side-nav and asserts what RENDERS: each carries a non-empty <svg>
// and none leaks an old glyph. Revert either view to `icon: '…'` and its <svg>
// vanishes (red); drop its name from util.js _LUCIDE and its <svg> comes out
// empty (red).
test('every nav item renders a non-empty lucide SVG — no unicode glyph survives', () => {
    nav.renderNav();
    const OLD_GLYPHS = ['▦', '▤', '◆', '▢', '≡'];
    for (const id of ['terminals', 'work']) {
        const icon = document.querySelector(`#side-nav .side-item[data-view="${id}"] .side-item-icon`);
        assert.ok(icon, `the ${id} nav item is missing`);
        const svg = icon.querySelector('svg');
        assert.ok(svg, `the ${id} nav icon is not an SVG — it fell back to a text glyph`);
        assert.ok(svg.children.length > 0,
            `the ${id} nav icon SVG is empty — its lucide name is not in util.js _LUCIDE`);
        for (const g of OLD_GLYPHS)
            assert.ok(!icon.textContent.includes(g), `the old ${g} glyph is still rendered on ${id}`);
    }
});

// --- 1c^b. 🔴 the LABEL is real text on every rendered row --------------------
//
// The only prior guard pointed at the label was a WIRING test
// (dashboard_scale_nav_a11y.test.mjs) matching the CLASS STRING inside
// _navItemHtml's template — never the text it wraps — so emptying the label
// span left every nav row icon-only, and every guard stayed green. This drives
// the REAL renderNav() and reads .side-item-label.textContent back off the
// REAL rendered node for both shipped views.
test('every nav item renders its REAL label as text — not an icon-only row', () => {
    nav.renderNav();
    const LABELS = { terminals: 'Wall', work: 'Work' };
    for (const [id, label] of Object.entries(LABELS)) {
        const row = document.querySelector(`#side-nav .side-item[data-view="${id}"]`);
        assert.ok(row, `the ${id} nav item is missing`);
        assert.equal(row.querySelector('.side-item-label').textContent, label,
            `${id}'s .side-item-label lost its real text — an icon-only nav row is exactly the ` +
            'hue-free-cue regression this ticket exists to protect against');
    }
});

// --- 1c³. 🔴 selecting a view lights its own row, and only its own row -------
//
// _syncSidebarActive (nav.js) sweeps `.side-item` to toggle `.active`. This
// drives the REAL renderNav() + REAL selectView() and reads `.active` back off
// the REAL rendered rows — a guard that only checked the class STRING existed
// in source would pass a sweep that never actually ran.
test('selecting a view lights its own row and clears the other — via the REAL onclick handler', () => {
    nav.renderNav();

    // round 20/21 (judge findings on PR #326, CMX-257): the ONLY thing that
    // makes a rendered nav row route anywhere is the onclick _navItemHtml
    // emits — calling window.chela.selectView(...) directly never touches it,
    // and a substring regex on the attribute cannot tell a live statement from
    // dead code (`if (false) chela.selectView(...)` still contains the exact
    // bytes a presence-only regex looks for). So below, the attribute is
    // EVALUATED as a function body — the same body the browser would run on
    // click — against a recording stub bound to the row as `this` (matching
    // _navItemHtml's `this.dataset.view`), and the assertion is that the stub
    // was actually CALLED.
    const _invokeOnclick = (row, chelaStub) => {
        const handler = new Function('chela', row.getAttribute('onclick') || '');
        handler.call(row, chelaStub);
    };

    const workRow = document.querySelector('#side-nav .side-item[data-view="work"]');
    assert.match(workRow.getAttribute('onclick'), /chela\.selectView\(this\.dataset\.view\)/,
        'the Work row is not wired to chela.selectView(this.dataset.view)');
    const calls = [];
    _invokeOnclick(workRow, { selectView: (...args) => calls.push(args) });
    assert.deepEqual(calls, [['work']],
        "the Work row's onclick did not actually CALL chela.selectView — dead-coding the handler leaves the " +
        'attribute text intact but the row unreachable');

    window.chela.selectView('work');
    assert.equal(
        document.querySelector('#side-nav .side-item[data-view="work"]').classList.contains('active'),
        true, 'the Work row never lit after selecting it');
    assert.equal(
        document.querySelectorAll('#side-nav .side-item.active').length, 1,
        'more than one row is lit after selecting Work');

    window.chela.selectView('terminals');
    assert.equal(
        document.querySelector('#side-nav .side-item[data-view="terminals"]').classList.contains('active'),
        true, 'the Wall row never lit after selecting it');
    assert.equal(
        document.querySelector('#side-nav .side-item[data-view="work"]').classList.contains('active'),
        false, 'the previously-active Work row is still lit after switching to the Wall');
});

// --- 1c⁴. 🔴 drilling into an agent lights NO nav row — it has none of its own -
//
// agent-detail is a virtual view (views.js: `virtual: true`) reached from the
// always-visible sidebar Sessions list, not from a nav tab — unlike the
// pre-CMX-279 shape, where it borrowed the (now-deleted) Agents row as a fake
// "parent" to keep something lit. nav.js's _syncSidebarActive now maps
// 'agent-detail' to no data-view at all, so NOTHING in #side-nav should light
// up while drilled into one. showAgentDetail itself isn't exported (it's
// reachable only from inline HTML handlers), so this drives the REAL,
// user-reachable path to it: `chela.selectAgent` — the sidebar agent row's own
// onclick — falls through to showAgentDetail whenever the wall can't place the
// agent (unresolved in `_agentsCache`, which is exactly this case: no fleet
// has been loaded into this jsdom instance for this name).
//
// CMX-279 rework round 1 (PR #350, judge finding): this also drives
// nav.js's `_agentDetailBackView()` — TERMINALS_ENABLED is true for this whole
// file (see before(), above), so the "← Back" link must route to 'terminals'
// (the Wall), never the deleted 'agents' view. tests/dashboard_default_view.test.mjs
// covers the OTHER branch (TERMINALS_ENABLED false -> 'work') plus the found-agent
// call site (nav.js:608); this covers the not-found call site (nav.js:560) on the
// terminals-on branch, closing all 4 combinations (DEFEAT_SHAPES shape 7: two call
// sites x two branches).
test('drilling into an agent lights no nav row — agent-detail has none of its own', () => {
    nav.renderNav();

    // showAgentDetail also fires an unawaited refreshSummary()/checkContext() — real
    // network calls in production, reaching #hdr-next/#hdr-updated (absent from this
    // suite's minimal BODY, see its own comment: "only the ids nav.js reaches for")
    // and expecting an array back from /api/agents/context. Give it both so those
    // calls resolve quietly instead of throwing into an unhandled rejection AFTER
    // this test (synchronous) has already returned.
    if (!document.getElementById('hdr-next')) document.body.appendChild(document.createElement('span')).id = 'hdr-next';
    if (!document.getElementById('hdr-updated')) document.body.appendChild(document.createElement('span')).id = 'hdr-updated';
    // renderAgentDetail (nav.js) no-ops without a host to paint into — absent from
    // this suite's minimal BODY (it only carries "the ids nav.js reaches for" for
    // the sidebar), so give it one here, same pattern as hdr-next/hdr-updated above.
    if (!document.getElementById('agent-detail')) document.body.appendChild(document.createElement('div')).id = 'agent-detail';
    const prevFetch = globalThis.fetch;
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    try {
        window.chela.selectAgent('cmx279-ghost-agent');
    } finally {
        globalThis.fetch = prevFetch;
    }

    assert.equal(
        document.querySelectorAll('#side-nav .side-item.active').length, 0,
        'a nav row is lit while drilled into an agent detail — agent-detail is virtual (no nav item of its own) ' +
        'and no longer borrows a deleted view\'s row, so nothing in #side-nav should be active');

    const back = document.querySelector('#agent-detail .detail-back');
    assert.ok(back, 'no .detail-back node rendered into #agent-detail (not-found branch, nav.js:560)');
    assert.match(back.getAttribute('onclick'), /chela\.selectView\('terminals'\)/,
        'the "← Back" link is not wired to chela.selectView(\'terminals\') — with terminals on, it must route ' +
        'to the Wall, never the deleted \'agents\' view');
});

// CMX-279 rework round 1 (PR #350, judge finding): the FOUND branch of
// renderAgentDetail (nav.js:608) is a SEPARATE call site from the not-found one
// above — DEFEAT_SHAPES shape 7 ("two callers, one guarded"). A resolvable
// agent with no window_id never enters selectAgent's wall-focus branch even
// with terminals on, so it always falls through to showAgentDetail's found path.
test('the agent-detail "← Back" link also routes to the Wall from the FOUND branch (nav.js:608)', () => {
    nav.renderNav();
    if (!document.getElementById('hdr-next')) document.body.appendChild(document.createElement('span')).id = 'hdr-next';
    if (!document.getElementById('hdr-updated')) document.body.appendChild(document.createElement('span')).id = 'hdr-updated';
    if (!document.getElementById('agent-detail')) document.body.appendChild(document.createElement('div')).id = 'agent-detail';
    util.setAgentsCache([{ name: 'cmx279-known-agent', online: true }]);   // no window_id -> always showAgentDetail
    const prevFetch = globalThis.fetch;
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    try {
        window.chela.selectAgent('cmx279-known-agent');
    } finally {
        globalThis.fetch = prevFetch;
    }

    const back = document.querySelector('#agent-detail .detail-back');
    assert.ok(back, 'no .detail-back node rendered into #agent-detail (found branch, nav.js:608)');
    assert.match(back.getAttribute('onclick'), /chela\.selectView\('terminals'\)/,
        'the "← Back" link is not wired to chela.selectView(\'terminals\') on the found branch');
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
