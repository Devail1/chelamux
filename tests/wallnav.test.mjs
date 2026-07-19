// WALL NAV UX, IN A REAL DOM (CMX-108) — three small polish parts on top of the Wall's
// toolbar and pane headers: a "Layout" menu folding the grid-preset picker + lock button
// off the bar, a per-pane layout PIN that opts a tile out of applyGridLayout's auto-reflow,
// and an Alt+1..9 keyboard-fast pane switcher.
//
// Runs the REAL terminals.js in jsdom (same approach as tests/wall.test.mjs and
// tests/walldock.test.mjs): real `renderTerminals` -> `buildWall` -> real
// `.grid-stack-item` tiles, real header buttons. The GridStack fake here is a little
// fuller than the other suites' (it tracks x/y/w/h per node and reflects `update()`
// back onto the DOM's gs-x/gs-y/gs-w/gs-h attributes) because the pin test needs to
// observe applyGridLayout's real reflow decisions, not just "did it not crash".
//
// Three properties, each a regression that would ship silently:
//
//   1. 🔴 THE LAYOUT MENU ACTUALLY GATES THE CONTROLS. #layout-menu starts hidden;
//      openLayoutMenu/hideLayoutMenu toggle it, and the grid-preset picker + lock
//      button live INSIDE it (not still floating loose on the toolbar — the whole
//      point of the fold).
//   2. 🔴 A PINNED PANE'S GEOMETRY IS NEVER TOUCHED BY A GRID PRESET. applyGridLayout
//      reflows every OTHER pane; a pinned one keeps its exact gs-x/gs-y/gs-w/gs-h.
//   3. 🔴 ALT+N JUMPS TO THE RIGHT PANE, PLAIN "N" DOES NOT. The badge numbering and
//      the shortcut's target must never drift apart, and the shortcut must be gated on
//      Alt (a bare digit is normal terminal input and must reach the pane untouched).
//
// Run: node --test tests/wallnav.test.mjs (pytest runs it via tests/test_js_suites.py;
// it needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

// The terminals panel + the layout-menu popover, as index.html emits them (only the
// ids terminals.js / nav.js reach for).
const PANEL = `
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid">
    <button id="term-layout-btn" onclick="chela.openLayoutMenu(event)"></button>
  </span>
  <button id="term-new-shell"></button>
  <div id="term-switcher"></div>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>
<div class="popover" id="layout-menu" style="display:none;">
  <span id="term-grid-presets"></span>
  <button id="term-lock-btn" onclick="chela.toggleWallLock(this)"></button>
</div>`;

const AGENTS = [
    { name: 'alpha', window_id: '@1', online: true },
    { name: 'bravo', window_id: '@2', online: true },
    { name: 'charlie', window_id: '@3', online: true },
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

// A fuller GridStack fake than the other suites': it reads gs-x/gs-y/gs-w/gs-h off the
// tiles at init (buildWall sets them from _wallTileHTML), and `update()` mutates BOTH
// the node and the DOM attribute — so applyGridLayout's real reflow decisions are
// observable from plain DOM reads, no reaching into terminals.js's private `_grid`.
function fakeGridStack() {
    const attr = (el, k, d) => { const v = el.getAttribute('gs-' + k); return v == null ? d : Number(v); };
    const nodeFor = el => ({
        id: el.getAttribute('gs-id'),
        x: attr(el, 'x', 0), y: attr(el, 'y', 0), w: attr(el, 'w', 1), h: attr(el, 'h', 1),
        el,
    });
    return {
        init(_opts, el) {
            const nodes = Array.from(el.children).map(nodeFor);
            const grid = {
                engine: { nodes },
                on() {}, off() {}, destroy() {},
                enableMove() {}, enableResize() {},
                cellHeight() {}, column() {},
                batchUpdate() {}, commit() {}, float() {},
                update(itemEl, opts) {
                    const n = nodes.find(nd => nd.el === itemEl);
                    if (!n) return;
                    Object.assign(n, opts);
                    for (const k of ['x', 'y', 'w', 'h']) {
                        if (opts[k] != null) itemEl.setAttribute('gs-' + k, String(opts[k]));
                    }
                },
                save: () => nodes.map(n => ({ id: n.id, x: n.x, y: n.y, w: n.w, h: n.h })),
                removeWidget(itemEl, removeDOM) {
                    const i = nodes.findIndex(nd => nd.el === itemEl);
                    if (i !== -1) nodes.splice(i, 1);
                    if (removeDOM !== false) itemEl.remove();
                },
                makeWidget(itemEl) { nodes.push(nodeFor(itemEl)); return itemEl; },
                getGridItems: () => nodes.map(n => n.el),
                removeAll() { nodes.length = 0; },
            };
            return grid;
        },
    };
}

let terminals, util;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        // defineProperty, NOT assignment: from node 21 `globalThis.navigator` has only a
        // getter, so plain assignment THROWS. (Learned in tests/wall.test.mjs.)
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.Element.prototype.scrollIntoView = () => {};   // jsdom has no layout engine
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};

    // Browser-faithful import order (main.js is the entry; nav <-> main is a cycle —
    // import anything else first and nav.js's `let`s are in their TDZ).
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

beforeEach(() => {
    window.chela.hideLayoutMenu();
});

const tile = wid => document.querySelector(`#term-stage .grid-stack-item[gs-id="${wid}"]`);
const geom = wid => {
    const el = tile(wid);
    return el && ['x', 'y', 'w', 'h'].map(k => el.getAttribute('gs-' + k)).join(',');
};

// 1 — THE FOLD. One trigger; the picker + lock live behind it, not loose on the bar.
test('the Layout menu is hidden by default, and opening it reveals the real controls', () => {
    const menu = document.getElementById('layout-menu');
    assert.equal(menu.style.display, 'none', 'starts closed');

    window.chela.openLayoutMenu();
    assert.equal(menu.style.display, 'block', 'opens on click');
    // The controls the toolbar used to show inline now live INSIDE the menu.
    assert.ok(menu.querySelector('#term-grid-presets'), 'grid presets are inside the menu');
    assert.ok(menu.querySelector('#term-grid-presets .gl-btn'), 'presets are actually populated, not an empty shell');
    assert.ok(menu.querySelector('#term-lock-btn'), 'lock button is inside the menu');

    window.chela.hideLayoutMenu();
    assert.equal(menu.style.display, 'none', 'closes on hideLayoutMenu');
});

// 2 — THE PIN. Reflowing the wall must never touch a pinned pane's geometry.
test('a pinned pane keeps its exact geometry through a grid-preset reflow', () => {
    const PINNED = '@2', OTHER = '@1';
    const pinBtn = tile(PINNED).querySelector('.gs-pin-btn');
    assert.ok(pinBtn, 'every wall tile gets a pin button');
    assert.equal(pinBtn.getAttribute('aria-pressed'), 'false');

    window.chela.termPinToggle(pinBtn, PINNED);
    assert.equal(pinBtn.getAttribute('aria-pressed'), 'true', 'toggled on');
    assert.ok(tile(PINNED).classList.contains('pane-pinned'), 'tile wears the pinned class');
    assert.ok(JSON.parse(localStorage.getItem('pc_wall_pinned') || '[]').includes(PINNED),
        'the pin is persisted so a reload remembers it');

    const before = { pinned: geom(PINNED), other: geom(OTHER) };
    window.chela.applyGridLayout(1, 1);   // single-column stack: guaranteed to move OTHER

    assert.equal(geom(PINNED), before.pinned,
        'a pinned pane must come out of a grid-preset reflow with the SAME x/y/w/h');
    assert.notEqual(geom(OTHER), before.other,
        'an unpinned pane must actually be repositioned by the preset (sanity check — proves the ' +
        'preset ran at all, so the pinned assertion above is not vacuously true)');

    // Unpin and confirm the next reflow reaches it again — the exemption isn't sticky
    // past the toggle.
    window.chela.termPinToggle(pinBtn, PINNED);
    assert.equal(pinBtn.getAttribute('aria-pressed'), 'false');
    assert.ok(!tile(PINNED).classList.contains('pane-pinned'));
    window.chela.applyGridLayout(3, 1);
    assert.notEqual(geom(PINNED), before.pinned, 'once unpinned, a reflow may move it again');
});

// 3 — THE KEYBOARD SWITCHER. Alt+N jumps to the pane the badge shows; bare N does not.
test('Alt+N jumps to the Nth pane by its badge number; a bare digit is left alone', async () => {
    const idx = wid => tile(wid).querySelector('.gs-idx');
    assert.equal(idx('@1').textContent, '1');
    assert.equal(idx('@2').textContent, '2');
    assert.equal(idx('@3').textContent, '3');
    assert.equal(idx('@1').hidden, false, 'a numbered pane\'s badge must be visible');

    const flashed = wid => tile(wid).querySelector('.grid-stack-item-content').classList.contains('pane-flash');

    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '3', altKey: true, bubbles: true }));
    await new Promise(r => setTimeout(r, 120));   // focusPaneByWid defers by 60ms before flashing
    assert.ok(flashed('@3'), 'Alt+3 must flash the pane the badge labelled 3');
    assert.ok(!flashed('@1'), 'Alt+3 must not touch an unrelated pane');

    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '1', bubbles: true }));   // no altKey
    await new Promise(r => setTimeout(r, 120));
    assert.ok(!flashed('@1'), 'a bare digit (no Alt) must be ignored — it is normal terminal input');
});
