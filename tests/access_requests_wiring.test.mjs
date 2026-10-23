// 🙋 CMX-7 wiring: the refresh loop must actually ASK the server for the guests' access
// requests, so the pending pill (#btn-access-req, the ONLY way to open the sheet) ever shows.
// The access-request island's own tests (frontend/src/features/accessreq/tests) prove the pill
// logic; this proves the real page runs it: drop the lane and the pill never shows here.
// Harness copied from update_banner_wiring.test.mjs.
//
// Run: pnpm exec vitest run tests/access_requests_wiring.test.mjs (tests/test_js_suites.py runs it).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { bootBlock, exposeDomConstructors, mountIslands } from './js_helpers/dashboard_dom.mjs';

const PAGE_VERSION = 'aaaaaaaaaaaa';
const ACCESS = { share_typing: true, default_minutes: 60, requests: [
    { id: 'abcdefabcdef-0', sid: 'abcdefabcdef', kind: 'mount', target: '/srv/data', access: 'ro',
      status: 'pending', description: 'mount /srv/data (read-only)', refusal: null },
] };

const PANEL = `
<span class="island-slot" id="access-req-island"></span>
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button>
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
            : path.endsWith('/api/agents/context') ? []
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
                        : path.endsWith('/api/orchestrator/status') ? ORCH_STATUS
                            : path.includes('/api/log') ? LOG_RESPONSE
                                : path.endsWith('/api/version') ? { version: PAGE_VERSION }
                                : path.endsWith('/api/share-requests') ? ACCESS
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

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${bootBlock({ assetVersion: PAGE_VERSION })}${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
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

    exposeDomConstructors(dom.window);
    await mountIslands(dom.window);
    (await import('../chela/dashboard/frontend/src/boot.ts')).start();
    // the boot's refresh runs every lane; let them all settle
    for (let i = 0; i < 40 && document.getElementById('btn-access-req')?.hidden !== false; i++) {
        await new Promise(resolve => setTimeout(resolve, 5));
    }
});

test("the page's own refresh poll shows the pending access-request pill", () => {
    const pill = document.getElementById('btn-access-req');
    assert.equal(pill.hidden, false, 'the refresh loop never polled /api/share-requests: the pill never shows');
    assert.match(pill.textContent, /1 access request\b/);
});
