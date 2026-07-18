// THE PANE-TITLE TOGGLE, WIRED END TO END — the two properties orchestrator.test.mjs
// (client-side state only) and test_api_orchestrator.py (server routes only) cannot
// prove between them: that the "⊙ Orchestrator" button actually RENDERS in the wall's
// real paneHead markup, and that the SSE `orchestrator` delta actually drives a live
// repaint of that button without waiting on a poll.
//
// A prior round of this PR passed CI with both call-sites silently dead: paneHead's
// `${_orchBtnHTML(wid)}` swapped for `${''}` (no button, ever) and sse.js's listener
// registered on `'xorchestrator'` instead of `'orchestrator'` (SSE frame arrives, never
// heard). Nothing in the suite rendered a real pane or drove a real EventSource, so
// both mutations left every test green. This suite runs the REAL renderTerminals() in a
// REAL DOM (jsdom) and drives the REAL sse.js through a fake EventSource, so either
// mutation goes red here.
//
// Run: node --test tests/orchestrator_ui.test.mjs (tests/test_js_suites.py runs every
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
</div>`;

const AGENTS = [
    { name: 'shell', window_id: '@1', online: true },
    { name: 'shell', window_id: '@2', online: true },
];

// The one fact the SSE test flips mid-suite: what /api/orchestrator/status reports.
let ORCH_STATUS = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };

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

// A minimal EventSource stand-in: jsdom has none, so without this initSSE() no-ops
// entirely (`if (!window.EventSource) return;`) and the listener under test never
// gets registered — a vacuous pass, not a proof. Recording every addEventListener
// call lets the test dispatch a same-named fake frame exactly as the browser would.
class FakeEventSource {
    constructor(url) {
        this.url = url;
        this.listeners = {};
        FakeEventSource.last = this;
    }
    addEventListener(type, cb) {
        (this.listeners[type] ||= []).push(cb);
    }
    close() {}
}

let terminals, orchestrator;

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
    // initSSE() guards on `window.EventSource` (not the bare global), so this must land
    // on the SAME window object main.js's `window` resolves to — must exist BEFORE
    // main.js's initSSE() runs.
    globalThis.EventSource = FakeEventSource;
    dom.window.EventSource = FakeEventSource;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    // Same cyclic-import order as the browser (index.html -> main.js -> everything
    // else) — main.js's top-level initSSE() is what wires the real `orchestrator`
    // listener onto our FakeEventSource.
    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    orchestrator = await import('../chela/dashboard/static/js/orchestrator.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();   // ONE wall, built once — as in a live session
});

const orchBtn = wid => document.querySelector(
    `#term-stage .grid-stack-item[gs-id="${wid}"] .gs-orch-btn`);

// --- 1. WIRING: the button is actually part of paneHead's real markup -----------

test('the "⊙ Orchestrator" toggle renders in every pane\'s real head, not just in theory', () => {
    for (const wid of ['@1', '@2']) {
        const btn = orchBtn(wid);
        assert.ok(btn, `pane ${wid} has no .gs-orch-btn — the pane-title toggle never rendered`);
        assert.equal(btn.getAttribute('data-wid'), wid);
    }
});

// --- 2. WIRING: the SSE `orchestrator` delta actually reaches the listener ------

test('an SSE "orchestrator" frame repaints the pane buttons live, with no poll', async () => {
    const source = FakeEventSource.last;
    assert.ok(source, 'no EventSource was ever constructed — initSSE() bailed out');
    const handlers = source.listeners['orchestrator'];
    assert.ok(handlers && handlers.length > 0,
        'nothing is listening for the "orchestrator" SSE event — the live-update claim is dead');

    assert.equal(orchestrator.orchestratorState().wid, null, 'sanity: nobody owns the slot yet');
    assert.equal(orchBtn('@2').classList.contains('on'), false, 'sanity: @2 is not painted as owner yet');

    // A takeover happened server-side; the frame that announces it carries no
    // payload (sse.js refetches /api/orchestrator/status), exactly like the real one.
    ORCH_STATUS = { wid: '@2', name: 'agent-b', state: 'ok', why: '', queued: 0 };
    handlers.forEach(cb => cb({}));
    // Flush the refetch's microtask chain (fetch -> res.json() -> _apply -> listeners).
    await new Promise(resolve => setTimeout(resolve, 0));

    assert.equal(orchestrator.orchestratorState().wid, '@2',
        'the SSE frame fired but the shared orchestrator state never updated');
    assert.equal(orchBtn('@2').classList.contains('on'), true,
        'the SSE frame fired but pane @2\'s button was never repainted "on"');
    assert.equal(orchBtn('@1').classList.contains('on'), false,
        'pane @1 must not also show as owner');
});
