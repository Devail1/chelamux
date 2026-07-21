// The Decisions sidebar section live-updates off the SSE `log` delta — and, since
// cmx-107 moved it out of the Personas VIEW into an always-visible sidebar section
// (index.html #side-decisions), that repaint is now TAB-INDEPENDENT (sse.js::_sseLog
// calls `onDecisionsLogDelta(d)` unconditionally, no `currentTab` gate). Before
// cmx-107 this suite proved the opposite — that a `log` frame only reached the
// panel while the Personas tab was active, and NOT otherwise. That gate is gone by
// design: the whole point of the sidebar relocation is that Decisions is always on
// screen, so it must always live-update, not just while some particular tab happens
// to be open.
//
// Both tests below assert the delta reaches /api/log with NO tab gate at all:
//   - fires while an unrelated tab ('agents') is active
//   - fires while the (now decisions-free) Personas tab is active too
// Reintroducing any `currentTab === '...'` guard around `onDecisionsLogDelta` in
// sse.js's `_sseLog` makes one of these red.
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
<div class="panel" id="panel-personas"></div>
<section class="side-section" id="side-decisions">
  <div id="decisions-chip"></div>
  <div class="decisions-list" id="decisions-list"></div>
</section>`;

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
    // Flush main.js's own load-time fetches (agents/status/decisions seed) so they
    // don't bleed into a per-test `requests` window.
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

test('a `log` SSE frame refetches Decisions while an UNRELATED tab is active (no tab gate)', async () => {
    util.setCurrentTab('agents');
    dispatchLogFrame();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(requests.some(r => r.includes('/api/log')),
        'a log frame on a non-Personas tab did not refetch /api/log — Decisions is ' +
        'supposed to be tab-independent now (cmx-107), not gated to one view');
});

test('a `log` SSE frame refetches Decisions while the Personas tab is active too', async () => {
    util.setCurrentTab('personas');
    dispatchLogFrame();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(requests.some(r => r.includes('/api/log')),
        'a log frame on the Personas tab did not refetch /api/log');
});
