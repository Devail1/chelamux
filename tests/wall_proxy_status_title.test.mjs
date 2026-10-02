// A SANDBOXED PANE'S STATUS TOOLTIP SAYS IT CAME FROM THE PROXY (CMX-436), IN A REAL DOM.
//
// A sandboxed session's working/idle is derived from its credential proxy's traffic
// (`status_source: "sandbox-proxy"` on /api/agents), not Claude's own report. The pure
// stateTitle() is unit-tested in wall_tile.test.mjs; THIS drives the REAL terminals.js
// (a real wall over a fake GridStack) and reads the RENDERED tooltips, so the wiring
// that puts stateTitle() onto the Wall's state pill AND the pane's status dot is guarded:
// swap either call site back to the bare word and this goes red.
//
// Guards (corrupt→RED):
//   - the sandboxed pane's `.gs-state` pill AND its `.term-status-dot` say
//     "… — from the sandbox proxy";
//   - ⭐ an ordinary pane's pill and dot carry the plain word, no proxy claim.
//
// Run: node --test tests/wall_proxy_status_title.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

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

const SANDBOXED = '@5';
const ORDINARY = '@2';

const AGENTS = [
    { name: 'sandbox-aaron', window_id: SANDBOXED, online: true, session_status: 'busy',
        status_source: 'sandbox-proxy', claude_running: true, dispatched: false, needs_human: false },
    { name: 'worker', window_id: ORDINARY, online: true, session_status: 'busy',
        status_source: null, claude_running: true, dispatched: false, needs_human: false },
];

function fakeFetch(url) {
    const p = String(url);
    const body =
        p.endsWith('/api/agents') ? AGENTS
            : p.endsWith('/api/agents/context') ? []
                : p.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : p.startsWith('/api/term/ready') ? { ready: true }
                        : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {},
        removeWidget(el, removeDOM) { if (removeDOM !== false) el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let terminals;

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
    dom.window.document.elementFromPoint = () => null;
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    dom.window.HTMLElement.prototype.scrollIntoView = () => {};
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setAgentsCache(AGENTS);
    util.setCurrentTab('terminals');
    window.chela.setTermMode('wall');
    await terminals.renderTerminals();
    [SANDBOXED, ORDINARY].forEach(w => terminals._minimized.delete(w));
    await terminals.renderTerminals();
});

const pill = wid => document.querySelector(`#panel-terminals .gs-state[data-state-for="${wid}"]`);
const dot = wid => document.querySelector(`#panel-terminals .term-status-dot[data-status-for="${wid}"]`);

test('the sandboxed pane\'s state pill tooltip says it came from the sandbox proxy', () => {
    const el = pill(SANDBOXED);
    assert.ok(el, 'setup: the sandboxed pane must render a state pill on the wall');
    assert.equal(el.querySelector('.gs-state-word').textContent, 'working');
    assert.equal(el.title, 'Working — from the sandbox proxy');
});

test('the sandboxed pane\'s status dot tooltip says it came from the sandbox proxy', () => {
    const el = dot(SANDBOXED);
    assert.ok(el, 'setup: the sandboxed pane must render a status dot');
    assert.equal(el.title, 'Working — from the sandbox proxy');
});

test('⭐ an ordinary pane\'s pill and dot carry the plain word, no proxy claim', () => {
    assert.ok(pill(ORDINARY) && dot(ORDINARY), 'setup: the ordinary pane must render a pill and a dot');
    assert.equal(pill(ORDINARY).title, 'Working');
    assert.equal(dot(ORDINARY).title, 'Working');
});
