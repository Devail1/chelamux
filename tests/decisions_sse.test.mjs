// The decisions panel live-updates off the SSE `log` delta — but ONLY while the
// Personas tab is on screen (sse.js::_sseLog -> `if (currentTab === 'personas')
// onDecisionsLogDelta(d)`). orchestrator_ui.test.mjs proves the `orchestrator` frame
// reaches its listener; this proves the `log` frame reaches the DECISIONS panel, and
// that the tab match is real in BOTH directions:
//   - revert `=== 'personas'` (e.g. to `=== 'feed'`) -> a personas-tab log frame no
//     longer refetches /api/log -> test 1 goes red.
//   - drop the tab guard entirely (always route) -> a NON-personas log frame refetches
//     decisions it shouldn't -> test 2 goes red.
// Drives the REAL sse.js through a fake EventSource (jsdom has none), same harness as
// orchestrator_seed.test.mjs / orchestrator_ui.test.mjs.
//
// Run: node --test tests/decisions_sse.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest; needs `npm ci` for jsdom).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const PANEL = `
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
</div>
<div class="panel" id="panel-personas">
  <div id="decisions-chip"></div>
  <div class="decisions-list" id="decisions-list"></div>
</div>`;

const AGENTS = [
    { name: 'shell', window_id: '@1', online: true },
    { name: 'shell', window_id: '@2', online: true },
];
const ORCH_STATUS = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };
const LOG_RESPONSE = { boot_id: 'b1', events: [], gap: null, first_seq: 0, last_seq: 0, next_seq: 0 };

let requests;

function fakeFetch(url) {
    const path = String(url);
    requests.push(path);
    const body =
        path.endsWith('/api/agents') ? AGENTS
            : path.endsWith('/api/agents/context') ? {}
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
                        : path.endsWith('/api/orchestrator/status') ? ORCH_STATUS
                            : path.includes('/api/log') ? LOG_RESPONSE
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

class FakeEventSource {
    constructor(url) { this.url = url; this.listeners = {}; FakeEventSource.last = this; }
    addEventListener(type, cb) { (this.listeners[type] ||= []).push(cb); }
    close() {}
}

let util;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    // Must exist BEFORE main.js's initSSE() runs, on the SAME window it resolves.
    globalThis.EventSource = FakeEventSource;
    dom.window.EventSource = FakeEventSource;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    // Browser load order: index.html -> main.js -> everything else. main.js's top-level
    // initSSE() wires the REAL `log` listener onto our FakeEventSource.
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    util.setAgentsCache(AGENTS);
    // Flush main.js's own load-time fetches (agents/status/seed) so they don't bleed
    // into a per-test `requests` window.
    await new Promise(resolve => setTimeout(resolve, 0));
});

beforeEach(() => { requests = []; });

function dispatchLogFrame() {
    const src = FakeEventSource.last;
    assert.ok(src, 'no EventSource was ever constructed — initSSE() bailed out');
    const handlers = src.listeners['log'];
    assert.ok(handlers && handlers.length > 0,
        'nothing is listening for the `log` SSE event — the live-update claim is dead');
    // The real frame is a NOTIFICATION carrying only the new seq; the reader refetches
    // /api/log from its own cursor.
    handlers.forEach(cb => cb({ data: JSON.stringify({ boot_id: 'b1', seq: 9 }) }));
}

test('a `log` SSE frame refetches the decisions panel WHILE on the Personas tab', async () => {
    util.setCurrentTab('personas');
    dispatchLogFrame();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(requests.some(r => r.includes('/api/log')),
        'a log frame on the Personas tab did NOT refetch /api/log — the tab-gated live path is dead');
});

test('a `log` SSE frame does NOT touch decisions when another tab is active (the tab match is real)', async () => {
    util.setCurrentTab('agents');
    dispatchLogFrame();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(!requests.some(r => r.includes('/api/log')),
        'a log frame refetched decisions while NOT on the Personas tab — the tab gate is missing');
});
