// CMX-426 wiring: main.js's refresh() poll must actually ASK the server for its version
// and raise the "chela was updated — Reload" banner. update_banner.test.mjs proves the
// banner logic; this proves the real page runs it — drop the checkForUpdate() call from
// refresh() and the banner never appears here. Harness copied from decisions_seed.test.mjs.
//
// Run: node --test tests/update_banner_wiring.test.mjs (tests/test_js_suites.py runs it).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const PAGE_VERSION = 'aaaaaaaaaaaa';     // what index.html was rendered with
const SERVER_VERSION = 'bbbbbbbbbbbb';   // what the restarted dashboard now reports

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
                                : path.endsWith('/api/version') ? { version: SERVER_VERSION }
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
    dom.window.CHELA_ASSET_VERSION = PAGE_VERSION;
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

    await import('../chela/dashboard/static/js/main.js');
    // refresh() awaits a chain of polls before the version check; let them all settle.
    for (let i = 0; i < 20 && !document.getElementById('update-banner'); i++) {
        await new Promise(resolve => setTimeout(resolve, 5));
    }
});

test('the page\'s own refresh poll raises the reload banner after a deploy', () => {
    const el = document.getElementById('update-banner');
    assert.ok(el, 'refresh() never checked /api/version — no banner after a deploy');
    assert.equal(el.hidden, false);
    assert.match(el.textContent, /chela was updated/);
});
