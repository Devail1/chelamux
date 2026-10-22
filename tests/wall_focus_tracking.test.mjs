// CMX-104: THE FOCUS RING MARKS THE PANE THAT HOLDS THE KEYBOARD — AND ONLY IT.
// Live after CMX-90 (two-pane Wall, Focus layout): Liav typed into @2 (its xterm drew
// the solid, focused cursor) while the .term-focused ring sat on @3 (hollow, blurred
// cursor). the ring (now features/paneframe's applyTermFocus) trusted the PARENT's document.activeElement and only ever
// un-ringed the one pane it remembered. It now asks each term frame's OWN
// document.hasFocus() — the focus xterm's cursor follows — and re-asserts "exactly
// one .term-focused, on that pane" on the DOM every tick.
//
// Runs the REAL terminals.js in jsdom against the real CMX-90 wall DOM (renderTerminals
// -> buildWall tiles: .grid-stack-item > .grid-stack-item-content > iframe.term-frame).
// jsdom has no focus model across frames, so each frame document's hasFocus() and the
// parent's activeElement/hasFocus are pinned per case — the exact states a real
// Chromium reports (measured with playwright while diagnosing, see the PR).
//
// Run: pnpm exec vitest run tests/wall_focus_tracking.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const PANEL = `
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="sidebar-agents"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>`;

const AGENTS = [
    { name: 'liavedunix', window_id: '@2', online: true },
    { name: 'openmmo', window_id: '@3', online: true },
];

function fakeFetch(url) {
    const path = String(url);
    const body = path.endsWith('/api/agents') ? AGENTS
        : path.endsWith('/api/agents/context') ? []
            : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                : path.startsWith('/api/term/ready') ? { ready: true }
                    : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function fakeGridStack() {
    return {
        init(_opts, el) {
            const nodes = Array.from(el.children).map(it => ({ id: it.getAttribute('gs-id'), x: 0, y: 0, w: 6, h: 5, el: it }));
            return {
                engine: { nodes }, on() {}, off() {}, destroy() {}, enableMove() {}, enableResize() {},
                cellHeight() {}, column() {}, batchUpdate() {}, commit() {}, float() {}, update() {},
                save: () => [], removeWidget() {}, makeWidget(e) { return e; },
                getGridItems: () => nodes.map(n => n.el), removeAll() {},
            };
        },
    };
}

let terminals, paneframe;

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
    dom.window.Element.prototype.scrollIntoView = () => {};
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;   // the 250ms poll is driven by hand below
    await import('../chela/dashboard/frontend/src/boot.ts');
    const util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/frontend/src/features/wall/index.ts');
    paneframe = await import('../chela/dashboard/frontend/src/features/paneframe/index.ts');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

const content = wid => document.querySelector(`#term-stage .grid-stack-item[gs-id="${wid}"] > .grid-stack-item-content`);
const frame = wid => content(wid).querySelector('iframe.term-frame');
const ringed = () => [...document.querySelectorAll('.term-focused')]
    .map(el => el.closest('.grid-stack-item').getAttribute('gs-id'));

// Pin what a real browser reports: which element the parent calls active, whether the
// page has focus at all, and which frame's OWN document holds focus.
function browserSays({ active = null, pageFocused = true, frameWithFocus = null }) {
    Object.defineProperty(document, 'activeElement', {
        configurable: true, get: () => (active ? frame(active) : document.body),
    });
    document.hasFocus = () => pageFocused;
    for (const wid of ['@2', '@3']) {
        frame(wid).contentDocument.hasFocus = () => wid === frameWithFocus;
    }
}

beforeEach(() => {
    browserSays({});
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), [], 'precondition: no pane ringed before the case');
});

test('the real wall rendered both panes with real term frames (or every case below is vacuous)', () => {
    for (const wid of ['@2', '@3']) {
        assert.ok(content(wid), `${wid} tile missing`);
        assert.ok(frame(wid) && frame(wid).contentDocument, `${wid} has no live iframe.term-frame`);
    }
});

test('clicking from pane A\'s frame straight into pane B\'s moves .term-focused to B and off A', () => {
    browserSays({ active: '@3', frameWithFocus: '@3' });
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), ['@3']);
    browserSays({ active: '@2', frameWithFocus: '@2' });
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), ['@2'], 'the ring must follow the click into @2 and leave @3');
});

test('CMX-104: the frame whose OWN document holds focus gets the ring, even when the parent\'s activeElement still names the other pane', () => {
    browserSays({ active: '@3', frameWithFocus: '@3' });
    paneframe.applyTermFocus();
    // The live state: the keyboard (and xterm's solid cursor) is in @2, but the
    // parent document still reports @3's iframe as active.
    browserSays({ active: '@3', frameWithFocus: '@2' });
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), ['@2'], 'the ring must mark the pane the keyboard is in, not the stale activeElement');
});

test('CMX-104: a ring left on a pane the tracker no longer remembers is cleared on the next tick', () => {
    browserSays({ active: '@2', frameWithFocus: '@2' });
    paneframe.applyTermFocus();
    content('@3').classList.add('term-focused');   // a stray ring, from anywhere
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), ['@2'], 'exactly one pane may carry .term-focused');
});

test('CMX-104: activeElement naming a pane whose own document does NOT hold focus rings nothing', () => {
    browserSays({ active: '@3', frameWithFocus: '@3' });
    paneframe.applyTermFocus();
    browserSays({ active: '@3', frameWithFocus: null });   // the page has focus, no frame does
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), [], 'a stale activeElement must not keep the ring on a pane the keyboard left');
});

test('focus back in the dashboard itself clears every ring', () => {
    browserSays({ active: '@2', frameWithFocus: '@2' });
    paneframe.applyTermFocus();
    browserSays({ active: null, frameWithFocus: null });
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), []);
});

test('the browser window losing OS focus keeps the ring on the pane focus will return to', () => {
    browserSays({ active: '@3', frameWithFocus: '@3' });
    paneframe.applyTermFocus();
    browserSays({ active: '@3', pageFocused: false, frameWithFocus: null });
    paneframe.applyTermFocus();
    assert.deepEqual(ringed(), ['@3']);
});
