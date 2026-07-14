// THE WALL, IN A REAL DOM — the two properties a fake DOM cannot prove.
//
// The first cut of this suite hand-rolled an `El` shim and asserted that the room
// accent left the iframes alone. It could not fail: the shim implemented neither
// `innerHTML` nor a live `src`, which are THE two mechanisms that reload a terminal,
// so the very corruption it was written to catch would have sailed through it. Its
// companion was a grep over the source text — a lint, not a proof, and non-transitive
// (a `_repaint()` helper between the caller and `buildWall` defeats it entirely).
//
// So this suite runs the REAL code in a REAL DOM (jsdom): the real `renderTerminals`
// → `buildWall` → real `<iframe>` elements, then the real `/api/rooms` poll path
// (`termTick` → `_refreshRooms` → `applyRoomAccents`), and compares the iframe NODES
// by object identity and by `src`. Corrupt `applyRoomAccents` with an `innerHTML =`
// or a `frame.src =` and this goes red — which is the whole point, and is what the
// shim could never do.
//
// Two properties, both regressions that would be invisible until a user hit them:
//
//   1. 🔴 A ROOM CHANGE RELOADS NO TERMINAL. Room state must never reach `_termSig`
//      and the accent must never rebuild a tile — otherwise two agents starting to
//      talk reloads every live terminal in the fleet.
//   2. 🔴 A WIRE DRAG THAT ENDS OFF-WINDOW MUST NOT LEAVE THE WALL DEAD. The drag
//      puts `.gs-dragging` on the grid, which drops `pointer-events` on EVERY
//      `.term-frame` (so the wire can see the tiles under the iframes). Release the
//      button outside the browser window and `document` never sees the `mouseup` —
//      the class leaks, and every terminal on the wall stops taking clicks, with no
//      self-heal (`buildWall` only runs when the fleet changes).
//
// Run: node --test tests/wall.test.mjs  (pytest runs it via tests/test_js_suites.py;
// it needs `npm ci` for jsdom — CI does that, and CHELA_REQUIRE_JS_TESTS makes a
// missing jsdom a FAILURE, never a silent pass.)
import { before, beforeEach, test } from 'node:test';
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

const AGENTS = [
    { name: 'shell', window_id: '@1', online: true },
    { name: 'shell', window_id: '@2', online: true },
];

// The fleet's HTTP surface, stubbed at `fetch` — everything below it is the real code.
let ROOMS = { rooms: {}, pending: [] };
function fakeFetch(url) {
    const path = String(url);
    const body =
        path.endsWith('/api/agents') ? AGENTS
            : path.endsWith('/api/agents/context') ? {}
                : path.endsWith('/api/rooms') ? ROOMS
                    : path.startsWith('/api/term/ready') ? { ready: true }
                        : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

// jsdom does no layout, so it has no elementFromPoint. The wire uses it to find the
// tile under the cursor; the tests below never need a hit (they end the gesture off
// the wall), so it reports empty stage — which the drop rules already treat as CANCEL.
let HIT_WID = null;
const elementFromPoint = () =>
    (HIT_WID && document.querySelector(`.grid-stack-item[gs-id="${HIT_WID}"]`)) || null;

// GridStack is a vendored browser global. buildWall's DOM work (the part under test)
// is all done before init() is called; the grid itself only needs to not explode.
function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {}, removeWidget(el) { el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let terminals, util, wire;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    // Set the globals BEFORE importing: terminals.js reads localStorage and
    // window.TERMINALS_ENABLED at module scope, exactly as the browser does.
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        // defineProperty, NOT assignment: from node 21 `globalThis.navigator` is an
        // accessor with only a getter, so `globalThis.navigator = x` THROWS. Plain
        // assignment passed on node 20 (a dev laptop) and failed on the CI runner —
        // the local runtime is not the one that governs.
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = elementFromPoint;
    // jsdom has no matchMedia (the phone/desktop split reads it).
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};

    // The dashboard's modules are a cycle (nav ↔ main), so evaluation ORDER is the
    // browser's: index.html loads main.js as the entry, and everything else is
    // pulled in behind it. Import anything else first and nav.js's `let`s are in
    // their TDZ when main's bootstrap runs — a browser-faithful import, not a mock.
    // main.js also arms the app's poll timers, which a test has no use for.
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    wire = await import('../chela/dashboard/static/js/wire.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();   // ONE wall, built once — as in a live session
});

// The wall is built once and never rebuilt (that is the property under test), so a
// test starts from "wired to nothing" rather than from a fresh stage.
beforeEach(async () => {
    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));  // no wire in flight
    ROOMS = { rooms: {}, pending: [] };
    await terminals.termTick();
});

// `.term-frame` is worn by the live iframe AND by the "starting…" placeholder div, so
// the iframe is selected by TAG — a wall of placeholders must never read as a wall of
// terminals (that would make every assertion below vacuously true).
const iframes = () => Array.from(document.querySelectorAll('#term-stage iframe.term-frame'));
const frameFor = wid => document.querySelector(
    `#term-stage .grid-stack-item[gs-id="${wid}"] iframe.term-frame`);
const grid = () => document.querySelector('#term-stage .grid-stack');
// Assigning `src` reloads an iframe EVEN WHEN THE URL IS UNCHANGED — a reload that
// leaves no trace in the DOM afterwards, so no amount of comparing state can see it.
// Watch the assignment itself: the guard is "nobody so much as wrote to .src".
function watchSrcWrites(frame, log) {
    const proto = Object.getOwnPropertyDescriptor(window.HTMLIFrameElement.prototype, 'src');
    Object.defineProperty(frame, 'src', {
        configurable: true,
        get: () => proto.get.call(frame),
        set: v => { log.push(v); proto.set.call(frame, v); },
    });
}

const roomed = (room, wids) => ({
    rooms: { [room]: { created: 1, members: Object.fromEntries(wids.map(w => [w, { name: w }])) } },
    pending: [],
});

// --- 0. the harness itself is not a mock ---------------------------------------

test('the REAL buildWall put REAL iframes on the stage', () => {
    assert.equal(iframes().length, 2, 'two live agents -> two live terminals');
    assert.equal(frameFor('@1').tagName, 'IFRAME');
    assert.match(frameFor('@1').getAttribute('src'), /\/term\/%401\//);
    assert.ok(port('@1'), 'and each tile has the wire port the gesture starts from');
});

// --- 1. 🔴 a room change reloads no terminal ------------------------------------

test('a room change is identity-preserving — the live iframes are NOT recreated', async () => {
    const before = { '@1': frameFor('@1'), '@2': frameFor('@2') };
    assert.equal(iframes().length, 2, 'no iframes on the wall would make this test vacuous');
    const srcs = { '@1': before['@1'].src, '@2': before['@2'].src };
    const gridEl = grid();
    const srcWrites = [];
    Object.values(before).forEach(f => watchSrcWrites(f, srcWrites));

    ROOMS = roomed('wire-shell-shell-ab12cd', ['@1', '@2']);
    await terminals.termTick();          // the REAL poll: /api/rooms -> applyRoomAccents

    // The update REALLY landed — otherwise "nothing was recreated" is a tautology.
    for (const wid of ['@1', '@2']) {
        const content = document.querySelector(`.grid-stack-item[gs-id="${wid}"] .grid-stack-item-content`);
        assert.ok(content.classList.contains('in-room'), `${wid} was painted into the room`);
        assert.match(content.querySelector('.gs-room').textContent, /🔌 wire-shell-shell-ab12cd/);
    }
    // …and it reloaded nothing: same NODES, same src, no src ASSIGNMENT, same grid.
    assert.equal(grid(), gridEl, 'the stage was not re-innerHTML-ed');
    for (const wid of ['@1', '@2']) {
        assert.equal(frameFor(wid), before[wid], `${wid}'s iframe is the SAME NODE — never rebuilt`);
        assert.equal(frameFor(wid).src, srcs[wid], `${wid}'s src was never rewritten`);
        assert.ok(frameFor(wid).isConnected, 'and it never left the document');
    }
    assert.deepEqual(srcWrites, [],
        'nobody so much as ASSIGNED .src — an assignment reloads the terminal even if the URL is identical');
});

test('a third agent joining, and everyone leaving, still reloads nothing', async () => {
    const before = iframes();
    assert.equal(before.length, 2, 'no iframes on the wall would make this test vacuous');
    ROOMS = roomed('ops', ['@1', '@2']);
    await terminals.termTick();
    ROOMS = roomed('ops', ['@1']);        // @2 left
    await terminals.termTick();
    ROOMS = { rooms: {}, pending: [] };   // and the room is gone
    await terminals.termTick();

    // Identity, element by element — NOT deepEqual, which compares two distinct-but-
    // identical iframes as equal and would call a full rebuild "the same nodes".
    const after = iframes();
    assert.equal(after.length, before.length);
    after.forEach((f, i) => assert.equal(f, before[i], 'the SAME iframe node, never rebuilt'));
    const c2 = document.querySelector('.grid-stack-item[gs-id="@2"] .grid-stack-item-content');
    assert.equal(c2.classList.contains('in-room'), false, 'the accent really did clear');
    assert.equal(c2.querySelector('.gs-room').hidden, true);
});

// --- 1b. what the accent SAYS (it is read by a red-weak user) --------------------

test('the accent is never hue-only: the badge carries the room NAME and a glyph', async () => {
    ROOMS = roomed('ops', ['@1', '@2']);
    await terminals.termTick();
    const contents = ['@1', '@2'].map(w =>
        document.querySelector(`.grid-stack-item[gs-id="${w}"] .grid-stack-item-content`));
    for (const content of contents) {
        const badge = content.querySelector('.gs-room');
        assert.equal(badge.hidden, false);
        assert.match(badge.textContent, /🔌 ops/);                      // the NON-HUE cue
        assert.equal(badge.getAttribute('data-room'), 'ops');
        assert.ok(content.style.getPropertyValue('--room-accent'), 'colour is the SECOND signal');
    }
    // Both tiles wear the same accent — that is what "wired together" looks like.
    assert.equal(contents[0].style.getPropertyValue('--room-accent'),
        contents[1].style.getPropertyValue('--room-accent'));
});

test('a member whose window is GONE is simply not painted — no tile, no crash', async () => {
    ROOMS = roomed('ops', ['@1', '@ghost']);     // @ghost has no tile on this wall
    await terminals.termTick();
    const c1 = document.querySelector('.grid-stack-item[gs-id="@1"] .grid-stack-item-content');
    assert.ok(c1.classList.contains('in-room'));
    assert.equal(iframes().length, 2, 'and the wall is intact');
});

// --- 1c. 🔴 COLLAPSING THE SIDEBAR RELOADS NO TERMINAL ---------------------------
//
// The desktop sidebar collapses to an icon rail by putting `sidebar-collapsed` on
// `<body>` (nav.js `_setSidebarCollapsed`) — pure CSS, and it then pokes a `resize`
// so the wall RE-FITS. A re-fit is not a rebuild. But `buildWall` does
// `stage.innerHTML =` whenever its cache key `_termSig` changes, so the moment any
// layout/body state leaks into that key, every collapse RELOADS EVERY LIVE TERMINAL
// IN THE FLEET (the CMX-67 trap).
//
// The grep this replaces (`!terminals.js.includes('sidebar-collapsed')`) tested one
// string literal: fold `document.body.className` into the signature and the grep is
// still green while the fleet reloads on every toggle. So assert what the browser
// does — toggle the real class, run the real render, compare iframe NODE IDENTITY.

test('collapsing the sidebar re-fits the wall — it does NOT rebuild it', async () => {
    const before = { '@1': frameFor('@1'), '@2': frameFor('@2') };
    assert.equal(iframes().length, 2, 'no iframes on the wall would make this test vacuous');
    const srcs = { '@1': before['@1'].src, '@2': before['@2'].src };
    const gridEl = grid();
    const srcWrites = [];
    Object.values(before).forEach(f => watchSrcWrites(f, srcWrites));

    // Exactly what the rail does to the document, both ways.
    for (const collapsed of [true, false]) {
        document.body.classList.toggle('sidebar-collapsed', collapsed);
        window.dispatchEvent(new window.Event('resize'));   // the poke nav.js sends
        await terminals.renderTerminals();                  // the REAL render path

        assert.equal(grid(), gridEl, 'the stage was re-innerHTML-ed — the wall was rebuilt');
        for (const wid of ['@1', '@2']) {
            assert.equal(frameFor(wid), before[wid],
                `${wid}'s iframe is a NEW NODE — collapsing the sidebar reloaded the fleet`);
            assert.equal(frameFor(wid).src, srcs[wid], `${wid}'s src was rewritten`);
            assert.ok(frameFor(wid).isConnected, 'and it never left the document');
        }
    }
    assert.deepEqual(srcWrites, [],
        'nobody so much as ASSIGNED .src — an assignment reloads the terminal even if the URL is identical');
    assert.equal(document.body.classList.contains('sidebar-collapsed'), false, 'left clean');
});

// --- 2. 🔴 the wire drag must never leave the wall dead --------------------------
//
// `.gs-dragging` on the grid = `pointer-events: none` on every `.term-frame`
// (style.css). If it survives the gesture, EVERY terminal on the wall is dead to
// clicks and nothing rebuilds the wall to heal it.

const port = wid => document.querySelector(`.grid-stack-item[gs-id="${wid}"] .gs-port`);
const startWire = wid => {
    // The production binding: the tile's port has onmousedown="chela.wireDragStart(...)".
    window.chela.wireDragStart(new window.MouseEvent('mousedown', {
        bubbles: true, clientX: 10, clientY: 10, buttons: 1,
    }), port(wid), wid);
};
const dragging = () => grid().classList.contains('gs-dragging');

test('a wire drag drops pointer-events on the iframes — that is what makes it work', () => {
    startWire('@1');
    assert.ok(dragging(), 'the iframes must stop eating the mouse mid-wire');
    assert.ok(document.querySelector('#term-stage').classList.contains('wire-live'));
    document.dispatchEvent(new window.MouseEvent('mouseup', { clientX: 10, clientY: 10 }));
    assert.equal(dragging(), false, 'and a normal release gives the wall back');
});

test('a release OUTSIDE the window does not leave the whole wall dead to clicks', () => {
    startWire('@1');
    assert.ok(dragging());
    window.dispatchEvent(new window.Event('blur'));   // drag off the window, release there
    assert.equal(dragging(), false,
        'the wall is dead to clicks until reload: nothing else clears .gs-dragging');
    assert.equal(document.querySelector('#term-stage').classList.contains('wire-live'), false);
});

test('a cancelled pointer (touch stolen, devtools opened) also gives the wall back', () => {
    startWire('@1');
    document.dispatchEvent(new window.Event('pointercancel'));
    assert.equal(dragging(), false);
});

test('a mousemove with no button held cancels the wire — the release happened off-window', () => {
    startWire('@1');
    // The mouse comes back over the page with nothing pressed: the gesture is over,
    // and it ended somewhere we never saw. Cancel — never a room the user didn't drop.
    document.dispatchEvent(new window.MouseEvent('mousemove', {
        clientX: 40, clientY: 40, buttons: 0,
    }));
    assert.equal(dragging(), false, 'the wire must not outlive the button that started it');
    assert.equal(document.querySelector('.wire-overlay'), null, 'and its overlay is gone');
});

test('Escape cancels a wire in flight', () => {
    startWire('@1');
    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));
    assert.equal(dragging(), false);
});
