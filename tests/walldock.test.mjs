// LAZY WALL TILES, IN A REAL DOM — a dispatched worker opens MINIMIZED, and POPS OUT
// when it wants a human (CMX-76). Same rule as the Telegram lazy-bind, second surface.
//
// This runs the REAL terminals.js in jsdom (like tests/wall.test.mjs): the real
// `renderTerminals` → `buildWall` → real `.grid-stack-item` tiles, the real `termTick`
// poll off a stubbed `/api/agents`, the real `minimizePane` / `restoreFromDock` / dock
// chips. Nothing here greps the source: every assertion is on what a browser would show.
//
// Four properties, and three of them are about NOT DOING SOMETHING — which is exactly
// what a source-grep can never prove:
//
//   1. 🔴 A WORKING WORKER TAKES NO TILE. It opens minimized (chip in the dock), while a
//      human's session keeps its tile. This is the feature.
//   2. 🔴 A BLOCKED WORKER POPS OUT — and its terminal is NEVER RELOADED doing it. The
//      whole point of minimize-to-dock is that the ttyd iframe stays attached and live;
//      pop a worker out by rebuilding its tile and you have thrown away the scrollback
//      of the very agent you are about to talk to. Compared by iframe NODE IDENTITY.
//   3. 🔴 IT NEVER FIGHTS THE HUMAN. Restore a docked worker by hand and the next poll
//      must not re-dock it. A stateless "dispatched && !needs_human → minimize" rule
//      passes tests 1 and 2 and makes the wall unusable — you could not keep a worker
//      on screen for four seconds. The decision is taken ONCE per window.
//   4. 🔴 A RECYCLED @N NEVER HIDES A HUMAN'S WINDOW. tmux hands ids out afresh after a
//      server restart, and the dock state is persisted in localStorage keyed by id. A
//      wid that is no longer dispatched is disowned — and un-hidden.
//
// Run: node --test tests/walldock.test.mjs  (pytest runs it via tests/test_js_suites.py;
// it needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

// The terminals panel, as index.html emits it (only the ids terminals.js reaches for).
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

const HUMAN = '@1';     // the orchestrator — a human's session, never touched
const WORKER = '@9';    // a dispatcher-spawned worktree agent
const WORKER2 = '@11';  // a second one, spawned mid-suite (test 4)

// The fleet, mutated per-test to drive the state machine. `dispatched` + `needs_human`
// are what /api/agents really ships (chela/dashboard/app.py::api_agents). `session_status`
// stays 'busy' throughout ON PURPOSE: a worker stopped at a Bash PERMISSION gate is busy
// to `claude agents --json`, so every "it blocked" below is carried by needs_human alone —
// which is the bit a status-only wall would miss.
let AGENTS = [];
const worker = (wid, name, { dispatched = true, needsHuman = false } = {}) => (
    { name, window_id: wid, online: true, session_status: 'busy',
        claude_running: true, dispatched, needs_human: needsHuman });
const fleet = (opts, extra = []) => [
    { name: 'orchestrator', window_id: HUMAN, online: true, session_status: 'idle',
        claude_running: true, dispatched: false, needs_human: false },
    worker(WORKER, 'cmx-76', opts),
    ...extra,
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

// GridStack is a vendored browser global. The tile DOM (what is asserted below) is all
// real; the engine only has to not explode — EXCEPT for one signature that is load-bearing
// here: `removeWidget(el, removeDOM)`. Minimize calls it with `false`, and that second
// argument IS the feature — it un-manages the tile while LEAVING THE ELEMENT (and its live
// ttyd iframe) in the DOM. A fake that always removes the element would delete the very
// iframe whose survival property 2 exists to prove.
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
    // jsdom ships no canvas, and `getContext('2d')` returns null. The tab-signal badge
    // (util.js::_drawFavicon) paints one whenever the "needs you" count goes ABOVE ZERO —
    // which, in this suite, is the pop-out itself. A no-op 2D context keeps the assertions
    // about the WALL rather than about a canvas polyfill.
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};

    // Browser-faithful import order: main.js is the entry, everything else comes in
    // behind it through the module graph (nav ↔ main is a cycle — import anything else
    // first and nav.js's `let`s are in their TDZ). main.js also arms the poll timers.
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');

    // The worker is WORKING when the wall first paints — the state the feature is about.
    AGENTS = fleet();
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

const tile = wid => document.querySelector(`#term-stage .grid-stack-item[gs-id="${wid}"]`);
const frameFor = wid => document.querySelector(
    `#term-stage .grid-stack-item[gs-id="${wid}"] iframe.term-frame`);
const chip = wid => document.querySelector(`#term-min-dock .min-chip[data-wid="${wid}"]`);
// Minimize hides the tile IN PLACE (display:none) rather than detaching it — that is what
// keeps the terminal alive. So "docked" is: element present, hidden, out of the engine.
const isDocked = wid => {
    const el = tile(wid);
    return !!el && el.dataset.minimized === '1' && el.style.display === 'none' && !!chip(wid);
};
const isOnWall = wid => {
    const el = tile(wid);
    return !!el && el.dataset.minimized !== '1' && el.style.display !== 'none' && !chip(wid);
};

// 1 — THE FEATURE. The dispatcher's worker never took a tile; the human's session did.
test('a working dispatched agent opens MINIMIZED — the human session keeps its tile', () => {
    assert.ok(isDocked(WORKER), 'the dispatched worker should have opened into the dock');
    assert.ok(isOnWall(HUMAN), "a human's session is never docked by the wall");
    // …and its terminal is LIVE the whole time: the iframe was never detached, so you can
    // still open the chip and read what it is doing. A dock is not a kill.
    assert.ok(frameFor(WORKER), 'the docked pane must keep its live ttyd iframe');
});

// 2 — THE POP-OUT, and the reload that must not happen with it.
test('it POPS OUT when it blocks on a human — without reloading its terminal', async () => {
    const before = frameFor(WORKER);
    assert.ok(before);

    AGENTS = fleet({ needsHuman: true });   // the worker hits a permission gate
    await terminals.termTick();

    assert.ok(isOnWall(WORKER), 'a blocked worker must be back on the wall');
    // 🔴 The same iframe NODE, not an equal one: rebuilding the tile would reload ttyd and
    // throw away the scrollback of the agent you are about to answer.
    assert.equal(frameFor(WORKER), before, 'popping out must not rebuild the pane');
    // It also wears the amber "needs you" marker — which comes from needs_human, NOT from
    // session_status (still 'busy' here: a permission gate is invisible to `claude agents
    // --json`). Paint it off status alone and the pane pops out looking calm.
    assert.ok(tile(WORKER).querySelector('.grid-stack-item-content').classList
        .contains('term-waiting'), 'a blocked pane must be flagged as waiting');
});

// 3 — IT NEVER FIGHTS THE HUMAN. The decision is taken once per window, not per poll.
test('once popped, the wall never re-docks it — the human owns the tile from then on', async () => {
    // It is on the wall and no longer blocked (the human answered the gate). A stateless
    // "dispatched && !needs_human → minimize" rule would yank it back into the dock here,
    // mid-conversation, every 4 seconds.
    AGENTS = fleet({ needsHuman: false });
    await terminals.termTick();
    assert.ok(isOnWall(WORKER), 'an answered worker must not be re-docked underneath you');

    // And the converse: minimize it BY HAND (the real header button's real handler) and
    // the wall leaves it alone too — including when it blocks again. Past the first
    // pop-out, the tile is the human's to place.
    window.chela.termMinFor(tile(WORKER).querySelector('.gs-min-btn'));
    assert.ok(isDocked(WORKER));
    AGENTS = fleet({ needsHuman: true });
    await terminals.termTick();
    assert.ok(isDocked(WORKER), "a human's own minimize is never overridden");
});

// 4 — A RECYCLED @N MUST NEVER LEAVE A HUMAN'S WINDOW HIDDEN.
test('a wid that stops being dispatched is disowned — and un-hidden', async () => {
    // The dock state is persisted in localStorage keyed by window id, and tmux hands `@N`
    // out afresh after a server restart — so the pane behind a stale "docked" can turn out
    // to be a HUMAN's terminal. The wall hid it; the wall must give it back. (It gives back
    // only what IT hid: a pane the human minimized stays minimized — see test 3.)
    AGENTS = fleet({ needsHuman: true }, [worker(WORKER2, 'cmx-77')]);
    await terminals.termTick();
    assert.ok(isDocked(WORKER2), 'a freshly spawned worker docks like any other');

    AGENTS = fleet({ needsHuman: true }, [worker(WORKER2, 'cmx-77', { dispatched: false })]);
    await terminals.termTick();
    assert.ok(isOnWall(WORKER2), 'a window the dispatcher does not own must never stay hidden');
});
