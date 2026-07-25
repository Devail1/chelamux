// PER-CLIENT `ignore-size` REPORTING, IN A REAL DOM (CMX-175).
//
// `window-size largest` (scripts/agent-terminals.sh) sizes a grouped session's shared
// window to its BIGGEST attached tmux client, so a phone pane opened alongside a big
// desktop wall renders at the desktop's geometry. The fix is per-client `ignore-size`:
// a tile that ISN'T being watched (tab backgrounded, pane minimized) reports that to
// the backend (POST /api/term/<wid>/watch), which flags that ONE tmux client out of the
// `largest` computation — see chela/dashboard/app.py's `api_term_watch` / `term_ws`.
//
// This runs the REAL terminals.js in jsdom (like tests/wall.test.mjs / walldock.test.mjs):
// the real `renderTerminals`, the real visibilitychange listener, the real `minimizePane`
// / `toggleDockChip` (via the same `window.chela` surface the pane buttons call). Nothing
// here greps the source — every assertion is on an actual POST the browser would send.
//
// Two properties:
//
//   1. 🔴 A CID RIDES ON EVERY IFRAME'S SRC. Without it the backend cannot match a
//      "stopped watching" report to the ONE tmux client that sent it, and would have to
//      guess (or hit every client on the shared window, which is the bug this fixes).
//   2. 🔴 BACKGROUNDING THE TAB OR DOCKING A TILE REPORTS watching:false FOR THAT WID —
//      and coming back / restoring reports watching:true — WITHOUT reloading the pane
//      (no iframe src reassignment, no lost scrollback).
//
// Run: node --test tests/term_ignore_size.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

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

const AGENTS = [
    { name: 'shell-1', window_id: '@1', online: true },
    { name: 'shell-2', window_id: '@2', online: true },
];

// Every POST to /api/term/<wid>/watch, in call order — the surface under test.
let watchCalls = [];
function fakeFetch(url, opts) {
    const path = String(url);
    if (path.match(/\/api\/term\/[^/]+\/watch$/)) {
        watchCalls.push({ wid: decodeURIComponent(path.match(/\/api\/term\/([^/]+)\/watch$/)[1]),
            body: JSON.parse(opts.body) });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, tracked: true }) });
    }
    const body =
        path.endsWith('/api/agents') ? AGENTS
            : path.endsWith('/api/agents/context') ? {}
                : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                    : path.startsWith('/api/term/ready') ? { ready: true }
                        : path.startsWith('/api/term/clients') ? {}
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

let terminals, util;
let visState = 'visible';

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
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};

    // document.visibilityState is a getter-only accessor; redefine it (configurable in
    // jsdom) so tests can drive the SAME visibilitychange listener a real backgrounded
    // tab fires, rather than calling internal functions directly.
    Object.defineProperty(document, 'visibilityState', { get: () => visState, configurable: true });
    Object.defineProperty(document, 'hidden', { get: () => visState === 'hidden', configurable: true });

    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

beforeEach(async () => {
    visState = 'visible';
    watchCalls = [];
});

const frameFor = wid => document.querySelector(
    `#term-stage .grid-stack-item[gs-id="${wid}"] iframe.term-frame`);

test('every live tile iframe carries a cid on its src', () => {
    for (const wid of ['@1', '@2']) {
        const src = frameFor(wid).getAttribute('src');
        assert.match(src, /[?&]cid=[^&]+/, `${wid}'s iframe src must carry a cid query param`);
    }
});

test('backgrounding the tab reports watching:false for every live tile — no reload', async () => {
    const before1 = frameFor('@1'), before2 = frameFor('@2');
    visState = 'hidden';
    document.dispatchEvent(new window.Event('visibilitychange'));
    await Promise.resolve();   // let the fire-and-forget fetch promise settle

    const byWid = Object.fromEntries(watchCalls.map(c => [c.wid, c.body]));
    assert.equal(byWid['@1'].watching, false);
    assert.equal(byWid['@2'].watching, false);
    // 🔴 no reconnect — unlike the 45s teardown, this must never touch .src.
    assert.equal(frameFor('@1'), before1, 'must not rebuild/reload the pane');
    assert.equal(frameFor('@2'), before2, 'must not rebuild/reload the pane');
});

test('foregrounding the tab again reports watching:true', async () => {
    visState = 'hidden';
    document.dispatchEvent(new window.Event('visibilitychange'));
    await Promise.resolve();
    watchCalls = [];

    visState = 'visible';
    document.dispatchEvent(new window.Event('visibilitychange'));
    await Promise.resolve();

    const byWid = Object.fromEntries(watchCalls.map(c => [c.wid, c.body]));
    assert.equal(byWid['@1'].watching, true);
    assert.equal(byWid['@2'].watching, true);
});

test('minimizing a tile to the dock reports watching:false for THAT wid only', async () => {
    const btn = document.querySelector(
        '#term-stage .grid-stack-item[gs-id="@1"] .gs-min-btn');
    window.chela.termMinFor(btn);
    await Promise.resolve();

    const calls = watchCalls.filter(c => c.wid === '@1');
    assert.ok(calls.length >= 1, 'minimizing must report a watch state for @1');
    assert.equal(calls.at(-1).body.watching, false);
    assert.ok(!watchCalls.some(c => c.wid === '@2'), 'a sibling tile must be untouched');
});

test('restoring a docked tile reports watching:true again', async () => {
    const btn = document.querySelector(
        '#term-stage .grid-stack-item[gs-id="@1"] .gs-min-btn');
    window.chela.termMinFor(btn);
    await Promise.resolve();
    watchCalls = [];

    window.chela.toggleDockChip('@1');
    await Promise.resolve();

    const calls = watchCalls.filter(c => c.wid === '@1');
    assert.ok(calls.length >= 1, 'restoring must report a watch state for @1');
    assert.equal(calls.at(-1).body.watching, true);
});
