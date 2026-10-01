// CMX-418 — the pane header's "🌐 web" chip follows /api/agents .share_net.
//
// Runs the REAL main.js/nav.js/terminals.js in jsdom (same harness as
// tests/window_id.test.mjs) over a fleet of three windows: one launched with web
// access, one sandboxed without it, one not sandboxed at all.
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   1. after the first render + status seed, ONLY the web window's chip is visible —
//      the chip is driven by the poll's share_net, not by a constant;
//   2. a later poll that flips share_net moves the chip both ways (gain AND loss);
//   3. a freshly re-rendered pane header reads the seeded mode (no flash of the wrong
//      state until the next poll).
// The judge's round-2 survivor: `if (a.share_net) _netModes.set(…)` → `if (false)`
// stayed green because nothing rendered a fleet carrying share_net at all.
//
// Run: node --test tests/share_web_chip.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const PANEL = `
<div id="sidebar-agents"></div>
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button>
  <div id="term-switcher"></div>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>`;

const AGENTS = [
    { name: 'aaron', window_id: '@7', online: true, cwd: '/p/aaron', share_net: 'web' },
    { name: 'boxed', window_id: '@8', online: true, cwd: '/p/boxed', share_net: 'none' },
    { name: 'plain', window_id: '@9', online: true, cwd: '/p/plain', share_net: null },
];

function fakeFetch(url) {
    const path = String(url);
    const body =
        path.endsWith('/api/agents') ? AGENTS
            : path.endsWith('/api/agents/context') ? []
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
                        : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {}, removeWidget(el) { el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let terminals, util;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.HTMLElement.prototype.scrollIntoView = function () {};
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

const chip = wid => document.querySelector(
    `#term-stage .grid-stack-item[gs-id="${wid}"] .gs-net-badge[data-net-for="${wid}"]`);
const shown = () => ['@7', '@8', '@9'].filter(w => chip(w) && !chip(w).hidden);

test('every pane header carries a chip slot (the harness is not empty)', () => {
    for (const wid of ['@7', '@8', '@9']) assert.ok(chip(wid), `no web chip slot for ${wid}`);
    assert.match(chip('@7').textContent, /web/);
});

test('only the window launched with web access shows the 🌐 web chip', () => {
    assert.deepEqual(shown(), ['@7']);
});

test('a poll that flips share_net moves the chip both ways', async () => {
    AGENTS[0].share_net = 'none';
    AGENTS[1].share_net = 'web';
    try {
        await terminals.termTick();
        assert.deepEqual(shown(), ['@8']);
    } finally {
        AGENTS[0].share_net = 'web';
        AGENTS[1].share_net = 'none';
    }
    await terminals.termTick();
    assert.deepEqual(shown(), ['@7']);
});

test('a re-rendered pane header reads the seeded mode before any poll', async () => {
    await terminals.renderTerminals();
    assert.deepEqual(shown(), ['@7']);
});
