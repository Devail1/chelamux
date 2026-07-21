// main.js's INITIAL SEED of orchestrator state — the other half of "the pane
// buttons reflect the real owner without waiting on a tab-open or an SSE delta."
//
// orchestrator_ui.test.mjs proves the SSE-delta repaint path (a frame arrives ->
// buttons repaint). It does NOT prove main.js's own top-level `refreshOrchestrator
// Status()` call (main.js:57) ever fires: that suite starts /api/orchestrator/status
// at the default "nobody owns it" value, so a mutation that drops the seed call
// entirely (e.g. `refreshOrchestratorStatus();` -> `void 0;`) changes nothing there
// — the buttons would already read "off" either way.
//
// Here the server already reports an OWNER before the page even loads (as it would
// for a client that (re)connects after another pane already took over). Nothing in
// this test opens a tab, dispatches an SSE frame, or calls refreshOrchestratorStatus
// itself — the only thing that can paint pane @2's button "on" is main.js's own
// unconditional seed call. Drop that call and this test is the one that goes red.
//
// Run: node --test tests/orchestrator_seed.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest; needs `npm ci` for jsdom).
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
</div>`;

const AGENTS = [
    { name: 'shell', window_id: '@1', online: true },
    { name: 'shell', window_id: '@2', online: true },
];

// The server already reports @2 as the live owner BEFORE the page loads — as if
// another pane had taken over earlier and this is a fresh page load/reconnect.
const ORCH_STATUS = { wid: '@2', name: 'agent-b', state: 'ok', why: '', queued: 0 };

function fakeFetch(url) {
    const path = String(url);
    const body =
        path.endsWith('/api/agents') ? AGENTS
            : path.endsWith('/api/agents/context') ? {}
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
                        : path.endsWith('/api/orchestrator/status') ? ORCH_STATUS
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

let orchestrator;

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
    // main.js's top-level selectView('terminals') renders the wall with whatever
    // orchestrator state is cached at that instant (still the pre-fetch default —
    // it has not resolved yet), and main.js's own trailing refreshOrchestratorStatus()
    // call is what must repaint it afterward, with nothing else in this test
    // triggering a second read.
    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    orchestrator = await import('../chela/dashboard/static/js/orchestrator.js');
    util.setAgentsCache(AGENTS);

    // Flush the seed fetch's microtask chain (fetch -> res.json() -> _apply ->
    // listeners) without dispatching any SSE frame or opening any tab.
    await new Promise(resolve => setTimeout(resolve, 0));
    await new Promise(resolve => setTimeout(resolve, 0));
});

test('main.js seeds orchestrator state on load, with no tab-open and no SSE frame', () => {
    assert.equal(orchestrator.orchestratorState().wid, '@2',
        'the page loaded with @2 already the live owner, but shared orchestrator ' +
        'state was never seeded — refreshOrchestratorStatus() on main.js\'s load ' +
        'path never ran (or its result was discarded)');
});
