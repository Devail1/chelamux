// main.js's INITIAL SEED of the Decisions sidebar section — the page-load half of
// cmx-107 ("always visible", not gated behind opening the Personas tab).
//
// decisions_sse.test.mjs proves the SSE `log`-delta repaint path is tab-independent.
// It does NOT prove main.js's own top-level `enterDecisions()` call (main.js) ever
// fires: that suite's `before()` already flushes one microtask turn before each test
// runs, so a mutation that drops the seed call entirely (e.g. `enterDecisions();` ->
// `void 0;`) would still leave that suite green — nothing there asserts the section
// is non-empty BEFORE any SSE frame or tab-open.
//
// Here the server already has one decision logged BEFORE the page even loads (as it
// would for any real page load — the log is durable, dispatch has been running).
// Nothing in this test opens the Personas tab, dispatches an SSE frame, or calls
// enterDecisions/tickDecisions itself — the only thing that can paint a row into
// #decisions-list is main.js's own unconditional seed call. Drop that call and this
// test is the one that goes red.
//
// Run: node --test tests/decisions_seed.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest; needs `npm ci` for jsdom).
import { before, test } from 'node:test';
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
// A decision already in the durable log BEFORE this page loads — as it always is
// once chela/inbox.py has queued/logged anything at all.
const LOG_RESPONSE = {
    boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
    events: [{ seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-9 awaiting review', payload: {} }],
};

function fakeFetch(url) {
    const path = String(url);
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
    constructor(url) { this.url = url; this.listeners = {}; }
    addEventListener(type, cb) { (this.listeners[type] ||= []).push(cb); }
    close() {}
}

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
    globalThis.EventSource = FakeEventSource;
    dom.window.EventSource = FakeEventSource;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    // Same load order as the browser (index.html -> main.js -> everything else).
    // currentTab defaults to 'agents' (util.js) and main.js's top-level
    // selectView('terminals') only changes it to 'terminals' — never 'personas' —
    // so nothing here ever opens the tab that used to gate this render pre-cmx-107.
    await import('../chela/dashboard/static/js/main.js');

    // Flush the seed fetch's microtask chain (fetch -> res.json() -> render)
    // without dispatching any SSE frame or opening any tab.
    await new Promise(resolve => setTimeout(resolve, 0));
    await new Promise(resolve => setTimeout(resolve, 0));
});

test('main.js seeds the Decisions sidebar section on load, with no tab-open and no SSE frame', () => {
    const rows = document.querySelectorAll('#decisions-list .feed-row');
    assert.equal(rows.length, 1,
        'the durable log already had a decision logged before page load, but the ' +
        'sidebar Decisions section never rendered it — main.js\'s load-time ' +
        'enterDecisions() call never ran (or its result was discarded)');
    assert.ok(rows[0].textContent.includes('cmx-9 awaiting review'));
});
