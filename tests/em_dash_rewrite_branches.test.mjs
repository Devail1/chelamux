// CMX-160 rewrote the copy on two dashboard lines that sit behind a condition no test had
// covered. These pin both conditions, so the rewrite can't hide a dropped branch:
//   1. agents.js: a context reading that is a transcript ESTIMATE gets the
//      `chela install-statusline` hint in its tooltip, and an exact reading does not.
//   2. dispatcher.js: seeding a path that is NOT a git repo warns that the dispatcher
//      needs one, and seeding a git repo does not.
//
// Run: node --test tests/em_dash_rewrite_branches.test.mjs (tests/test_js_suites.py runs it).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install`; tests/test_js_suites.py enforces it

let CONTEXT = [];
let INIT = {};

function fakeFetch(url) {
    const path = String(url);
    const body = path.endsWith('/api/agents/context') ? CONTEXT
        : path.endsWith('/api/dispatcher/init') ? INIT
            : path.endsWith('/api/agents') ? []
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.endsWith('/api/orchestrator/status') ? { wid: null, name: null, state: 'unregistered', why: '', queued: 0 }
                        : path.includes('/api/log') ? { boot_id: 'b1', gap: null, first_seq: 0, last_seq: 0, next_seq: 0, events: [] }
                            : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

// agents.js imports main.js, which boots the whole page on import, so the page skeleton,
// GridStack, EventSource and the timers are stubbed (harness from
// access_requests_wiring.test.mjs).
const PAGE = `
<span id="hdr-agents"></span><span id="hdr-schedules"></span><span id="hdr-next"></span><span id="hdr-updated"></span>
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button><button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button><div id="term-switcher"></div><div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>
<div class="panel" id="panel-personas"></div>
<section class="side-section" id="side-decisions"><div id="decisions-chip"></div><div class="decisions-list" id="decisions-list"></div></section>
<div id="ctx-a1"><div class="context-bar-fill"></div><span class="context-label"></span></div>
<input id="init-path" value="/srv/repo"><pre id="init-result"></pre>`;

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

let agents;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
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
    agents = await import('../chela/dashboard/static/js/agents.js');
    await import('../chela/dashboard/static/js/dispatcher.js');
});

const reading = over => ({ name: 'a1', used: '50k', total: '200k', used_pct: 25, session_name: 'cmx-1', ...over });
const label = () => document.querySelector('#ctx-a1 .context-label');

test('an estimated context reading carries the install-statusline hint in its tooltip', async () => {
    CONTEXT = [reading({ estimated: true })];
    await agents.checkContext();
    assert.equal(label().title,
        'cmx-1 · estimate from transcript; run `chela install-statusline` for exact context %');
});

test('an exact context reading has no estimate hint', async () => {
    CONTEXT = [reading({ estimated: false })];
    await agents.checkContext();
    assert.equal(label().title, 'cmx-1');
});

const result = () => document.getElementById('init-result').textContent;

test('seeding a path that is not a git repo warns that the dispatcher needs one', async () => {
    INIT = { ok: true, path: '/srv/repo', created: ['WORKFLOW.md'], skipped: [], is_git: false };
    await window.chela.doInitRepo();
    assert.match(result(), /^⚠ Not a git repo\. The dispatcher needs one to branch & open PRs\.$/m);
});

test('seeding a git repo shows no git warning', async () => {
    INIT = { ok: true, path: '/srv/repo', created: ['WORKFLOW.md'], skipped: [], is_git: true };
    await window.chela.doInitRepo();
    assert.match(result(), /^✓ Created WORKFLOW\.md$/m);
    assert.doesNotMatch(result(), /Not a git repo/);
});
