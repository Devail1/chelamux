// CMX-242 — the toolbar writes the Wall's layout (shared/store/wall.ts `wallLayout`); the
// legacy grid (terminals.js `_onLayoutChange`) applies it. This runs the REAL terminals.js in a
// REAL DOM with a GridStack fake that records what it is told, and drives the store the way
// the toolbar's buttons do:
//
//   - lock turns resize off and leaves move on; auto-arrange and Focus turn both off;
//   - auto-arrange ranks the wall by attention and NEVER writes pc_wall_layout (the manual
//     layout is sacred); turning it off restores pc_wall_layout verbatim;
//   - Focus lays one pane large and the rest a strip, marked by class; turning it off clears
//     the marks and restores the manual layout; switching straight to auto-arrange clears them;
//   - a preset fills the wall and writes pc_wall_layout, and choosing it AGAIN re-tidies a wall
//     the user dragged about.
//
// Run: pnpm exec vitest run tests/wall_layout_apply.test.mjs (tests/test_js_suites.py runs it).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const PANEL = `
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span class="island-slot" id="wall-toolbar-island"></span>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
</div>`;

// @2 wants a human: auto-arrange ranks it first, Focus picks it for the large pane.
const AGENTS = [
    { name: 'a', window_id: '@1', online: true, session_status: 'idle' },
    { name: 'b', window_id: '@2', online: true, session_status: 'waiting' },
    { name: 'c', window_id: '@3', online: true, session_status: 'busy' },
];

function fakeFetch(url) {
    const path = String(url);
    const body = path.endsWith('/api/agents') ? AGENTS
        : path.endsWith('/api/agents/context') ? []
            : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                : path.startsWith('/api/term/ready') ? { ready: true } : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

// What the grid was told. `move`/`resize` are the last enableMove/enableResize.
const told = { move: null, resize: null };
function fakeGridStack() {
    const attr = (el, k, d) => { const v = el.getAttribute('gs-' + k); return v == null ? d : Number(v); };
    // each tile carries its node, so a test reads the geometry the grid was last given
    const nodeFor = el => (el.__node = { id: el.getAttribute('gs-id'), x: attr(el, 'x', 0), y: attr(el, 'y', 0), w: attr(el, 'w', 1), h: attr(el, 'h', 1), el });
    return {
        init(_opts, el) {
            const nodes = Array.from(el.children).map(nodeFor);
            return {
                engine: { nodes },
                on() {}, off() {}, destroy() {}, cellHeight() {}, column() {}, batchUpdate() {}, commit() {}, float() {},
                enableMove(on) { told.move = on; },
                enableResize(on) { told.resize = on; },
                update(itemEl, o) {
                    const n = nodes.find(nd => nd.el === itemEl);
                    if (n) Object.assign(n, o);
                },
                save: () => nodes.map(n => ({ id: n.id, x: n.x, y: n.y, w: n.w, h: n.h })),
                removeWidget(itemEl) { const i = nodes.findIndex(nd => nd.el === itemEl); if (i !== -1) nodes.splice(i, 1); },
                makeWidget(itemEl) { nodes.push(nodeFor(itemEl)); return itemEl; },
                getGridItems: () => nodes.map(n => n.el),
                removeAll() { nodes.length = 0; },
            };
        },
    };
}

let terminals, util, wall;

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: (_t, k) => (k === 'canvas' ? null : () => {}) });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    dom.window.matchMedia = q => ({ media: q, matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    localStorage.setItem('pc_term_mode', 'wall');
    await import('../chela/dashboard/frontend/src/boot.ts');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/frontend/src/features/wall/index.ts');
    wall = await import('../chela/dashboard/frontend/src/shared/store/wall.ts');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

const item = wid => document.querySelector(`#term-stage .grid-stack-item[gs-id="${wid}"]`);
const geom = () => Object.fromEntries(['@1', '@2', '@3'].map(w => {
    const { x, y, w: wd, h } = item(w).__node;
    return [w, { x, y, w: wd, h }];
}));

const MANUAL = { '@1': { x: 0, y: 0, w: 4, h: 5 }, '@2': { x: 4, y: 0, w: 4, h: 5 }, '@3': { x: 8, y: 0, w: 4, h: 5 } };
const focusMarks = () => ['@1', '@2', '@3'].map(w =>
    item(w).classList.contains('wall-focus-main') ? 'main' : item(w).classList.contains('wall-focus-strip') ? 'strip' : '-');

beforeEach(() => {
    // back to a calm manual wall: nothing owns the layout, the user's own layout saved
    wall.setWallFocus(false);
    wall.setWallAuto(false);
    wall.setWallLocked(false);
    localStorage.setItem('pc_wall_layout', JSON.stringify(MANUAL));
});

test('lock turns resize off and leaves move on (a drag swaps); unlock turns resize back on', () => {
    wall.setWallLocked(true);
    assert.deepEqual([told.move, told.resize], [true, false]);
    wall.setWallLocked(false);
    assert.deepEqual([told.move, told.resize], [true, true]);
});

test('auto-arrange turns move and resize off, ranks the wall, and NEVER writes the manual layout', () => {
    wall.setWallAuto(true);
    assert.deepEqual([told.move, told.resize], [false, false]);
    assert.deepEqual(JSON.parse(localStorage.getItem('pc_wall_layout')), MANUAL, 'auto-arrange wrote pc_wall_layout');
    assert.deepEqual(focusMarks(), ['-', '-', '-']);
});

test('Focus lays the pane that wants a human large and the rest a strip; off clears it and restores', () => {
    wall.setWallFocus(true);
    assert.deepEqual([told.move, told.resize], [false, false]);
    assert.deepEqual(focusMarks(), ['strip', 'main', 'strip']);
    assert.ok(document.querySelector('#term-stage .grid-stack').classList.contains('wall-focus-on'));
    assert.deepEqual(JSON.parse(localStorage.getItem('pc_wall_layout')), MANUAL, 'Focus wrote pc_wall_layout');
    wall.setWallFocus(false);
    assert.deepEqual(focusMarks(), ['-', '-', '-']);
    assert.ok(!document.querySelector('#term-stage .grid-stack').classList.contains('wall-focus-on'));
    assert.deepEqual([told.move, told.resize], [true, true]);
});

test('switching straight from Focus to auto-arrange clears Focus\'s marks', () => {
    wall.setWallFocus(true);
    assert.deepEqual(focusMarks(), ['strip', 'main', 'strip']);
    wall.setWallAuto(true);
    assert.deepEqual(focusMarks(), ['-', '-', '-']);
    assert.equal(wall.wallLayout.get().focus, false);
    assert.deepEqual([told.move, told.resize], [false, false]);
});

test('switching straight from auto-arrange to Focus lays Focus out', () => {
    wall.setWallAuto(true);
    wall.setWallFocus(true);
    assert.deepEqual(focusMarks(), ['strip', 'main', 'strip']);
});

test('a preset fills the wall and writes it as the manual layout; choosing it AGAIN re-tidies', () => {
    wall.setWallPreset({ cols: 3, rows: 1 });
    const filled = JSON.parse(localStorage.getItem('pc_wall_layout'));
    assert.deepEqual(Object.keys(filled).sort(), ['@1', '@2', '@3']);
    assert.deepEqual(new Set(Object.values(filled).map(c => c.w)), new Set([4]), 'three columns are 4 wide each');
    localStorage.setItem('pc_wall_layout', JSON.stringify(MANUAL));   // the user drags it about
    wall.setWallPreset({ cols: 3, rows: 1 });                           // the same preset, chosen again
    assert.notDeepEqual(JSON.parse(localStorage.getItem('pc_wall_layout')), MANUAL, 'choosing the preset again did not re-tidy');
});

test('a preset while auto-arrange is on changes its column count but never writes the manual layout', () => {
    wall.setWallAuto(true);
    wall.setWallPreset({ cols: 1, rows: 1 });
    assert.deepEqual(JSON.parse(localStorage.getItem('pc_wall_layout')), MANUAL);
});

test('turning auto-arrange off restores the manual layout verbatim, and never rewrites it', () => {
    wall.setWallAuto(true);
    wall.setWallPreset({ cols: 1, rows: 1 });   // auto re-fills the wall at one column
    assert.notDeepEqual(geom(), MANUAL, 'auto-arrange moved nothing: the restore below would be vacuous');
    wall.setWallAuto(false);
    assert.deepEqual(geom(), MANUAL);
    assert.deepEqual(JSON.parse(localStorage.getItem('pc_wall_layout')), MANUAL);
});

test('turning Focus off restores the manual layout verbatim too', () => {
    wall.setWallFocus(true);
    assert.equal(geom()['@2'].w, 8, 'the large pane is 8 of 12 columns');
    wall.setWallFocus(false);
    assert.deepEqual(geom(), MANUAL);
});
