// CMX-417 — every pane's tmux window id (`@N`) is visible in the dashboard.
//
// The orchestrator, `chela peek @N`, inbox notices and peer messages all address
// an agent by `@N`; before this the Wall never showed it, so the operator could
// not tell which pane was @32.
//
// Runs the REAL main.js/nav.js/terminals.js in jsdom (same harness as
// tests/wall.test.mjs) over a fleet whose ids are deliberately confusable:
// @3 and @32 (a prefix of each other), @32 carrying a very long name.
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   1. each pane's footer renders ITS OWN id — not another window's, not the name;
//   2. the sidebar row carries its window's id (row face + tooltip);
//   3. the palette resolves "@N" EXACTLY: "@3" reaches @3 (not @32), and running
//      the row focuses that window's pane;
//   4. clicking the footer chip copies the id and shows a "Copied" toast;
//   5. ⭐ MUST STILL PASS: a window with a very long name still shows its id.
//
// Run: node --test tests/window_id.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { resolveWindowId, windowIdQuery } from '../chela/dashboard/static/js/windowid.js';

const LONG = 'a-very-long-session-name-that-goes-on-and-on-well-past-any-pane-width-' + 'x'.repeat(80);

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

const AGENTS = [
    { name: 'alpha', window_id: '@3', online: true, cwd: '/p/alpha' },
    { name: LONG, window_id: '@32', online: true, cwd: '/p/long' },
];

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

let terminals, util, nav, clipboard = [];

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    Object.defineProperty(dom.window.navigator, 'clipboard', {
        configurable: true,
        value: { writeText: t => { clipboard.push(t); return Promise.resolve(); } },
    });
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
    util = await import('../chela/dashboard/static/js/util.js');
    nav = await import('../chela/dashboard/static/js/nav.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
    nav.renderSidebarAgents(AGENTS);
});

const tile = wid => document.querySelector(`#term-stage .grid-stack-item[gs-id="${wid}"]`);
const footerId = wid => {
    const chip = tile(wid) && tile(wid).querySelector('.term-ctx-bar .gs-wid');
    return chip ? chip.textContent.trim() : null;
};
const sidebarRow = name => [...document.querySelectorAll('#sidebar-agents .agent-row')]
    .find(r => r.dataset.agent === name);
const paletteRows = () => [...document.querySelectorAll('#palette-list .palette-item')].map(r => ({
    i: Number(r.dataset.i),
    title: r.querySelector('.pi-title').textContent,
    sub: r.querySelector('.pi-sub').textContent,
}));
const sleep = ms => new Promise(r => setTimeout(r, ms));

// --- the harness itself is not a mock -------------------------------------------

test('the wall under test holds a tile per window', () => {
    for (const wid of ['@3', '@32']) assert.ok(tile(wid), `no tile for ${wid}`);
});

// --- 1. the footer shows ITS OWN window id --------------------------------------

test('each pane footer renders its own window id, as "@N"', () => {
    assert.equal(footerId('@3'), '@3');
    assert.equal(footerId('@32'), '@32');
});

test('the footer id leads the bar and is a real button wired to copy', () => {
    const bar = tile('@32').querySelector('.term-ctx-bar');
    const first = bar.firstElementChild;
    assert.ok(first.classList.contains('gs-wid'), `bar starts with ${first.className}, not .gs-wid`);
    assert.equal(first.tagName, 'BUTTON');
    assert.equal(first.dataset.wid, '@32');
});

// --- ⭐ MUST STILL PASS: a long name never pushes the id out ---------------------

test('⭐ a window with a very long name still shows its id in the footer and the sidebar', () => {
    assert.equal(footerId('@32'), '@32');
    const row = sidebarRow(LONG);
    assert.ok(row, 'no sidebar row for the long-named agent');
    assert.equal(row.querySelector('.ar-wid').textContent, '@32');
});

// --- 2. the sidebar row carries the id ------------------------------------------

test('the sidebar row shows its window id on its second line and in its tooltip', () => {
    const row = sidebarRow('alpha');
    assert.ok(row, 'no sidebar row for alpha');
    const wid = row.querySelector('.ar-sub .ar-wid');
    assert.ok(wid, 'no .ar-wid on the row\'s second line');
    assert.equal(wid.textContent, '@3');
    assert.match(row.title, /· @3(\n|$| —)/, `tooltip ${JSON.stringify(row.title)} lacks "· @3"`);
});

// --- 3. the palette resolves "@N" exactly ---------------------------------------

test('"@3" in the palette puts @3 first — exact, not its prefix-sibling @32', () => {
    window.chela.openPalette();
    window.chela._renderPalette('@3');
    const rows = paletteRows();
    assert.ok(rows.length, 'no palette rows for "@3"');
    assert.equal(rows[0].title, 'alpha', `first row is ${JSON.stringify(rows[0])}`);
    assert.match(rows[0].sub, /@3$/);
    window.chela.closePalette();
});

test('"@3" still lists its prefix-sibling @32 below — even with a very long name — and @3 only once', () => {
    window.chela.openPalette();
    window.chela._renderPalette('@3');
    const rows = paletteRows();
    assert.ok(rows.slice(1).some(r => r.sub.endsWith('· @32')), `no @32 row in ${JSON.stringify(rows)}`);
    assert.equal(rows.filter(r => / @3$/.test(r.sub)).length, 1, `@3 listed twice: ${JSON.stringify(rows)}`);
    window.chela.closePalette();
});

test('"@32" in the palette resolves to the long-named @32', () => {
    window.chela.openPalette();
    window.chela._renderPalette('@32');
    const rows = paletteRows();
    assert.equal(rows[0].sub, 'window · @32');
    assert.match(rows[0].title, /^a-very-long-session-name/);
    window.chela.closePalette();
});

test('running the "@3" row focuses @3\'s pane (and only it) — a jump, not a toggle', async () => {
    window.chela.openPalette();
    window.chela._renderPalette('@3');
    const row = paletteRows()[0];
    window.chela._palRun(row.i);
    await sleep(120);   // focusPaneByWid defers 60ms for the view switch to settle
    const flashed = [...document.querySelectorAll('#term-stage .grid-stack-item')]
        .filter(it => it.querySelector('.pane-flash, .grid-stack-item-content.pane-flash'))
        .map(it => it.getAttribute('gs-id'));
    assert.deepEqual(flashed, ['@3']);
    assert.ok(!terminals._minimized.has('@3'), 'the jump minimized the pane it should focus');
});

test('a query that is not an open window id gets no window row', () => {
    window.chela.openPalette();
    window.chela._renderPalette('@99');
    assert.ok(!paletteRows().some(r => r.sub.startsWith('window ·')));
    window.chela.closePalette();
});

// --- 4. click copies ------------------------------------------------------------

test('clicking the footer id copies it and shows a "Copied" toast', async () => {
    clipboard = [];
    const chip = tile('@32').querySelector('.gs-wid');
    // jsdom runs no inline handlers: assert the wiring, then call what it calls.
    assert.match(chip.getAttribute('onclick'), /chela\.copyWindowId\(this\)/);
    window.chela.copyWindowId(chip);
    await sleep(0);
    assert.deepEqual(clipboard, ['@32']);
    const toast = tile('@32').querySelector('.gs-wid-toast');
    assert.ok(toast, 'no toast after the copy');
    assert.equal(toast.textContent, 'Copied @32');
});

// --- the pure matcher -----------------------------------------------------------

test('windowIdQuery accepts exactly "@<digits>"', () => {
    assert.equal(windowIdQuery('@32'), '@32');
    assert.equal(windowIdQuery('  @7 '), '@7');
    for (const q of ['', '@', '32', '@3x', 'alpha', '@-1', null, undefined]) {
        assert.equal(windowIdQuery(q), null, JSON.stringify(q));
    }
});

test('resolveWindowId is exact, never a prefix', () => {
    assert.equal(resolveWindowId('@3', ['@32', '@3']), '@3');
    assert.equal(resolveWindowId('@3', ['@32']), null);
    assert.equal(resolveWindowId('@32', ['@3', '@32']), '@32');
    assert.equal(resolveWindowId('alpha', ['@3']), null);
});
