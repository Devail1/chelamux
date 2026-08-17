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

let nav, util, orchestrator;

before(async () => {
    // The dashboard's modules are a cycle (nav ↔ main), so evaluation ORDER is the
    // browser's: main.js is the entry and everything else is pulled in behind it.
    ({ modules: { util, nav, orchestrator } } = await bootDashboardDom({
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
        // CMX-300: the role badge reads orchestrator.js's live `_status`, which
        // orchestratorSubscribe/orchestratorRelease mutate through a REAL
        // /api/orchestrator/{subscribe,release} round trip — so the default
        // blanket-`{}` stub (every other test in this file relies on it staying
        // a no-op) needs those two paths to actually echo back an ok:true
        // envelope, exactly like the real endpoints in app.py.
        //
        // wid and name are deliberately DIFFERENT values here (never the same
        // string body.wid echoed twice) — app.py's own
        // _orchestrator_status_payload() returns wid = inbox.orchestrator_wid(...)
        // (the @id) and name = store['orchestrator_name'] (the window's tmux
        // NAME), two facts about the SAME window that are never equal in
        // production. A fixture that echoes body.wid into both fields makes
        // `orchestratorState().wid === a.window_id` and
        // `orchestratorState().name === a.window_id` indistinguishable — nav.js
        // reading the wrong one (DEFEAT_SHAPES #02) would be invisible here too.
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
            if (u.includes('/api/orchestrator/release')) {
                return Promise.resolve({
                    ok: true, status: 200,
                    json: () => Promise.resolve({ ok: true, wid: null, name: null, state: 'unregistered', why: '', queued: 0 }),
                });
            }
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        },
        extraModules: ['util.js', 'nav.js', 'orchestrator.js'],
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

// --- 1d. 🔴 CMX-300: every row's ROLE — orchestrator / dispatched / plain -------
//
// Three roles, mutually exclusive. 'orchestrator' is read live off orchestrator.js's
// shared `_status` (the SAME single decisions-inbox slot terminals.js's pane toggle
// and decisions.js's owner chip already render — driven here through the REAL
// orchestratorSubscribe()/orchestratorRelease() round trip, not a hand-set global).
// 'dispatched' is the API-provided `a.dispatched` flag (app.py's api_agents).
// 'plain' — a session opened by hand — renders NO badge at all: asserting its
// absence is the guard against a badge silently degrading into "shown on every
// row", which would be as unreadable as no cue at all.
//
// CMX-300 rework round 1 (PR #374, judge finding 1): the previous version of every
// test below called `orchestratorSubscribe()` and THEN hand-called
// `nav.renderSidebarAgents(...)` itself — which re-renders the row regardless of
// whether `onOrchestratorChange`'s listener ever fired, so neutering that listener
// body (DEFEAT_SHAPES #50 — a renderer proven against hand-called arguments, the
// real caller never run) stayed invisible. Every test below now renders ONCE,
// before subscribing, and asserts the post-subscribe/post-release state with NO
// further renderSidebarAgents call in between — the redraw has to come from the
// real listener nav.js registers at module load (nav.js:447), or these go red.
test('the orchestrator window gets an Orchestrator role badge — plain windows get none', async () => {
    const rows = [
        agent('orch', { window_id: '@1' }),
        agent('other', { window_id: '@2' }),
    ];
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
    assert.equal(rowFor('orch').querySelector('.ar-role'), null,
        'a window rendered a role badge before it ever held the inbox slot');

    // No renderSidebarAgents call here — onOrchestratorChange must be the thing
    // that redraws the row off the REAL subscribe round trip.
    await orchestrator.orchestratorSubscribe('@1');

    const badge = rowFor('orch').querySelector('.ar-role');
    assert.ok(badge, 'the orchestrator window rendered no .ar-role badge after subscribing — ' +
        'onOrchestratorChange did not redraw the sidebar');
    // CMX-302: the orchestrator badge is a bare crown ICON, not text — the word
    // itself lives in the title tooltip so the badge stays narrow.
    const svg = badge.querySelector('svg');
    assert.ok(svg, 'the orchestrator badge rendered no crown icon');
    // 🔴 GUARD (CMX-302 rework round 2): `querySelector('svg')` alone only proves an
    // <svg> WRAPPER exists — util.js's lucideIcon() emits the wrapper tag unconditionally
    // and interpolates _LUCIDE[name] inside it, so an empty `_LUCIDE['crown']` entry
    // ('' instead of the real path data) still produces a present-but-EMPTY <svg> that
    // this assertion alone cannot tell apart from a real crown. Assert the actual glyph
    // content rendered inside it.
    assert.ok(svg.querySelector('path'), 'the orchestrator badge rendered an empty <svg> — ' +
        'no <path> content (an empty _LUCIDE entry must not read as a rendered icon)');
    assert.equal(badge.getAttribute('title'), 'Orchestrator session');
    assert.ok(badge.classList.contains('orchestrator'));
    assert.equal(rowFor('other').querySelector('.ar-role'), null,
        'a plain window rendered a role badge — plain sessions must render none');

    // ...and releasing (no renderSidebarAgents call here either) must clear it.
    await orchestrator.orchestratorRelease('@1');
    assert.equal(rowFor('orch').querySelector('.ar-role'), null,
        'the Orchestrator badge survived orchestratorRelease — onOrchestratorChange ' +
        'did not redraw the sidebar on release either');
});

test('a dispatcher-owned window gets a Dispatched role badge, distinct from Orchestrator', async () => {
    await orchestrator.orchestratorRelease('@1');   // no window holds the inbox slot
    const rows = [
        agent('worker', { window_id: '@3', dispatched: true }),
        agent('manual', { window_id: '@4', dispatched: false }),
    ];
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
    const badge = rowFor('worker').querySelector('.ar-role');
    assert.ok(badge, 'a dispatched window rendered no .ar-role badge at all');
    assert.equal(badge.textContent, 'Dispatched');
    assert.ok(badge.classList.contains('dispatched'));
    assert.equal(rowFor('manual').querySelector('.ar-role'), null,
        'a manually-launched window rendered a role badge — plain sessions must render none');
});

test('holding BOTH the inbox slot and the dispatched flag reads as Orchestrator, not Dispatched', async () => {
    const rows = [agent('both', { window_id: '@5', dispatched: true })];
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
    assert.ok(!rowFor('both').querySelector('.ar-role').classList.contains('orchestrator'),
        'the row already read as Orchestrator before subscribing — fixture leaked state');

    await orchestrator.orchestratorSubscribe('@5');   // no renderSidebarAgents call here
    const badge = rowFor('both').querySelector('.ar-role');
    assert.ok(badge.classList.contains('orchestrator') && !badge.classList.contains('dispatched'),
        'holding both the inbox slot and the dispatched flag should read as Orchestrator, not Dispatched');
    await orchestrator.orchestratorRelease('@5');
});

// CMX-300 rework round 1 (PR #374, judge finding 1, WIRING): a click on any pane's
// orchestrator toggle (terminals.js) or the decisions dropdown (decisions.js) must
// redraw BOTH the sidebar badge AND an already-open agent-detail panel — off the
// SAME onOrchestratorChange listener, with no /api/agents refetch. Unlike the
// tests above (which only ever check the sidebar), this one also opens the
// agent-detail panel first and proves it updates too — the second half of
// nav.js:447-450's listener body, and the second thing the WIRING finding named.
// The agent-detail half of this same listener (nav.js:449, `if (_detailAgent)
// renderAgentDetail();`) is NOT driven here: with TERMINALS_ON (this suite's
// boot config), `selectAgent` on any window-id'd agent always routes to the
// wall (terminals.js's focusPaneByWid -> selectView('terminals')) and never
// falls through to showAgentDetail — see nav.js:140-151's own comment. An
// agent-detail panel open for a window that ALSO holds the orchestrator slot
// is therefore only reachable in a terminals-OFF deployment, which is a real,
// separate configuration — covered by
// tests/sidebar_agent_detail_orchestrator_wiring.test.mjs, which boots
// `terminalsEnabled: false` specifically so `selectAgent` falls through.
test('WIRING: onOrchestratorChange redraws the sidebar badge off one real subscribe/release round trip — no /api/agents refetch', async () => {
    const rows = [agent('cmx300-wired', { window_id: '@9' })];
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
    assert.equal(rowFor('cmx300-wired').querySelector('.ar-role'), null,
        'the row already carried a role badge before ever holding the inbox slot');

    const prevFetch = globalThis.fetch;
    let sawAgentsFetch = false;
    globalThis.fetch = (url, opts) => {
        if (String(url).includes('/api/agents') && !String(url).includes('/api/agents/context')) sawAgentsFetch = true;
        return prevFetch(url, opts);
    };
    try {
        // The ONLY thing that happens next is the real subscribe round trip — no
        // renderSidebarAgents call from the test itself. If onOrchestratorChange's
        // listener body is neutered (dead-coded), the badge never appears.
        await orchestrator.orchestratorSubscribe('@9');

        const badge = rowFor('cmx300-wired').querySelector('.ar-role');
        assert.ok(badge && badge.classList.contains('orchestrator') && badge.querySelector('svg'),
            'the sidebar badge did not update off the real subscribe round trip — onOrchestratorChange is not wired to renderSidebarAgents');
        assert.equal(sawAgentsFetch, false,
            'the badge redraw refetched /api/agents — it must redraw off the already-cached agent list, not re-poll');

        await orchestrator.orchestratorRelease('@9');
        assert.equal(rowFor('cmx300-wired').querySelector('.ar-role'), null,
            'the badge survived orchestratorRelease — onOrchestratorChange did not redraw on release either');
        assert.equal(sawAgentsFetch, false,
            'the release redraw refetched /api/agents — it must redraw off the already-cached agent list, not re-poll');
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test('role colour is colourblind-safe and CASCADES onto the rendered badge — not just present in source', () => {
    // The pre-existing type-cue colour test above is honestly a source-only
    // assertion (there is no rendered node, built from this app's own layout, to
    // read it back from there). This one doesn't have that excuse: it loads the
    // REAL style.css into a fresh jsdom document (the same recipe
    // tests/wire_live_css.test.mjs uses for CMX-120) and reads the CASCADED
    // colour off a REAL `.ar-role` node. Renaming just the selector that binds the
    // colour to the badge (`.ar-role.orchestrator` -> `.ar-role.orchestrator-x`,
    // leaving both hex constants untouched — DEFEAT_SHAPES #05/#54) makes this go
    // red even though a `CSS.includes(hex)` source check stays green.
    const dom = new JSDOM(`<!doctype html><html><head><style>${CSS}</style></head><body>
        <span class="ar-role orchestrator">Orchestrator</span>
        <span class="ar-role dispatched">Dispatched</span>
        <span class="ar-type claude">C</span>
        <span class="ar-type shell">S</span>
        <span class="ar-type server">V</span>
    </body></html>`, { pretendToBeVisual: true });
    const orch = dom.window.document.querySelector('.ar-role.orchestrator');
    const disp = dom.window.document.querySelector('.ar-role.dispatched');
    const orchColor = dom.window.getComputedStyle(orch).color;
    const dispColor = dom.window.getComputedStyle(disp).color;
    assert.equal(orchColor, 'rgb(204, 121, 167)',   // #CC79A7
        'the .ar-role.orchestrator selector no longer paints the rendered badge (colour lost or selector renamed)');
    assert.equal(dispColor, 'rgb(0, 114, 178)',   // #0072B2
        'the .ar-role.dispatched selector no longer paints the rendered badge (colour lost or selector renamed)');

    // ...and distinct from the window-TYPE palette, as this test's own title claims.
    // Read the type colours back off the SAME cascaded document rather than
    // hardcoding their source hexes — otherwise a recoloured .ar-type glyph
    // collides with a role colour in reality while this assertion, comparing
    // against a stale constant, stays green (DEFEAT_SHAPES #70).
    const typeColors = ['claude', 'shell', 'server'].map(cls =>
        dom.window.getComputedStyle(dom.window.document.querySelector(`.ar-type.${cls}`)).color);
    assert.ok(!typeColors.includes(orchColor) && !typeColors.includes(dispColor),
        'a role colour collides with a window-type colour — the two palettes must stay visually distinct');
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

// CMX-302 negative control: the guard above only proves a KNOWN icon name (like
// 'crown') renders non-empty. It says nothing about what happens when a name is
// NOT in _LUCIDE — and `${_LUCIDE[name] || ''}` used to answer that with a
// silently-valid-looking `<svg></svg>` (a real <svg> element, so a bare
// `querySelector('svg')` truthiness check — as the orchestrator-badge test above
// does — can't tell it apart from the real icon). A typo'd or renamed lucide name
// must fail loudly at the call site instead, so the corruption surfaces the moment
// the row renders rather than as a blank badge nobody notices.
test('lucideIcon FAILS LOUDLY for an unknown icon name — it never falls back to an empty <svg>', () => {
    assert.throws(() => util.lucideIcon('not-a-real-lucide-icon'),
        /unknown icon/,
        'lucideIcon silently accepted an icon name that is not in _LUCIDE instead of failing');
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

// CMX-300 rework round 1 (PR #374, judge finding 2): renderAgentDetail (nav.js:623)
// adds a Role row (`['Role', escHtml(_ROLE_LABEL[_agentRole(a)])]`), but nothing in
// this suite ever asserted the string 'Role' or its VALUE anywhere in #agent-detail
// — the two "← Back" tests above only ever read `.detail-back`, so blanking the
// row's value (keeping the label, dropping what it reports) stayed invisible. This
// reads the row's own `.k`/`.v` pair back off the REAL rendered `.detail-grid`, for
// both a plain session and a dispatched one (dispatched doesn't require
// `window_id`, so it's reachable through the same no-window_id FOUND branch the
// test above already establishes — see nav.js:230).
function _detailRowValue(key) {
    const k = [...document.querySelectorAll('#agent-detail .detail-grid .k')].find(el => el.textContent === key);
    return k ? k.nextElementSibling.textContent : undefined;
}

test('the agent-detail panel renders a Role row with the resolved role label — plain', () => {
    nav.renderNav();
    if (!document.getElementById('hdr-next')) document.body.appendChild(document.createElement('span')).id = 'hdr-next';
    if (!document.getElementById('hdr-updated')) document.body.appendChild(document.createElement('span')).id = 'hdr-updated';
    if (!document.getElementById('agent-detail')) document.body.appendChild(document.createElement('div')).id = 'agent-detail';
    util.setAgentsCache([{ name: 'cmx300-detail-plain', online: true }]);
    const prevFetch = globalThis.fetch;
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    try {
        window.chela.selectAgent('cmx300-detail-plain');
    } finally {
        globalThis.fetch = prevFetch;
    }
    assert.equal(_detailRowValue('Role'), 'Plain session',
        'the Role row is missing, or does not report Plain session, for a plain agent-detail panel');
});

test('the agent-detail panel renders a Role row with the resolved role label — dispatched', () => {
    nav.renderNav();
    if (!document.getElementById('hdr-next')) document.body.appendChild(document.createElement('span')).id = 'hdr-next';
    if (!document.getElementById('hdr-updated')) document.body.appendChild(document.createElement('span')).id = 'hdr-updated';
    if (!document.getElementById('agent-detail')) document.body.appendChild(document.createElement('div')).id = 'agent-detail';
    util.setAgentsCache([{ name: 'cmx300-detail-dispatched', online: true, dispatched: true }]);
    const prevFetch = globalThis.fetch;
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    try {
        window.chela.selectAgent('cmx300-detail-dispatched');
    } finally {
        globalThis.fetch = prevFetch;
    }
    assert.equal(_detailRowValue('Role'), 'Dispatched',
        'the Role row is missing, or does not report Dispatched, for a dispatched agent-detail panel');
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
