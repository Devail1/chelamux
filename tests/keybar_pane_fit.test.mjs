// PHONE KEYBAR vs THE PANE'S BOTTOM ROWS — real-DOM guard for CMX-102.
//
// iPhone Safari, keyboard open: the single pane ran PAST the keybar, so Claude's status
// line was cut in half and every extra line of a multi-line prompt grew into the band
// behind the bar until you scrolled the page. The pane's CSS calc() subtracts the
// keyboard (--kb-occluded) and a fixed list of chrome constants, and misses whatever is
// not on that list. CMX-102 measures instead: while the keyboard is up, terminals.js
// `_kbPin` → `_kbFitPane` sizes the pane so its box ends exactly at the keybar's top.
//
// Guards (each corrupt→RED):
//   - ⭐ the height math subtracts BOTH the keyboard (via the visual viewport) AND the
//     keybar's own height, from the pane's measured top; desktop and keyboard-closed
//     return null (nothing overridden);
//   - `_kbPin` publishes it: html.kb-fit + --kb-pane-fit-h on a phone with the keyboard
//     up, and clears both when the keyboard closes / on desktop;
//   - a keybar HEIGHT change alone (ResizeObserver — rotation, font size) re-fits;
//   - style.css consumes it: `html.kb-fit .term-single .term-pane { height: var(--kb-pane-fit-h) }`
//     inside the phone media block, after the fullscreen rule it must win against.
// The real-layout half (last terminal row's bottom ≤ keybar top at 390px) lives in
// tests/browser/keybar_pane_fit.test.mjs.
//
// Run: node --test tests/keybar_pane_fit.test.mjs (pytest runs it via tests/test_js_suites.py).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const PANEL = `
<main class="canvas" id="canvas">
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
  <div id="term-keybar"><button class="kb2-key">Esc</button></div>
</div>
</main>`;

const AGENTS = [{ name: 'shell', window_id: '@1', online: true }];

// iPhone 12/13/14 portrait: 844 CSS px tall, a 336px keyboard (accessory row included).
const SCREEN_H = 844;
const KB_H = 336;
const PANE_TOP = 98;
let mobile = true;
let barH = 47;
let vv;            // the fake VisualViewport
let roCallbacks = [];
let terminals;

function fakeFetch(url) {
    const path = String(url);
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (path.startsWith('/api/term/ready')) body = { ready: true };
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
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({
        media: q, get matches() { return /max-width:\s*768px/.test(q) ? mobile : false; },
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    // jsdom has no VisualViewport and no ResizeObserver: both are installed BEFORE
    // terminals.js loads, because it binds to them at module evaluation.
    vv = new dom.window.EventTarget();
    Object.assign(vv, { offsetTop: 0, height: SCREEN_H });
    Object.defineProperty(dom.window, 'visualViewport', { value: vv, configurable: true });
    Object.defineProperty(dom.window, 'innerHeight', { value: SCREEN_H, configurable: true });
    globalThis.ResizeObserver = dom.window.ResizeObserver = class {
        constructor(cb) { this.cb = cb; }
        observe(el) { roCallbacks.push({ el, cb: this.cb }); }
        disconnect() {}
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

// jsdom has no layout: give the keybar and the pane the geometry a phone would.
function stubGeometry() {
    const bar = document.getElementById('term-keybar');
    Object.defineProperty(bar, 'offsetHeight', { configurable: true, get: () => barH });
    const pane = document.querySelector('#term-stage .term-pane');
    assert.ok(pane, 'single mode must have rendered a .term-pane');
    pane.getBoundingClientRect = () => ({ top: PANE_TOP, bottom: PANE_TOP + 600, left: 0, right: 390, width: 390, height: 600 });
}
const keyboard = open => { vv.offsetTop = 0; vv.height = open ? SCREEN_H - KB_H : SCREEN_H; };
const root = () => document.documentElement;
const fitVar = () => root().style.getPropertyValue('--kb-pane-fit-h');

beforeEach(() => {
    mobile = true; barH = 47;
    keyboard(false);
    const sel = document.getElementById('term-agent');
    if (![...sel.options].some(o => o.value === '@1')) sel.add(new window.Option('shell', '@1'));
    sel.value = '@1';
    stubGeometry();
    terminals._kbPin();
});

test('⭐ the fit subtracts the keyboard AND the keybar from the pane\'s measured top', () => {
    const h = terminals._paneFitHeight({
        mobile: true, occluded: KB_H, vvTop: 0, vvHeight: SCREEN_H - KB_H, barHeight: 47, paneTop: PANE_TOP,
    });
    // 844 − 336 (keyboard) − 47 (keybar) − 98 (pane top) = 363. Dropping the keybar
    // term gives 410: the pane's last 47px back behind the bar, the CMX-102 bug.
    assert.equal(h, SCREEN_H - KB_H - 47 - PANE_TOP);
    assert.equal(h, 363);
    // A panned visual viewport: the keybar's top moves with it, so the pane's bottom does too.
    assert.equal(terminals._paneFitHeight({
        mobile: true, occluded: KB_H - 40, vvTop: 40, vvHeight: SCREEN_H - KB_H, barHeight: 47, paneTop: PANE_TOP,
    }), 40 + SCREEN_H - KB_H - 47 - PANE_TOP);
});

test('desktop and keyboard-closed override nothing (null — the CSS calc stays in charge)', () => {
    const base = { occluded: KB_H, vvTop: 0, vvHeight: SCREEN_H - KB_H, barHeight: 47, paneTop: PANE_TOP };
    assert.equal(terminals._paneFitHeight({ ...base, mobile: false }), null, 'desktop subtracts nothing');
    assert.equal(terminals._paneFitHeight({ ...base, mobile: true, occluded: 0, vvHeight: SCREEN_H }), null,
        'keyboard closed: unchanged from today');
});

test('⭐ _kbPin publishes the fit on a phone with the keyboard up, and clears it when it closes', () => {
    assert.ok(!root().classList.contains('kb-fit'), 'keyboard closed: no override');
    keyboard(true);
    vv.dispatchEvent(new window.Event('resize'));
    assert.ok(root().classList.contains('kb-fit'), 'keyboard open on a phone → html.kb-fit');
    assert.equal(fitVar(), `${SCREEN_H - KB_H - 47 - PANE_TOP}px`);
    // The keybar was pinned to the same edge the pane now ends at.
    const bar = document.getElementById('term-keybar');
    assert.equal(parseFloat(bar.style.top) , PANE_TOP + parseFloat(fitVar()),
        'the pane\'s bottom must land exactly on the keybar\'s top');
    keyboard(false);
    vv.dispatchEvent(new window.Event('resize'));
    assert.ok(!root().classList.contains('kb-fit'), 'keyboard closed again → override removed');
    assert.equal(fitVar(), '');
});

test('desktop: even with the visual viewport shrunk (browser zoom), no override', () => {
    mobile = false;
    keyboard(true);
    vv.dispatchEvent(new window.Event('resize'));
    assert.ok(!root().classList.contains('kb-fit'));
    assert.equal(fitVar(), '');
});

test('a keybar HEIGHT change alone re-fits (ResizeObserver on the bar)', () => {
    keyboard(true);
    terminals._kbPin();
    assert.equal(fitVar(), `${SCREEN_H - KB_H - 47 - PANE_TOP}px`);
    const obs = roCallbacks.find(o => o.el === document.getElementById('term-keybar'));
    assert.ok(obs, 'terminals.js must observe #term-keybar with a ResizeObserver');
    barH = 60;   // e.g. a larger font size / rotation grew the bar; no viewport event fires
    obs.cb([]);
    assert.equal(fitVar(), `${SCREEN_H - KB_H - 60 - PANE_TOP}px`);
});

test('style.css applies the fit to the phone pane, after the fullscreen rule', () => {
    const css = readFileSync(new URL('../chela/dashboard/static/style.css', import.meta.url), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '');
    const rule = /html\.kb-fit\s+\.term-single\s+\.term-pane\s*\{\s*height:\s*var\(\s*--kb-pane-fit-h\s*\)\s*;?\s*\}/;
    const m = rule.exec(css);
    assert.ok(m, 'missing `html.kb-fit .term-single .term-pane { height: var(--kb-pane-fit-h) }`');
    const full = css.indexOf('body.term-mobile-full .term-single .term-pane {');
    assert.ok(full >= 0 && m.index > full,
        'the kb-fit rule must come AFTER the fullscreen pane rule (equal specificity — source order decides)');
    const mq = css.lastIndexOf('@media', m.index);
    assert.match(css.slice(mq, m.index), /^@media\s*\(max-width:\s*768px\)/, 'the kb-fit rule must be phone-only');
});
