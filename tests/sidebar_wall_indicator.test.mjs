// SIDEBAR "OPEN ON THE WALL" CUE, IN A REAL DOM (CMX-137).
//
// Every SESSIONS row looks the same whether its pane is live on the wall or docked
// to the min-dock — so a click reads as "focus" or "restore" only after you make it.
// nav.js::_agentRowHtml now marks a row `.on-wall` when its window_id is BOTH
// rendered (terminals.js's `_renderedWids`) AND not minimized (`_minimized`).
//
// This runs the REAL terminals.js (renderTerminals/termTick, building a REAL wall
// out of a fake GridStack — same rig as tests/walldock.test.mjs) alongside the REAL
// nav.js (renderSidebarAgents into a REAL #sidebar-agents), so the cue is asserted on
// what RENDERS off the SAME live module state the wall itself uses — not a source grep.
//
// Run: node --test tests/sidebar_wall_indicator.test.mjs  (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
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
</div>
<aside class="sidebar">
  <section class="side-section">
    <span class="side-count" id="hdr-agents">-/-</span>
    <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
  </section>
</aside>`;

const HUMAN = '@1';    // opens straight onto the wall, never docked
const WORKER = '@9';   // a dispatcher-spawned worker: opens minimized, may pop out

let AGENTS = [];
const worker = (wid, name, { dispatched = true, needsHuman = false } = {}) => (
    { name, window_id: wid, online: true, session_status: 'busy',
        claude_running: true, dispatched, needs_human: needsHuman });
const fleet = (opts) => [
    { name: 'orchestrator', window_id: HUMAN, online: true, session_status: 'idle',
        claude_running: true, dispatched: false, needs_human: false },
    worker(WORKER, 'cmx-137', opts),
];

function fakeFetch(url) {
    const path = String(url);
    const body =
        path.endsWith('/api/agents') ? AGENTS
            : path.endsWith('/api/agents/context') ? {}
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
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

let terminals, nav, util;

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
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    nav = await import('../chela/dashboard/static/js/nav.js');
    util.setCurrentTab('terminals');

    AGENTS = fleet();   // WORKER opens minimized (walldock's property 1)
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

const rowFor = wid => document.querySelector(`#sidebar-agents .agent-row[data-agent="${wid === HUMAN ? 'orchestrator' : 'cmx-137'}"]`);

test('a pane open on the wall (not docked) gets the on-wall cue', () => {
    nav.renderSidebarAgents(AGENTS);
    assert.ok(rowFor(HUMAN).classList.contains('on-wall'),
        "the orchestrator's pane is live on the wall — its row must carry the cue");
});

test('a pane docked to the min-dock (minimized) does NOT get the cue', () => {
    nav.renderSidebarAgents(AGENTS);
    assert.ok(!rowFor(WORKER).classList.contains('on-wall'),
        'a minimized pane is not "open on the wall" — the cue must not fire for it');
});

test('popping the dock out flips the cue on — driven by the SAME wall state, live', async () => {
    AGENTS = fleet({ needsHuman: true });   // worker blocks -> pops out of the dock
    await terminals.termTick();
    nav.renderSidebarAgents(AGENTS);
    assert.ok(rowFor(WORKER).classList.contains('on-wall'),
        'a popped-out (restored) pane must now read as open on the wall');
});

test('minimizing a wall pane by hand flips the cue back off', () => {
    const tile = document.querySelector(`#term-stage .grid-stack-item[gs-id="${HUMAN}"]`);
    window.chela.termMinFor(tile.querySelector('.gs-min-btn'));
    nav.renderSidebarAgents(AGENTS);
    assert.ok(!rowFor(HUMAN).classList.contains('on-wall'),
        'a pane just minimized to the dock must lose the on-wall cue immediately');
});
