// ⚖️ CMX-40 — a judge window's DETACHED mutation battery shows its live progress on the
// pane's state pill, the sidebar row and the Work card, instead of "idle".
//
// The judge agent goes idle by design once it launches `chela judge run --detach`, and
// Claude Code does not list a run it did not start — so before this the window read
// "idle" for the whole battery (measured on CMX-32, 15 min). `/api/agents` now carries
// `judge_battery` (judge.battery_for_window); these run the REAL main.js/nav.js/
// terminals.js in jsdom (same harness as tests/window_id.test.mjs) over three idle
// windows: a live battery (3/6), one that died before a verdict, and one with none.
//
// Run: node --test tests/judge_battery_badge.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { batteryState, tileState } from '../chela/dashboard/static/js/wallmodel.js';
import { runStateBadges } from '../chela/dashboard/static/js/kanbanlinearmodel.js';

const TESTING = { state: 'testing', done: 3, total: 6, label: '⚖️ testing · 3/6 · 15m' };
const DIED = { state: 'died', done: 2, total: 6, label: '⚖️ judge run died — no verdict' };

const AGENTS = [
    { name: 'judge-a', window_id: '@40', online: true, cwd: '/p/a', claude_running: true,
      session_status: 'idle', judge_battery: TESTING },
    { name: 'judge-b', window_id: '@41', online: true, cwd: '/p/b', claude_running: true,
      session_status: 'idle', judge_battery: DIED },
    { name: 'plain', window_id: '@42', online: true, cwd: '/p/c', claude_running: true,
      session_status: 'idle', judge_battery: null },
];

const PANEL = `
<div id="sidebar-agents"></div>
<div class="palette-overlay" id="palette">
  <div class="palette"><input id="palette-input"><div id="palette-list"></div></div>
</div>
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

function fakeFetch(url) {
    const path = String(url);
    const body =
        path.endsWith('/api/agents') ? AGENTS
            : path.endsWith('/api/agents/context') ? []
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
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

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.HTMLElement.prototype.scrollIntoView = function () {};
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    const nav = await import('../chela/dashboard/static/js/nav.js');
    const terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
    nav.renderSidebarAgents(AGENTS);
});

const pill = wid => document.querySelector(`#panel-terminals .gs-state[data-state-for="${wid}"]`);
const rowState = name => {
    const row = [...document.querySelectorAll('#sidebar-agents .agent-row')]
        .find(r => r.dataset.agent === name);
    return row && row.querySelector('.ar-state');
};

// --- the judge pane's state pill ---------------------------------------------------

test('a running battery overrides "idle" on the judge pane\'s pill with its count and elapsed', () => {
    const el = pill('@40');
    assert.ok(el, 'no state pill on the judge pane');
    assert.equal(el.textContent.trim(), '⚖️ testing · 3/6 · 15m');
    assert.ok(el.classList.contains('gs-state-testing'), el.className);
});

test('a battery that died before a verdict reads "died" on the pill — never idle', () => {
    const el = pill('@41');
    assert.equal(el.textContent.trim(), '⚖️ judge run died — no verdict');
    assert.ok(el.classList.contains('gs-state-died'), el.className);
});

test('negative control: a window with no battery still reads plain idle', () => {
    assert.equal(pill('@42').textContent.trim(), 'idle');
});

// --- the sidebar row ----------------------------------------------------------------

test('the sidebar row of a running battery says its progress, not idle', () => {
    const el = rowState('judge-a');
    assert.ok(el, 'no sidebar row for the judge window');
    assert.equal(el.textContent, '⚖️ testing · 3/6 · 15m');
    assert.ok(el.classList.contains('working'), el.className);
});

test('the sidebar row of a dead battery says it died, and wants a human', () => {
    const el = rowState('judge-b');
    assert.equal(el.textContent, '⚖️ judge run died — no verdict');
    assert.ok(el.classList.contains('waiting'), el.className);
    assert.equal(rowState('plain').textContent, 'idle');
});

// --- the model ----------------------------------------------------------------------

test('the agent\'s own busy / needs-you state still outranks the battery', () => {
    const a = { ...AGENTS[0], session_status: 'busy' };
    assert.equal(tileState(a, false).word, 'working');
    assert.equal(tileState(AGENTS[0], true).word, 'needs you');
    assert.equal(batteryState(AGENTS[2]), null);
});

// --- the Work card -------------------------------------------------------------------

test('a judging Work card shows the battery\'s progress instead of a bare "judging"', () => {
    const labels = c => runStateBadges(c).map(b => b.label);
    assert.deepEqual(labels({ judge_state: 'running', judge_battery: TESTING }), ['⚖️ testing · 3/6 · 15m']);
    assert.deepEqual(labels({ judge_state: 'running', judge_battery: DIED }), ['⚖️ judge run died — no verdict']);
    assert.deepEqual(labels({ judge_state: 'running', judge_battery: null }), ['⚖️ judging']);
    assert.deepEqual(labels({ judge_state: 'clean', judge_battery: null }), []);
});
