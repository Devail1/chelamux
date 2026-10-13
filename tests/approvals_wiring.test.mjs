// ⚖️🔌 CMX-103 wiring: the REAL page runs the Pending-approvals surface. approvals.test.mjs
// proves the module's logic in isolation; this loads main.js (the browser's entry point,
// harness copied from update_banner_wiring.test.mjs) and proves each hop that connects it:
//
//   1. 🔴 main.js's refresh() poll calls tickApprovals() — the pill shows on EVERY tab with
//      its count, before anyone opens anything. Drop that call and the pill never appears.
//   2. 🔴 the pill markup index.html actually ships opens the sheet — its inline onclick
//      reaches `chela.openApprovals`. The markup is read from the template, not retyped.
//   3. 🔴 an SSE `overrides` frame re-fetches /api/overrides (sse.js) — and updates the
//      count — between refresh() ticks.
//   4. 🔴 Settings → Notifications' "Approval requests" select reaches setApprovalNotify:
//      choosing "Browser notification" stores the opt-in, and the drawer re-renders it
//      selected; choosing "Off" clears it.
//
// runScripts:'dangerously' is ONLY so jsdom compiles inline on* attributes (2, 4) — the
// page carries no <script> of its own.
//
// Run: node --test tests/approvals_wiring.test.mjs (tests/test_js_suites.py runs it).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const TEMPLATE = readFileSync(new URL('../chela/dashboard/templates/index.html', import.meta.url), 'utf8');
const PILL = TEMPLATE.match(/<button[^>]*id="btn-approvals"[\s\S]*?<\/button>/)[0];

const PANEL = `
<header><span id="hdr-agents"></span><span id="hdr-next"></span><span id="hdr-updated"></span></header>
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
</section>
<aside class="drawer" id="settings-drawer"><div class="drawer-body" id="drawer-body"></div></aside>
<div class="safety-float">${PILL}</div>`;

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

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true, runScripts: 'dangerously' });
    dom.window.TERMINALS_ENABLED = true;
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

    await import('../chela/dashboard/static/js/main.js');
    // refresh() awaits a chain of polls before tickApprovals(); let them settle. Only the
    // page's own load is allowed to show the pill — nothing below has run yet.
    for (let i = 0; i < 40 && document.getElementById('btn-approvals').hidden; i++) await flush();
    pillShownByRefresh = !document.getElementById('btn-approvals').hidden;
});

test('the page\'s own refresh poll shows the pill with the pending count', () => {
    const pill = document.getElementById('btn-approvals');
    assert.ok(pillShownByRefresh, 'refresh() never ran tickApprovals() — no pill on page load');
    assert.match(pill.textContent, /1 pending approval\b/);
});

test('the pill index.html ships opens the Pending approvals sheet', async () => {
    document.getElementById('btn-approvals').click();
    for (let i = 0; i < 10 && !document.getElementById('approvals-backdrop'); i++) await flush();
    const sheet = document.getElementById('approvals-backdrop');
    assert.ok(sheet, 'clicking the shipped pill did not open the sheet');
    assert.equal(sheet.querySelectorAll('.pa-row').length, 1);
    window.chela.closeApprovals();
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
    let sel = document.getElementById('approval-notify-select');
    assert.ok(sel, 'Settings has no "Approval requests" select');
    assert.equal(sel.value, 'off', 'approval notifications must be OFF by default');

    sel.value = 'on';
    sel.dispatchEvent(new window.Event('change'));
    await flush();
    assert.equal(localStorage.getItem(KEY), '1', 'choosing "Browser notification" did not opt in');

    window.chela.toggleSettings();     // close
    window.chela.toggleSettings();     // re-open → re-render from storage
    await flush();
    sel = document.getElementById('approval-notify-select');
    assert.equal(sel.value, 'on', 'the drawer does not show the stored opt-in');

    sel.value = 'off';
    sel.dispatchEvent(new window.Event('change'));
    await flush();
    assert.equal(localStorage.getItem(KEY), null, 'choosing "Off" did not clear the opt-in');
    window.chela.toggleSettings();
});
