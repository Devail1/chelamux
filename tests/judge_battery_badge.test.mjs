// ⚖️ CMX-40 — a judge window's DETACHED mutation battery shows its live progress on the
// pane's state pill, its status dot / taskbar chip and the sidebar row, instead of "idle".
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

// `current` carries a guard text no surface may print: the dashboard draws the LABEL
// (counts + elapsed) only — the experiment running now may be a held-out one (CMX-395),
// and agents can read panes.
const SECRET = 'SECRET-held-out-guard-text';
const TESTING = { state: 'testing', done: 3, total: 6, label: '⚖️ testing · 3/6 · 15m',
                  current: `guard.py: ${SECRET}` };
const DIED = { state: 'died', done: 2, total: 6, label: '⚖️ judge run died — no verdict',
               current: `guard.py: ${SECRET}` };

const AGENTS = [
    { name: 'judge-a', window_id: '@40', online: true, cwd: '/p/a', claude_running: true,
      session_status: 'idle', judge_battery: TESTING },
    { name: 'judge-b', window_id: '@41', online: true, cwd: '/p/b', claude_running: true,
      session_status: 'idle', judge_battery: DIED },
    { name: 'judge-c', window_id: '@43', online: true, cwd: '/p/d', claude_running: true,
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
    // A pane minimized to the taskbar: its CHIP carries the same status dot.
    terminals.minimizePane('@43');
    await terminals.renderTerminals();
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
    assert.equal(batteryState(AGENTS.find(a => a.name === 'plain')), null);
});

// --- the pane-header status dot + the taskbar chip (CLASS, not just the label) --------

// Every `.term-status-dot` the wall draws for a window: the pane header's dot and, for a
// minimized pane, its taskbar chip. Asserted on the CLASS the CSS colours by — the label
// text alone cannot catch a dot that still renders the idle shape.
const dots = wid => [...document.querySelectorAll(`#panel-terminals .term-status-dot[data-status-for="${wid}"]`)];
const DOT_STATES = ['working', 'waiting', 'idle', 'done'];
const dotState = el => DOT_STATES.filter(c => el.classList.contains(c));

test('while the battery runs, the judge pane\'s header dot reads WORKING — never idle', () => {
    const els = dots('@40');
    assert.ok(els.length >= 1, 'no status dot on the judge pane');
    assert.ok(els.some(el => el.closest('.grid-stack-item-content, .term-pane')), 'setup: the dot must sit in the pane header');
    for (const el of els) assert.deepEqual(dotState(el), ['working'], el.className);
});

test('a battery that died before a verdict: its taskbar chip dot reads WAITING — never idle', () => {
    const chip = document.querySelector('#panel-terminals .min-chip .term-status-dot[data-status-for="@43"]');
    assert.ok(chip, 'setup: the minimized judge pane must have a taskbar chip with a dot');
    for (const wid of ['@41', '@43']) {
        assert.ok(dots(wid).length >= 1, `setup: no dot for ${wid}`);
        for (const el of dots(wid)) assert.deepEqual(dotState(el), ['waiting'], el.className);
    }
});

test('negative control: a window with no battery has an IDLE dot', () => {
    const els = dots('@42');
    assert.ok(els.length >= 1, 'setup: the plain pane must render a dot');
    for (const el of els) assert.deepEqual(dotState(el), ['idle'], el.className);
});

// --- a held-out experiment is never named on a dashboard surface -----------------------

test('the experiment running now is never printed on the pane, chip or sidebar', () => {
    assert.ok(pill('@40') && rowState('judge-a'), 'setup: the surfaces must render');
    assert.ok(!document.body.innerHTML.includes(SECRET), 'a running experiment\'s guard text reached the DOM');
    for (const el of [...dots('@40'), ...dots('@41'), ...dots('@43')]) assert.ok(!el.title.includes(SECRET), el.title);
});
