// ⚖️🔌 CMX-103 wiring: the REAL page runs the Pending-approvals surface. The approvals island's
// own tests (frontend/src/features/approvals/tests) prove its logic; this boots the page (the
// islands, then the legacy graph, as index.html loads them) and proves each hop between them:
//
//   1. 🔴 the refresh loop runs the approvals lane: the pill shows on EVERY view with its
//      count, before anyone opens anything.
//   2. 🔴 the pill, in the slot index.html actually ships, opens the sheet. The slot is read
//      from the template, not retyped.
//   3. 🔴 an SSE `overrides` frame re-fetches /api/overrides, and updates the count, between
//      refresh ticks.
//   4. 🔴 Settings → Notifications' "Approval requests" select (features/settings) reaches
//      setApprovalNotify: choosing "Browser notification" stores the opt-in, and the drawer
//      re-renders it selected; choosing "Off" clears it.
//
// runScripts:'dangerously' is ONLY so jsdom compiles inline on* attributes (4): the page
// carries no <script> of its own.
//
// Run: pnpm exec vitest run tests/approvals_wiring.test.mjs (tests/test_js_suites.py runs it).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { exposeDomConstructors, mountIslands, pickOption } from './js_helpers/dashboard_dom.mjs';

// The pill's shell slot: mountIslands draws it from the shell's own table (features/shell).
const PILL = '<span class="island-slot" id="approvals-island"></span>';

const PANEL = `
<header><span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span></header>
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
</section>
<aside class="drawer" id="settings-drawer"><div class="drawer-body" id="drawer-body"></div></aside>
<div class="safety-float">${PILL}</div>
<div id="overlays-island"></div>`;

const AGENTS = [{ name: 'shell', window_id: '@1', online: true }];
const ORCH_STATUS = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };
const LOG_RESPONSE = { boot_id: 'b1', events: [], gap: null, first_seq: 0, last_seq: 0, next_seq: 0 };

const req = (id) => ({ id, kind: 'override', task_id: 't1', label: 'cmx-1', head_sha: 'b'.repeat(40),
    judge_state: 'blocked', base: 'dev', reason: 'judge flaked', actor: 'orchestrator',
    age_s: 60, expires_in_s: 3600, blocked: null });
let LISTING = { requests: [req('override-' + 'a'.repeat(24))] };
let requests = [];

function fakeFetch(url) {
    const path = String(url);
    requests.push(path);
    const body =
        path.endsWith('/api/overrides') ? LISTING
            : path.endsWith('/api/agents') ? AGENTS
                : path.endsWith('/api/agents/context') ? []
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

const flush = () => new Promise(resolve => setTimeout(resolve, 5));
let pillShownByRefresh = false;

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true, runScripts: 'dangerously' });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
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
    // refresh() awaits a chain of polls before tickApprovals(); let them settle. Only the
    // page's own load is allowed to show the pill — nothing below has run yet.
    const shown = () => document.getElementById('btn-approvals')?.hidden === false;
    for (let i = 0; i < 40 && !shown(); i++) await flush();
    pillShownByRefresh = shown();
});

test('the page\'s own refresh poll shows the pill with the pending count', () => {
    const pill = document.getElementById('btn-approvals');
    assert.ok(pillShownByRefresh, 'the refresh loop never ran the approvals lane: no pill on page load');
    assert.match(pill.textContent, /1 pending approval\b/);
});

test('the pill index.html ships opens the Pending approvals sheet', async () => {
    document.getElementById('btn-approvals').click();
    for (let i = 0; i < 10 && !document.querySelector('.approvals-sheet'); i++) await flush();
    const sheet = document.querySelector('.approvals-sheet');
    assert.ok(sheet, 'clicking the shipped pill did not open the sheet');
    assert.equal(sheet.querySelectorAll('.pa-row').length, 1);
    sheet.querySelector('.ss-close').click();
    await flush();
    assert.equal(document.querySelector('.approvals-sheet'), null, 'the sheet\'s close button did not close it');
});

test('an SSE `overrides` frame re-fetches the list and updates the count', async () => {
    const src = FakeEventSource.last;
    assert.ok(src, 'no EventSource was ever constructed — initSSE() bailed out');
    const handlers = src.listeners['overrides'];
    assert.ok(handlers && handlers.length, 'nothing listens for the `overrides` SSE frame');
    LISTING = { requests: [req('override-' + 'a'.repeat(24)), req('override-' + 'c'.repeat(24))] };
    requests = [];
    handlers.forEach(cb => cb({ data: JSON.stringify({ pending: 2 }) }));
    await flush();
    assert.ok(requests.some(p => p.endsWith('/api/overrides')), 'the frame did not re-fetch /api/overrides');
    assert.match(document.getElementById('btn-approvals').textContent, /2 pending approvals/);
});

test('the Settings select opts in and out of approval notifications', async () => {
    class FakeNotification {
        static permission = 'granted';
        static requestPermission() { return Promise.resolve('granted'); }
    }
    window.Notification = FakeNotification;
    const KEY = 'chela_approval_notify';
    localStorage.removeItem(KEY);

    window.chela.toggleSettings();
    await flush();
    const sel = () => document.getElementById('approval-notify-select');
    assert.ok(sel(), 'Settings has no "Approval requests" select');
    assert.equal(sel().textContent, 'Off', 'approval notifications must be OFF by default');

    await pickOption(sel(), 'Browser notification');
    await flush();
    assert.equal(localStorage.getItem(KEY), '1', 'choosing "Browser notification" did not opt in');

    window.chela.toggleSettings();     // close
    await flush();
    window.chela.toggleSettings();     // re-open: re-render from storage
    await flush();
    assert.equal(sel().textContent, 'Browser notification', 'the drawer does not show the stored opt-in');

    await pickOption(sel(), 'Off');
    await flush();
    assert.equal(localStorage.getItem(KEY), null, 'choosing "Off" did not clear the opt-in');
    window.chela.toggleSettings();
});
