// SIDEBAR ROW CLICK IS WALL-CONTEXT-AWARE (CMX-139), IN A REAL DOM.
//
// Before this, clicking a SESSIONS row always called focusPaneByWid — restoring a
// docked pane and focusing it, but also re-flashing a pane that was already open and
// visible, which reads as a no-op click. CMX-137's on-wall ring cue already told you
// which case you were in; this makes the click ITSELF match what the cue promises:
//
//   1. wall visible + pane open on it (rendered, not minimized) -> MINIMIZE (a click
//      hides an open pane).
//   2. not on the wall at all (another tab, or single-terminal mode) -> switch to wall
//      mode and focus/restore the pane there.
//   3. wall visible but the pane is minimized -> restore + focus (unchanged).
//
// Same rig as tests/sidebar_wall_indicator.test.mjs: the REAL terminals.js (a REAL wall
// built out of a fake GridStack) alongside the REAL nav.js, so the behavior is asserted
// against the SAME live module state the sidebar and wall themselves use.
//
// Run: node --test tests/sidebar_click_context.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
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
<div class="panel" id="panel-agents"></div>
<aside class="sidebar">
  <section class="side-section">
    <span class="side-count" id="hdr-agents">-/-</span>
    <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
  </section>
</aside>`;

const HUMAN = '@1';    // opens straight onto the wall, never docked
const WORKER = '@9';   // a dispatcher-spawned worker: opens minimized, may pop out

const AGENTS = [
    { name: 'orchestrator', window_id: HUMAN, online: true, session_status: 'idle',
        claude_running: true, dispatched: false, needs_human: false },
    { name: 'cmx-139', window_id: WORKER, online: true, session_status: 'busy',
        claude_running: true, dispatched: true, needs_human: false },
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
    dom.window.HTMLElement.prototype.scrollIntoView = () => {};
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
    util.setAgentsCache(AGENTS);
});

// Every test starts wall-visible with both panes open (not docked), on the terminals
// tab, in wall mode — the baseline "on-wall" state each case then deviates from.
beforeEach(async () => {
    util.setCurrentTab('terminals');
    window.chela.setTermMode('wall');
    await terminals.renderTerminals();
    [HUMAN, WORKER].forEach(wid => { if (terminals._minimized.has(wid)) terminals._minimized.delete(wid); });
    await terminals.renderTerminals();
});

test('1. wall visible + pane open on it -> click MINIMIZES it', () => {
    assert.ok(!terminals._minimized.has(HUMAN), 'setup: orchestrator must start open, not docked');
    window.chela.selectAgent('orchestrator');
    assert.ok(terminals._minimized.has(HUMAN),
        'clicking an already-open pane while the wall is visible must dock it, not just re-focus it');
});

// focusPaneByWid (invoked by selectAgent whenever it doesn't minimize) defers its
// restore/scroll/focus by 60ms so selectView's render settles first — settle past that
// before asserting on its effects.
const settleFocus = () => new Promise(r => setTimeout(r, 120));

test('2. wall visible + pane minimized -> click restores + focuses (unchanged)', async () => {
    window.chela.termMinFor(document.querySelector(`#term-stage .grid-stack-item[gs-id="${WORKER}"] .gs-min-btn`));
    assert.ok(terminals._minimized.has(WORKER), 'setup: worker must be docked before the click');
    window.chela.selectAgent('cmx-139');
    await settleFocus();
    assert.ok(!terminals._minimized.has(WORKER),
        'clicking a docked pane while the wall is visible must restore it');
});

test('3. not on the wall (another tab) -> click switches to the wall AND focuses, never minimizes', async () => {
    util.setCurrentTab('agents');   // simulate being on a different tab
    assert.ok(!terminals._minimized.has(HUMAN), 'setup: orchestrator is open on the wall underneath');
    window.chela.selectAgent('orchestrator');
    assert.equal(util.currentTab, 'terminals', 'the click must switch back to the wall tab');
    await settleFocus();
    assert.ok(!terminals._minimized.has(HUMAN),
        'an open pane must be focused, NOT minimized, when the click is what brings the wall into view');
});

test('4. single-terminal mode -> click switches to wall mode, not just the tab', async () => {
    window.chela.setTermMode('single');
    assert.ok(!terminals.isWallVisible(), 'setup: single-terminal mode must not read as wall-visible');
    window.chela.selectAgent('orchestrator');
    assert.ok(terminals.isWallVisible(),
        'clicking a row in single-terminal mode must switch the wall into (grid) wall mode');
    await settleFocus();
});
