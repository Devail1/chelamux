// SETTINGS MODAL — tabs + the revived "Cost" tab (CMX-287), in a REAL DOM — same
// idiom as tests/settings_dispatch.test.mjs: run the real nav.js (via main.js,
// same module-graph-import approach) against mocked /api/agents + /api/cost and
// assert what renderSettings()/selectSettingsTab()/cost.js's refreshCost()
// actually produce in the DOM, not a source grep.
//
// Two properties:
//
//   1. 🔴 TAB SWITCHING SHOWS EXACTLY ONE PANEL. The drawer->modal rewrite's
//      whole point is per-tab panels instead of one long scroll — if
//      selectSettingsTab() stops toggling the `.active` class (or the CSS
//      selector drifts), every panel would render at once, silently reverting
//      to the old one-scroll drawer with extra chrome.
//   2. 🔴 THE COST TAB JOINS /api/agents (for cwd -> project) WITH /api/cost
//      (for cost_usd), GROUPED BY PROJECT, and re-fetches on window change. A
//      revival that renders an empty table (or never re-fetches on Today/7d/
//      30d) is decoration, not a working Cost tab.
//
// CMX-287 rework round 2 (PR #358): the judge mutated cost.js's join input
// (`_agentProject({ cwd: cwdByName[c.name] })` -> `_agentProject({ cwd: '' })`,
// which collapses every agent into the "(unknown)" project) and the suite
// stayed green — DEFEAT_SHAPES #33: the fixture's agent names ('chelamux-dev',
// 'nautilus-hub') already CONTAIN the project names ('chelamux', 'nautilus') as
// substrings, so `assert.match(table.textContent, /chelamux/)` kept matching
// against the agent-name cell even after the project cell fell back to
// "(unknown)" for both rows. Fixed by naming agents so neither name shares a
// substring with either project, and by pinning the exact project-row text
// (not just "is this substring anywhere in the table").
//
// Also closes DEFEAT_SHAPES #34 here: the fixture used to hand-type its own
// `<nav id="settings-tabs">`/`<div id="drawer-body">` markup instead of the
// real chela/dashboard/templates/index.html, so a template mutation (e.g.
// renaming #settings-tabs) went uncaught — the hand copy just kept agreeing
// with itself. Sliced from the real template now, same idiom as
// tests/dashboard_default_view.test.mjs.
//
// Run: node --test tests/settings_cost.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

// The REAL settings modal — scrim + tab rail + tabpanel host — sliced straight
// out of index.html, not hand-typed, so a mutation to the tab rail's id (the
// shape the judge found: `id="settings-tabs"` -> `id="settings-tabs-reverted"`)
// shows up here instead of only in a fixture that happens to still agree.
const SETTINGS_START = HTML.indexOf('<div class="drawer-scrim" id="drawer-scrim"');
const SETTINGS_END = HTML.indexOf('<!-- "+ new" popover');
if (SETTINGS_START < 0 || SETTINGS_END < 0) throw new Error('index.html markers for the settings modal moved — update this test');
const BODY = HTML.slice(SETTINGS_START, SETTINGS_END);

// Agent names deliberately share NO substring with either project name below
// ('chela-fleet' project is 'chelamux', 'nautilus-book' project is 'nautilus')
// — see DEFEAT_SHAPES #33. If a project name ever needs to appear verbatim in
// an agent name, the join assertions below must stop relying on `textContent`
// substring matches and pin the project-row cell specifically instead.
const AGENTS = [
    { name: 'runner-east', cwd: '/home/user/projects/chelamux' },
    { name: 'runner-west', cwd: '/home/user/projects/nautilus' },
];
const COST_LIVE = [
    { name: 'runner-east', model: 'sonnet', cost_usd: 1.5 },
    { name: 'runner-west', model: 'opus', cost_usd: 2.25 },
];

let costPayload = COST_LIVE;
let fetchCalls = [];

function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.fetch = (url, opts) => {
        const u = String(url);
        fetchCalls.push({ url: u, opts: opts || null });
        if (u.startsWith('/api/agents')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(AGENTS) });
        }
        if (u.startsWith('/api/cost')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(costPayload) });
        }
        // Every other settings fetch (/api/config, /api/settings, .../timing,
        // .../dispatch, ...): an empty 200 keeps renderSettings() from throwing.
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(() => {
    document.getElementById('settings-drawer').classList.remove('open');
    localStorage.removeItem('chela_cost_window');
    costPayload = COST_LIVE;
    fetchCalls = [];
});

async function openOnTab(tab) {
    window.chela.toggleSettings();
    await flush();
    window.chela.selectSettingsTab(tab);
    await flush();
    await flush();   // refreshCost()'s Promise.all
}

// --- 1. 🔴 tab switching shows exactly one panel -------------------------------

test('selecting a tab shows only that tab\'s panel and marks it active in the rail', async () => {
    await openOnTab('cost');

    const panels = document.querySelectorAll('.settings-tabpanel');
    assert.ok(panels.length >= 2, 'renderSettings() did not wrap sections into per-tab panels');
    const activePanels = document.querySelectorAll('.settings-tabpanel.active');
    assert.equal(activePanels.length, 1, 'more than one settings panel is active at once');
    assert.equal(activePanels[0].dataset.tab, 'cost', 'the Cost panel is not the active one after selectSettingsTab("cost")');

    const activeTabs = document.querySelectorAll('.settings-tab.active');
    assert.equal(activeTabs.length, 1, 'more than one tab rail entry is marked active');
    assert.equal(activeTabs[0].dataset.tab, 'cost', 'the Cost rail entry is not marked active');

    window.chela.selectSettingsTab('timing');
    const nowActive = document.querySelectorAll('.settings-tabpanel.active');
    assert.equal(nowActive.length, 1);
    assert.equal(nowActive[0].dataset.tab, 'timing', 'switching tabs did not move the active panel to Timing');
});

// --- 2. 🔴 the Cost tab joins agents + cost by project, and re-fetches on window change --

test('the Cost tab renders a project-grouped table joined from /api/agents + /api/cost', async () => {
    await openOnTab('cost');

    assert.ok(fetchCalls.some(c => c.url.startsWith('/api/agents')), 'the Cost tab never fetched /api/agents');
    assert.ok(fetchCalls.some(c => c.url.startsWith('/api/cost')), 'the Cost tab never fetched /api/cost');

    const table = document.getElementById('cost-table');
    assert.ok(table.querySelector('table.cost-table'), 'no cost table rendered');

    // Pin the PROJECT-ROW cells specifically (not "is this text anywhere in
    // the table") — the agent names deliberately don't contain either project
    // name (DEFEAT_SHAPES #33), so this can only pass if _agentProject() was
    // actually driven off /api/agents' cwd, not off the agent name/row order.
    const projectRowNames = Array.from(table.querySelectorAll('.cost-project-row td:first-child'))
        .map(td => td.textContent.trim());
    assert.deepEqual(projectRowNames.sort(), ['chelamux', 'nautilus'],
        'the project rows are not exactly {chelamux, nautilus}, joined from /api/agents\' cwd');

    const agentRowNames = Array.from(table.querySelectorAll('.cost-agent-row td:nth-child(2)'))
        .map(td => td.textContent.trim());
    assert.deepEqual(agentRowNames.sort(), ['runner-east', 'runner-west'],
        'the agent rows do not list the raw agent names from /api/cost');

    assert.match(table.textContent, /\$1\.50/, 'the per-agent cost is missing/wrong');
    assert.match(table.textContent, /\$3\.75/, 'the fleet total (1.50 + 2.25) is missing/wrong');

    fetchCalls = [];
    costPayload = [{ name: 'runner-east', model: 'sonnet', cost_usd: 9 }];
    await window.chela.setCostWindow('7d');

    const call = fetchCalls.find(c => c.url.startsWith('/api/cost'));
    assert.ok(call, 'setCostWindow("7d") did not re-fetch /api/cost');
    assert.match(call.url, /window=7d/, 'the window change was not threaded into the /api/cost query');
    assert.match(document.getElementById('cost-table').textContent, /\$9\.00/, 'the table did not re-render for the new window');
});
