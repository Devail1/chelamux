// PHONE KEYBAR vs THE PANE'S BOTTOM ROWS — real-DOM guard for CMX-102.
//
// iPhone Safari, keyboard open: the single pane ran PAST the keybar, so Claude's status
// line was cut in half and every extra line of a multi-line prompt grew into the band
// behind the bar until you scrolled the page. The pane's CSS calc() subtracts the
// keyboard (--kb-occluded) and a fixed list of chrome constants, and misses whatever is
// not on that list. CMX-102 measures instead: while the keyboard is up, the keybar island's
// `pinKeybar` (frontend/src/features/keybar, CMX-237) sizes the pane so its box ends exactly at
// the keybar's top.
//
// Guards (each corrupt→RED):
//   - ⭐ the height math subtracts BOTH the keyboard (via the visual viewport) AND the
//     keybar's own height, from the pane's measured top; desktop and keyboard-closed
//     return null (nothing overridden);
//   - `pinKeybar` publishes it: html.kb-fit + --kb-pane-fit-h on a phone with the keyboard
//     up, and clears both when the keyboard closes / on desktop;
//   - ⭐ EVERY re-fit trigger is driven through its real event, with the state moved
//     BEFORE the event and `pinKeybar` never called by hand: visualViewport resize AND
//     scroll, window resize (rotation), a scroll of the phone scroller (.canvas — moves
//     the pane's top), a keybar HEIGHT change alone (ResizeObserver), and a freshly
//     rebuilt pane (renderTerminals). Unwiring any one of them leaves the stale fit
//     in place → RED (DEFEAT_SHAPES #102);
//   - style.css consumes it: `html.kb-fit .term-single .term-pane { height: var(--kb-pane-fit-h) }`
//     inside the phone media block, after the fullscreen rule it must win against.
// The real-layout half (last terminal row's bottom ≤ keybar top at 390px) lives in
// tests/browser/keybar_pane_fit.test.mjs.
//
// Run: pnpm exec vitest run tests/keybar_pane_fit.test.mjs (pytest runs it via tests/test_js_suites.py).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { mountIslands } from './js_helpers/dashboard_dom.mjs';

const PANEL = `
<main class="canvas" id="canvas">
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="keybar-island"></div>
</div>
</main>`;

const AGENTS = [{ name: 'shell', window_id: '@1', online: true }];

// iPhone 12/13/14 portrait: 844 CSS px tall, a 336px keyboard (accessory row included).
const SCREEN_H = 844;
const KB_H = 336;
const PANE_TOP = 98;
let paneTop = PANE_TOP;
let mobile = true;
let barH = 47;
let vv;            // the fake VisualViewport
let roCallbacks = [];
let terminals;
let agentsStore, viewStore;
let keybar;
let model;

function fakeFetch(url) {
    const path = String(url);
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/agents/context')) body = [];
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
    dom.window.matchMedia = q => ({
        media: q, get matches() { return /max-width:\s*768px/.test(q) ? mobile : false; },
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    // jsdom has no VisualViewport and no ResizeObserver: both are installed BEFORE the
    // keybar island mounts, because it binds to them when it mounts.
    vv = new dom.window.EventTarget();
    Object.assign(vv, { offsetTop: 0, height: SCREEN_H });
    Object.defineProperty(dom.window, 'visualViewport', { value: vv, configurable: true });
    Object.defineProperty(dom.window, 'innerHeight', { value: SCREEN_H, configurable: true });
    globalThis.ResizeObserver = dom.window.ResizeObserver = class {
        constructor(cb) { this.cb = cb; }
        observe(el) { roCallbacks.push({ el, cb: this.cb }); }
        disconnect() {}
    };
    // jsdom has no layout. Patched on the PROTOTYPE, not one element: renderTerminals
    // rebuilds the pane, and the rebuilt one must report the same geometry.
    const realRect = dom.window.HTMLElement.prototype.getBoundingClientRect;
    dom.window.HTMLElement.prototype.getBoundingClientRect = function () {
        if (!this.classList.contains('term-pane')) return realRect.call(this);
        return { top: paneTop, bottom: paneTop + 600, left: 0, right: 390, width: 390, height: 600, x: 0, y: paneTop };
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await mountIslands(dom.window);
    keybar = await import('../chela/dashboard/frontend/src/features/keybar/index.ts');
    model = await import('../chela/dashboard/frontend/src/features/keybar/model.ts');
    (await import('../chela/dashboard/frontend/src/boot.ts')).start();
    ({ agents: agentsStore } = await import('../chela/dashboard/frontend/src/shared/store/agents.ts'));
    ({ view: viewStore } = await import('../chela/dashboard/frontend/src/shared/store/view.ts'));
    terminals = await import('../chela/dashboard/frontend/src/features/wall/index.ts');
    viewStore.set('terminals');
    agentsStore.set(AGENTS);
    await terminals.renderTerminals();
});

// jsdom has no layout: give the keybar and the pane the geometry a phone would.
function stubGeometry() {
    const bar = document.getElementById('term-keybar');
    Object.defineProperty(bar, 'offsetHeight', { configurable: true, get: () => barH });
    const pane = document.querySelector('#term-stage .term-pane');
    assert.ok(pane, 'single mode must have rendered a .term-pane');
}
const keyboard = open => { vv.offsetTop = 0; vv.height = open ? SCREEN_H - KB_H : SCREEN_H; };
const root = () => document.documentElement;
const fitVar = () => root().style.getPropertyValue('--kb-pane-fit-h');

beforeEach(() => {
    mobile = true; barH = 47; paneTop = PANE_TOP;
    keyboard(false);
    void terminals.showPane('@1');   // the active pane
    stubGeometry();
    keybar.pinKeybar();
});

test('⭐ the fit subtracts the keyboard AND the keybar from the pane\'s measured top', () => {
    const h = model.paneFitHeight({
        mobile: true, occluded: KB_H, vvTop: 0, vvHeight: SCREEN_H - KB_H, barHeight: 47, paneTop: PANE_TOP,
    });
    // 844 − 336 (keyboard) − 47 (keybar) − 98 (pane top) = 363. Dropping the keybar
    // term gives 410: the pane's last 47px back behind the bar, the CMX-102 bug.
    assert.equal(h, SCREEN_H - KB_H - 47 - PANE_TOP);
    assert.equal(h, 363);
    // A panned visual viewport: the keybar's top moves with it, so the pane's bottom does too.
    assert.equal(model.paneFitHeight({
        mobile: true, occluded: KB_H - 40, vvTop: 40, vvHeight: SCREEN_H - KB_H, barHeight: 47, paneTop: PANE_TOP,
    }), 40 + SCREEN_H - KB_H - 47 - PANE_TOP);
});

test('desktop and keyboard-closed override nothing (null — the CSS calc stays in charge)', () => {
    const base = { occluded: KB_H, vvTop: 0, vvHeight: SCREEN_H - KB_H, barHeight: 47, paneTop: PANE_TOP };
    assert.equal(model.paneFitHeight({ ...base, mobile: false }), null, 'desktop subtracts nothing');
    assert.equal(model.paneFitHeight({ ...base, mobile: true, occluded: 0, vvHeight: SCREEN_H }), null,
        'keyboard closed: unchanged from today');
});

test('⭐ pinKeybar publishes the fit on a phone with the keyboard up, and clears it when it closes', () => {
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

test('the fit floors a fractional box (never 1px into the keybar) and never goes negative', () => {
    // 844 − 336 − 47.4 − 98 = 362.6 → 362: rounding (to 363) would push the last row under the bar.
    assert.equal(model.paneFitHeight({
        mobile: true, occluded: KB_H, vvTop: 0, vvHeight: SCREEN_H - KB_H, barHeight: 47.4, paneTop: 98,
    }), 362);
    assert.equal(model.paneFitHeight({
        mobile: true, occluded: KB_H, vvTop: 0, vvHeight: 100, barHeight: 47, paneTop: PANE_TOP,
    }), 0, 'a pane whose top is already below the keybar gets 0, not a negative height');
});

// ── Every re-fit trigger, driven by its REAL event. Each test moves the state first,
// never calls pinKeybar itself, and asserts the fit the NEW state implies — so a trigger
// that is unwired leaves the old fit (or none) in place and goes RED. ──

const openFit = (bar = 47, top = PANE_TOP) => `${SCREEN_H - KB_H - bar - top}px`;

test('⭐ visualViewport `resize` (the keyboard opening) re-fits', () => {
    assert.ok(!root().classList.contains('kb-fit'));
    keyboard(true);
    vv.dispatchEvent(new window.Event('resize'));
    assert.ok(root().classList.contains('kb-fit'));
    assert.equal(fitVar(), openFit());
});

test('⭐ visualViewport `scroll` (iOS panning the visual viewport) re-fits', () => {
    keyboard(true);
    vv.dispatchEvent(new window.Event('scroll'));
    assert.ok(root().classList.contains('kb-fit'), 'a vv scroll must re-run the fit');
    assert.equal(fitVar(), openFit());
    vv.offsetTop = 30;                    // iOS pans: the keybar's top moves down 30px
    vv.dispatchEvent(new window.Event('scroll'));
    assert.equal(fitVar(), `${30 + SCREEN_H - KB_H - 47 - PANE_TOP}px`);
});

test('⭐ a window `resize` alone (rotation — no visualViewport event) re-fits', () => {
    keyboard(true);
    window.dispatchEvent(new window.Event('resize'));
    assert.ok(root().classList.contains('kb-fit'), 'window resize must re-run the fit');
    assert.equal(fitVar(), openFit());
    keyboard(false);
    window.dispatchEvent(new window.Event('resize'));
    assert.ok(!root().classList.contains('kb-fit'), 'and clear it once the keyboard is gone');
});

test('⭐ a scroll of the phone scroller (.canvas) re-fits from the pane\'s NEW top', () => {
    keyboard(true);
    vv.dispatchEvent(new window.Event('resize'));
    assert.equal(fitVar(), openFit());
    paneTop = 40;                         // the canvas scrolled: the pane moved up 58px
    document.querySelector('.canvas').dispatchEvent(new window.Event('scroll'));
    assert.equal(fitVar(), openFit(47, 40), 'the pane\'s bottom must stay on the keybar after a .canvas scroll');
});

test('⭐ a keybar HEIGHT change alone re-fits (ResizeObserver on the bar)', () => {
    keyboard(true);
    vv.dispatchEvent(new window.Event('resize'));
    assert.equal(fitVar(), openFit());
    const obs = roCallbacks.find(o => o.el === document.getElementById('term-keybar'));
    assert.ok(obs, 'the keybar island must observe #term-keybar with a ResizeObserver');
    barH = 60;
    obs.cb([]);
    assert.equal(fitVar(), openFit(60));
});

test('⭐ a freshly rebuilt pane re-fits if the keyboard is already up (renderTerminals)', async () => {
    keyboard(true);                       // keyboard up, but NO event: only the render may notice
    const before = document.querySelector('#term-stage .term-pane');
    // A second agent appears → the stage signature changes → the single pane is rebuilt.
    agentsStore.set([...AGENTS, { name: 'two', window_id: '@2', online: true }]);
    await terminals.renderTerminals();
    const after = document.querySelector('#term-stage.term-single .term-pane');
    assert.ok(after && after !== before, 'the single pane must have been rebuilt');
    assert.ok(root().classList.contains('kb-fit'), 'the rebuilt pane must be fitted by renderTerminals itself');
    assert.equal(fitVar(), openFit());
    agentsStore.set(AGENTS);
    await terminals.renderTerminals();
});


// ---- CMX-154: the pane's TERMINAL re-fits, not just the pane box ----------------
// iPhone Safari resized the pane (and so its ttyd iframe) but xterm kept its old row
// count, so its bottom rows sat clipped below the tile footer. The fit therefore
// calls ttyd's own `window.term.fit()` after every fit (keybar's pin.ts). Real-layout half:
// tests/browser/first_view_fit.test.mjs.
function paneTerm() {
    const ifr = document.querySelector('#term-stage.term-single .term-pane iframe.term-frame');
    assert.ok(ifr && ifr.contentWindow, 'the single pane must hold a live ttyd iframe');
    const calls = [];
    ifr.contentWindow.term = { fit: () => calls.push(fitVar()) };
    return { ifr, calls };
}

test('⭐ phone: every fit re-fits the pane\'s terminal — keyboard opening AND closing', () => {
    const { calls } = paneTerm();
    keyboard(true);
    vv.dispatchEvent(new window.Event('resize'));
    assert.deepEqual(calls, [openFit()], 'term.fit() runs once, AFTER the open fit is applied');
    keyboard(false);
    vv.dispatchEvent(new window.Event('resize'));
    assert.deepEqual(calls, [openFit(), ''], 'closing the keyboard re-fits too, after the override is cleared');
});

test('desktop: the terminal is left to ttyd\'s own resize handling (no term.fit())', () => {
    const { calls } = paneTerm();
    mobile = false;
    keyboard(true);
    vv.dispatchEvent(new window.Event('resize'));
    window.dispatchEvent(new window.Event('resize'));
    assert.deepEqual(calls, []);
});

test('⭐ a pane\'s iframe finishing its load (first view, tile switch) fits it — no viewport event', () => {
    const { ifr, calls } = paneTerm();
    keyboard(true);                       // keyboard already up, no resize/scroll event
    root().classList.remove('kb-fit');
    root().style.removeProperty('--kb-pane-fit-h');
    ifr.dispatchEvent(new window.Event('load'));
    assert.ok(root().classList.contains('kb-fit'), 'the load must apply the fit');
    assert.deepEqual(calls, [openFit()], 'and re-fit the terminal that just loaded');
});

test('a terminal that is not booted yet (no term / no fit) or an unreadable iframe never breaks the fit', () => {
    const { ifr } = paneTerm();
    ifr.contentWindow.term = {};
    keyboard(true);
    assert.doesNotThrow(() => keybar.pinKeybar());   // direct: dispatchEvent would swallow a throw
    delete ifr.contentWindow.term;
    assert.doesNotThrow(() => keybar.pinKeybar());
    Object.defineProperty(ifr, 'contentWindow', { configurable: true, get() { throw new Error('SecurityError: cross-origin frame'); } });
    keyboard(false);
    assert.doesNotThrow(() => keybar.pinKeybar());
    assert.ok(!root().classList.contains('kb-fit'), 'the box fit is still applied before the terminal is touched');
    delete ifr.contentWindow;
});
