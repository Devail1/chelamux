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
// Run: node --test tests/settings_cost.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = `
<div class="drawer-scrim" id="drawer-scrim" onclick="chela.toggleSettings()"></div>
<div class="settings-modal" id="settings-drawer">
  <nav class="settings-tabs" id="settings-tabs"></nav>
  <div class="settings-tabpanels" id="drawer-body"></div>
</div>`;

const AGENTS = [
    { name: 'chelamux-dev', cwd: '/home/user/projects/chelamux' },
    { name: 'nautilus-hub', cwd: '/home/user/projects/nautilus' },
];
const COST_LIVE = [
    { name: 'chelamux-dev', model: 'sonnet', cost_usd: 1.5 },
    { name: 'nautilus-hub', model: 'opus', cost_usd: 2.25 },
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
    assert.match(table.textContent, /chelamux/, 'the chelamux project (from /api/agents\' cwd) is missing from the table');
    assert.match(table.textContent, /nautilus/, 'the nautilus project is missing from the table');
    assert.match(table.textContent, /\$1\.50/, 'the per-agent cost is missing/wrong');
    assert.match(table.textContent, /\$3\.75/, 'the fleet total (1.50 + 2.25) is missing/wrong');

    fetchCalls = [];
    costPayload = [{ name: 'chelamux-dev', model: 'sonnet', cost_usd: 9 }];
    await window.chela.setCostWindow('7d');

    const call = fetchCalls.find(c => c.url.startsWith('/api/cost'));
    assert.ok(call, 'setCostWindow("7d") did not re-fetch /api/cost');
    assert.match(call.url, /window=7d/, 'the window change was not threaded into the /api/cost query');
    assert.match(document.getElementById('cost-table').textContent, /\$9\.00/, 'the table did not re-render for the new window');
});
