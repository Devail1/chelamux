// WALL NAV UX, IN A REAL DOM. Started as CMX-108's three polish parts on the Wall's
// toolbar and pane headers; CMX-111 corrected which cluster actually gets folded —
// Liav wanted the PER-PANE title-bar's Wire/Share/Orchestrator/Pin folded behind one
// "⋯", not the wall TOOLBAR's grid-preset picker + lock (that fold is reverted: the
// toolbar is back to inline, same as before CMX-108). The per-pane layout PIN and the
// Alt+1..9 keyboard-fast pane switcher are unrelated to either fold and are unchanged.
// CMX-112 adds properties 5 and 6 below: the switcher used to be cosmetic (it flashed
// a border but never routed keystrokes into the terminal) and one-shot (it never
// re-armed once focus moved into a pane's iframe). CMX-114 corrects two things Liav
// found live-broken after testing cmx-111/cmx-112 (properties 7 and 8): the overflow
// menu was icon-only instead of a labeled list like the topbar's, and — the sharper
// bug — cmx-112's jsdom guards for 5/6 were GREEN while Alt+N was DEAD in a real
// browser (a synthetic dispatchEvent reaches a bubble-phase listener; a real keydown
// into xterm's helper textarea does not). Properties 5 and 6 below are corrected in
// place to assert the ACTUAL fix (both focus calls; capture phase +
// stopImmediatePropagation), not the old, provably-insufficient behaviour.
//
// Runs the REAL terminals.js in jsdom (same approach as tests/wall.test.mjs and
// tests/walldock.test.mjs): real `renderTerminals` -> `buildWall` -> real
// `.grid-stack-item` tiles, real header buttons. The GridStack fake here is a little
// fuller than the other suites' (it tracks x/y/w/h per node and reflects `update()`
// back onto the DOM's gs-x/gs-y/gs-w/gs-h attributes) because the pin test needs to
// observe applyGridLayout's real reflow decisions, not just "did it not crash".
//
// Eight properties, each a regression that would ship silently:
//
//   1. 🔴 THE PANE "⋯" ACTUALLY GATES WIRE/SHARE/ORCHESTRATOR/PIN, AS LABELED ROWS.
//      Each pane's overflow menu starts hidden; togglePaneOverflow reveals
//      .gs-port/.gs-share-btn/.gs-orch-btn/.gs-pin-btn INSIDE it (not loose in
//      .gs-win-ctl — the whole point of the fold), and they stay real, working
//      buttons — not decoration. CMX-114: each is now a `.popover-item.ov-item` row
//      (icon + TEXT LABEL), the same classes the topbar's `#primary-menu` uses — not
//      the old icon-only horizontal strip.
//   2. 🔴 THE WALL TOOLBAR'S GRID PRESETS + LOCK STAYED INLINE. No "Layout" menu to open
//      — #term-grid-presets and #term-lock-btn are directly reachable in the toolbar,
//      proving the CMX-108 toolbar fold was actually reverted, not just hidden.
//   3. 🔴 A PINNED PANE'S GEOMETRY IS NEVER TOUCHED BY A GRID PRESET. applyGridLayout
//      reflows every OTHER pane; a pinned one keeps its exact gs-x/gs-y/gs-w/gs-h.
//   4. 🔴 ALT+N JUMPS TO THE RIGHT PANE, PLAIN "N" DOES NOT. The badge numbering and
//      the shortcut's target must never drift apart, and the shortcut must be gated on
//      Alt (a bare digit is normal terminal input and must reach the pane untouched).
//   5. 🔴 ALT+N ROUTES ACTUAL KEYSTROKES, NOT JUST A HIGHLIGHT. `focusPaneByWid` must
//      call BOTH the iframe ELEMENT'S own `.focus()` AND the xterm Terminal's OWN
//      `.focus()` (exposed as `contentWindow.term`) — CMX-114: contentWindow.focus()
//      alone flashes a border but leaves every keystroke going nowhere when switching
//      FROM another focused pane; `ifr.focus()` is required, not a same-tick fallback.
//   6. 🔴 ALT+N RE-ARMS AFTER FOCUS MOVES INTO A PANE, PREEMPTING XTERM. A same-origin
//      iframe's keydown never bubbles to the parent document, so the parent-level
//      listener alone goes deaf to the next Alt+N once a pane has focus — the switcher
//      must also be wired inside each pane's own document. CMX-114: that iframe-side
//      listener must be CAPTURE-phase with `stopImmediatePropagation`, because a real
//      keydown reaches xterm's own handler FIRST on the bubble phase (a synthetic
//      dispatchEvent does not — the exact green-but-dead trap this corrects).
//   7. 🔴 THE OVERFLOW ROWS ARE ICON + LABEL, NOT ICON-ONLY. Dropping either the
//      lucide `<svg>` or the text label on any row is a silent regression to the old
//      (rejected) icon-only strip.
//   8. 🔴 THE IFRAME LISTENER'S CAPTURE REGISTRATION IS OBSERVABLE, NOT ASSUMED. A
//      jsdom guard cannot prove a real xterm gets preempted (no real xterm in jsdom)
//      — it can only assert the WIRING: capture=true at registration, and
//      preventDefault + stopImmediatePropagation on a matched Alt-digit. The runtime
//      preemption itself is manually verified on the live wall (see the PR).
//
// CMX-116 adds properties 9-11: the palette (not Alt+N) is now the PRIMARY, discoverable
// pane switcher. 9 — Ctrl/⌘+K, injected the same way as Alt+N (property 6's iframe-side
// listener, generalized to carry BOTH shortcuts rather than forking a second injector),
// opens the palette from inside a focused pane. 10 — with an empty query the palette
// floats live, unminimized wall panes to the top (wall order, attention-first), with a
// divider before the rest of the list; typing falls back to the normal fuzzy match over
// everything, panes included. 11 — a CMX-114 regression: the "⋯" dropdown's `.on` fill
// (Orchestrator/Share/Pin) never painted because `.gs-keys button.popover-item`'s
// `background: none` (specificity 0,2,1) outranked the plain `.on` rules (0,2,0); fixed by
// repeating the `.gs-keys button` prefix on each `.on` rule (0,3,1) — asserted here as a
// static CSS-source fact (jsdom can't resolve cascade specificity), per the honest
// disclaimer in the PR: this is NOT a substitute for the manual live-wall check.
//
// Run: node --test tests/wallnav.test.mjs (pytest runs it via tests/test_js_suites.py;
// it needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const CSS = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

// The terminals panel, as index.html emits it (only the ids terminals.js / nav.js
// reach for) — grid presets + lock sit directly on the toolbar, inline (no Layout menu).
const PANEL = `
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid">
    <span id="term-grid-presets"></span>
    <button id="term-lock-btn" onclick="chela.toggleWallLock(this)"></button>
  </span>
  <button id="term-new-shell"></button>
  <div id="term-switcher"></div>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>
<div class="palette-overlay" id="palette">
  <div class="palette">
    <input id="palette-input">
    <div id="palette-list"></div>
  </div>
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
    // No pane overflow menu should carry over open between tests.
    document.querySelectorAll('.pane-overflow-menu').forEach(m => { m.hidden = true; });
    // focusPaneByWid's flash clears itself after 1100ms, but tests only wait 120ms —
    // without this a later test's flashed(wid) assertion could pass on a PRIOR
    // test's leftover flash instead of its own dispatch (a vacuous pass).
    document.querySelectorAll('.pane-flash').forEach(el => el.classList.remove('pane-flash'));
});

const tile = wid => document.querySelector(`#term-stage .grid-stack-item[gs-id="${wid}"]`);
const geom = wid => {
    const el = tile(wid);
    return el && ['x', 'y', 'w', 'h'].map(k => el.getAttribute('gs-' + k)).join(',');
};

// 1 — THE PANE FOLD. One "⋯" trigger per pane; Wire/Share/Orchestrator/Pin live
// behind it, not loose in the header — and stay the SAME real buttons, just moved.
test('a pane\'s "⋯" overflow is hidden by default, and opening it reveals Wire/Share/Orchestrator/Pin', () => {
    const wid = '@1';
    const overflowBtn = tile(wid).querySelector('.gs-overflow-btn');
    const menu = tile(wid).querySelector('.pane-overflow-menu');
    assert.ok(overflowBtn, 'every wall tile gets a "⋯" trigger');
    assert.ok(menu, 'every wall tile gets an overflow menu');
    assert.equal(menu.hidden, true, 'starts closed');
    assert.equal(overflowBtn.getAttribute('aria-expanded'), 'false');

    window.chela.togglePaneOverflow({ stopPropagation() {} }, overflowBtn);
    assert.equal(menu.hidden, false, 'opens on click');
    assert.equal(overflowBtn.getAttribute('aria-expanded'), 'true');
    // The controls the header used to show inline now live INSIDE this pane's menu.
    assert.ok(menu.querySelector('.gs-port'), 'wire port is inside the menu');
    assert.ok(menu.querySelector('.gs-share-btn'), 'share toggle is inside the menu');
    assert.ok(menu.querySelector('.gs-orch-btn'), 'orchestrator toggle is inside the menu');
    assert.ok(menu.querySelector('.gs-pin-btn'), 'pin toggle is inside the menu');

    window.chela.togglePaneOverflow({ stopPropagation() {} }, overflowBtn);
    assert.equal(menu.hidden, true, 'closes on a second click');
    assert.equal(overflowBtn.getAttribute('aria-expanded'), 'false');
});

// 7 — THE RESTYLE (CMX-114). Liav wanted the fold to look like the topbar's labeled
// #primary-menu, not the icon-only strip cmx-111 shipped: a vertical list of
// `.popover-item.ov-item` rows, each a lucide icon PLUS a text label, still wired to
// the same state-bearing buttons.
test('the "⋯" overflow renders a labeled vertical list, like the topbar #primary-menu — not an icon-only strip', () => {
    const wid = '@3';
    const overflowBtn = tile(wid).querySelector('.gs-overflow-btn');
    const menu = tile(wid).querySelector('.pane-overflow-menu');
    window.chela.togglePaneOverflow({ stopPropagation() {} }, overflowBtn);

    // Reuses the topbar's exact classes, not a bespoke lookalike.
    assert.ok(menu.classList.contains('overflow-menu'),
        'the menu must carry the topbar #primary-menu\'s `overflow-menu` class');

    const rows = menu.querySelectorAll('.popover-item.ov-item');
    assert.equal(rows.length, 4, 'all four actions (Wire/Share/Orchestrator/Pin) render as labeled rows');
    rows.forEach(row => {
        assert.ok(row.querySelector('svg'), 'every row must carry a lucide icon — dropping it is the icon-only regression');
        assert.ok(row.textContent.trim().length > 0, 'every row must carry a non-empty text label, not just an icon');
    });

    // The state-bearing elements are the SAME rows (restyled in place), not a
    // decorative wrapper around a still-hidden original button — a relabel that
    // dropped the real element (breaking _updateShareBtns/_updateOrchBtns/the pin
    // toggle's DOM queries) would fail here.
    const share = menu.querySelector('.gs-share-btn.popover-item.ov-item');
    const orch = menu.querySelector('.gs-orch-btn.popover-item.ov-item');
    const pinRow = menu.querySelector('.gs-pin-btn.popover-item.ov-item');
    assert.ok(share, 'the Share row must still BE .gs-share-btn (the state-bearing element)');
    assert.ok(orch, 'the Orchestrator row must still BE .gs-orch-btn (the state-bearing element)');
    assert.ok(pinRow, 'the Pin row must still BE .gs-pin-btn (the state-bearing element)');
    assert.equal(share.getAttribute('data-wid'), wid);
    assert.equal(orch.getAttribute('data-wid'), wid);

    // Toggling still flips aria-pressed/`.on` on the right element (the relabel must
    // not have broken the wiring _updateShareBtns/_updateOrchBtns/termPinToggle rely on).
    assert.equal(pinRow.getAttribute('aria-pressed'), 'false');
    window.chela.termPinToggle(pinRow, wid);
    assert.equal(pinRow.getAttribute('aria-pressed'), 'true', 'pin row still toggles aria-pressed on itself');
    assert.ok(pinRow.classList.contains('on'), 'pin row still gets the `.on` state class');
    window.chela.termPinToggle(pinRow, wid);   // leave state as found for later tests
    assert.equal(pinRow.getAttribute('aria-pressed'), 'false');

    window.chela.togglePaneOverflow({ stopPropagation() {} }, overflowBtn);
});

// 1b — Only one pane's overflow is open at a time (opening a second closes the first).
test('opening one pane\'s "⋯" overflow closes any other pane\'s open overflow', () => {
    const btn1 = tile('@1').querySelector('.gs-overflow-btn');
    const btn2 = tile('@2').querySelector('.gs-overflow-btn');
    const menu1 = tile('@1').querySelector('.pane-overflow-menu');
    const menu2 = tile('@2').querySelector('.pane-overflow-menu');

    window.chela.togglePaneOverflow({ stopPropagation() {} }, btn1);
    assert.equal(menu1.hidden, false);

    window.chela.togglePaneOverflow({ stopPropagation() {} }, btn2);
    assert.equal(menu2.hidden, false, 'the second pane\'s menu opens');
    assert.equal(menu1.hidden, true, 'the first pane\'s menu must close — only one open at a time');
});

// 2 — THE TOOLBAR REVERT. No Layout menu: the grid presets + lock are directly on the
// toolbar, same as before CMX-108 folded them.
test('the wall toolbar has no Layout menu — grid presets and lock are inline', () => {
    assert.equal(document.getElementById('layout-menu'), null, 'the Layout popover must not exist');
    assert.equal(window.chela.openLayoutMenu, undefined, 'openLayoutMenu must not be exposed');
    assert.equal(window.chela.hideLayoutMenu, undefined, 'hideLayoutMenu must not be exposed');

    const presets = document.getElementById('term-grid-presets');
    const lock = document.getElementById('term-lock-btn');
    assert.ok(presets, '#term-grid-presets exists on the toolbar');
    assert.ok(lock, '#term-lock-btn exists on the toolbar');
    assert.ok(presets.querySelector('.gl-btn'), 'presets are actually populated, not an empty shell');
    // Neither sits inside a hidden popover — both are reachable from the toolbar itself.
    assert.equal(presets.closest('.popover'), null, 'presets are not folded behind a popover');
    assert.equal(lock.closest('.popover'), null, 'lock is not folded behind a popover');
});

// 3 — THE PIN. Reflowing the wall must never touch a pinned pane's geometry.
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

// 4 — THE KEYBOARD SWITCHER. Alt+N jumps to the pane the badge shows; bare N does not.
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

// 5 — REAL INPUT ROUTING (CMX-112, corrected by CMX-114). A flashed border is not
// enough: Alt+N must call BOTH the iframe ELEMENT's own `.focus()` and the xterm
// Terminal's OWN `.focus()` (ttyd exposes it as `contentWindow.term`). CMX-114:
// contentWindow.focus() alone ("border moves, typing doesn't follow") does not
// reliably move real browser keyboard focus into the frame when switching FROM
// another focused pane — `ifr.focus()` is required, not skipped once term.focus()
// succeeds (the old cmx-112 fallback-only behaviour this replaces).
test('Alt+N focuses BOTH the pane\'s own iframe element and its xterm terminal', async () => {
    const wid = '@2';
    const ifr = tile(wid).querySelector('iframe.term-frame');
    assert.ok(ifr, 'the pane must have a real ttyd iframe, or this test is vacuous');

    let termFocused = false;
    ifr.contentWindow.term = { cols: 80, rows: 24, focus() { termFocused = true; } };
    let ifrFocusCalled = false;
    ifr.focus = () => { ifrFocusCalled = true; };

    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: '2', altKey: true, bubbles: true }));
    await new Promise(r => setTimeout(r, 120));   // focusPaneByWid defers by 60ms

    assert.ok(termFocused, 'Alt+2 must call the xterm Terminal\'s own .focus() so keystrokes are routed into it');
    assert.ok(ifrFocusCalled,
        'Alt+2 must ALSO call the iframe ELEMENT\'s own .focus() — contentWindow.focus() alone does not ' +
        'reliably move real browser keyboard focus into the frame when switching from another focused pane');

    delete ifr.contentWindow.term;
});

// 6 — RE-ARM AFTER FOCUS MOVES INTO A PANE (CMX-112). A same-origin iframe's keydown
// never bubbles to the parent document, so the shortcut must also be wired inside
// each pane's OWN document — otherwise the very first Alt+N leaves the switcher deaf
// to every Alt+N that follows.
test('Alt+N still switches panes once focus has moved into a pane\'s own iframe document', async () => {
    const from = tile('@1').querySelector('iframe.term-frame');
    assert.ok(from, 'the pane must have a real ttyd iframe, or this test is vacuous');

    from.dispatchEvent(new window.Event('load'));   // as the browser fires on navigation

    const flashed = wid => tile(wid).querySelector('.grid-stack-item-content').classList.contains('pane-flash');

    // Fired on the IFRAME'S OWN document, not the parent — what a keypress looks
    // like once xterm's helper textarea has focus inside that pane.
    from.contentDocument.dispatchEvent(new window.KeyboardEvent('keydown', { key: '2', altKey: true, bubbles: true }));
    await new Promise(r => setTimeout(r, 120));

    assert.ok(flashed('@2'), 'Alt+2 fired inside the focused pane\'s own iframe must still switch panes');
});

// 8 — THE IFRAME LISTENER IS CAPTURE-PHASE AND PREEMPTS XTERM (CMX-114). jsdom has no
// real xterm, so this cannot prove the runtime preemption itself (see property 8's
// disclaimer above and the PR) — it asserts the WIRING: the keydown listener is
// registered with capture=true (so it runs before a bubble-phase handler like xterm's
// own could), and a matched Alt-digit is preventDefaulted + stopImmediatePropagation'd
// so it can't reach the shell as a stray character.
test('the iframe alt-switch listener is registered in the CAPTURE phase and stops propagation on a match', () => {
    const ifr = tile('@3').querySelector('iframe.term-frame');
    assert.ok(ifr, 'the pane must have a real ttyd iframe, or this test is vacuous');
    const doc = ifr.contentDocument;

    let capturedOpts = null;
    let handler = null;
    const origAdd = doc.addEventListener.bind(doc);
    doc.addEventListener = (type, fn, opts) => {
        if (type === 'keydown' && !handler) { handler = fn; capturedOpts = opts; }
        return origAdd(type, fn, opts);
    };

    ifr.dispatchEvent(new window.Event('load'));   // re-wires _wireIframeAltSwitch with the spy in place
    doc.addEventListener = origAdd;

    assert.equal(capturedOpts, true,
        'the iframe keydown listener must be registered with capture=true — a bubble-phase listener ' +
        'never reliably preempts xterm\'s own real-keydown handling');
    assert.ok(handler, 'the listener must actually have been registered');

    let prevented = false;
    let stoppedImmediate = false;
    const evt = new window.KeyboardEvent('keydown', { key: '1', altKey: true });
    Object.defineProperty(evt, 'preventDefault', { value: () => { prevented = true; }, configurable: true });
    Object.defineProperty(evt, 'stopImmediatePropagation', { value: () => { stoppedImmediate = true; }, configurable: true });
    handler(evt);

    assert.ok(prevented, 'a matched Alt-digit must be preventDefaulted so it never reaches the shell as input');
    assert.ok(stoppedImmediate,
        'a matched Alt-digit must call stopImmediatePropagation so this listener wins the race against xterm\'s own');
});

// 9 — CTRL/⌘+K OPENS THE PALETTE FROM INSIDE A FOCUSED PANE (CMX-116). The palette,
// not Alt+N, is now the PRIMARY switcher — it must be reachable no matter where focus
// is. Same iframe-side injection property 6/8 proved necessary for Alt+N, generalized
// (not forked) to also carry this shortcut.
test('Ctrl+K fired inside a focused pane\'s own iframe document opens the command palette', () => {
    const ifr = tile('@1').querySelector('iframe.term-frame');
    assert.ok(ifr, 'the pane must have a real ttyd iframe, or this test is vacuous');
    ifr.dispatchEvent(new window.Event('load'));   // (re)wires the generalized iframe-side listener

    assert.equal(document.getElementById('palette').classList.contains('open'), false,
        'the palette must start closed, or this test is vacuous');

    ifr.contentDocument.dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));

    assert.equal(document.getElementById('palette').classList.contains('open'), true,
        'Ctrl+K fired inside the pane\'s own document must open the palette');
    window.chela.closePalette();
});

// 9b — same wiring discipline as property 8, for the SAME injector (not a duplicate
// one): capture=true at registration, preventDefault + stopImmediatePropagation on a
// matched Ctrl+K, so xterm never sees it as readline's kill-to-end-of-line.
test('the iframe listener also registers Ctrl+K in the CAPTURE phase and stops propagation on a match', () => {
    const ifr = tile('@2').querySelector('iframe.term-frame');
    assert.ok(ifr, 'the pane must have a real ttyd iframe, or this test is vacuous');
    const doc = ifr.contentDocument;

    let capturedOpts = null;
    let handler = null;
    const origAdd = doc.addEventListener.bind(doc);
    doc.addEventListener = (type, fn, opts) => {
        if (type === 'keydown' && !handler) { handler = fn; capturedOpts = opts; }
        return origAdd(type, fn, opts);
    };

    ifr.dispatchEvent(new window.Event('load'));   // re-wires with the spy in place
    doc.addEventListener = origAdd;

    assert.equal(capturedOpts, true, 'the iframe keydown listener must be registered with capture=true');
    assert.ok(handler, 'the listener must actually have been registered');

    let prevented = false;
    let stoppedImmediate = false;
    const evt = new window.KeyboardEvent('keydown', { key: 'K', ctrlKey: true });
    Object.defineProperty(evt, 'preventDefault', { value: () => { prevented = true; }, configurable: true });
    Object.defineProperty(evt, 'stopImmediatePropagation', { value: () => { stoppedImmediate = true; }, configurable: true });
    handler(evt);

    assert.ok(prevented, 'a matched Ctrl+K must be preventDefaulted so it never reaches xterm as readline kill-line');
    assert.ok(stoppedImmediate,
        'a matched Ctrl+K must call stopImmediatePropagation so this listener wins the race against xterm\'s own');
});

// 10 — THE PALETTE FLOATS OPEN PANES TO THE TOP (CMX-116). With an empty query, live
// unminimized wall panes lead the list in wall order, followed by a non-selectable
// divider, then the normal views/projects/actions — and a pane already shown up top is
// not ALSO repeated further down as a duplicate "session ·" row.
test('an empty-query palette floats live wall panes to the top, in wall order, before a divider', () => {
    window.chela.openPalette();
    const rows = Array.from(document.getElementById('palette-list').children);

    const paneRows = rows.slice(0, 3);
    paneRows.forEach(r => assert.ok(r.classList.contains('palette-item'), 'pane rows are real selectable items'));
    assert.deepEqual(paneRows.map(r => r.querySelector('.pi-title').textContent), ['alpha', 'bravo', 'charlie'],
        'the 3 live panes must lead the list in wall order (@1, @2, @3)');
    assert.deepEqual(paneRows.map(r => r.querySelector('.pi-sub').textContent),
        ['open pane · idle', 'open pane · idle', 'open pane · idle']);

    const divider = rows[3];
    assert.ok(divider.classList.contains('palette-divider'), 'a divider row must separate panes from the rest');
    assert.equal(divider.classList.contains('palette-item'), false, 'the divider must not be a selectable item');

    const afterDivider = rows[4];
    assert.equal(afterDivider.querySelector('.pi-sub').textContent, 'view',
        'the row right after the divider must be a view (the normal list), not another pane');

    const titles = rows.map(r => r.querySelector('.pi-title')).filter(Boolean).map(el => el.textContent);
    assert.equal(titles.filter(t => t === 'alpha').length, 1,
        'a pane already floated to the top must not ALSO repeat as a duplicate "session" row below the divider');

    window.chela.closePalette();
});

// 10b — ATTENTION-FIRST WITHIN THE PANES SECTION. A pane waiting on the human bubbles
// ahead of idle peers even though it trails them in wall order — the sidebar's own
// status word/rank, reused so the two never disagree about what needs you.
test('a pane that wants the human floats ahead of idle panes, still inside the panes section', () => {
    util.setAgentsCache([
        { name: 'alpha', window_id: '@1', online: true },
        { name: 'bravo', window_id: '@2', online: true, session_status: 'waiting' },
        { name: 'charlie', window_id: '@3', online: true },
    ]);
    window.chela.openPalette();
    const paneRows = Array.from(document.getElementById('palette-list').children).slice(0, 3);
    assert.deepEqual(paneRows.map(r => r.querySelector('.pi-title').textContent), ['bravo', 'alpha', 'charlie'],
        'bravo (waiting on the human) must float ahead of idle alpha/charlie despite trailing them in wall order');
    window.chela.closePalette();
    util.setAgentsCache(AGENTS);   // restore for any test that runs after this one
});

// 10c — TYPING ESCAPES THE "PANES ONLY" TRAP. A non-empty query drops the panes-first
// section entirely and falls back to the normal fuzzy match over everything a query
// would have matched before this feature existed — a view must still be reachable by
// name once the user types.
test('typing a query drops the panes-first section and fuzzy-matches everything, views included', () => {
    window.chela.openPalette();
    window.chela._renderPalette('wall');   // "Wall" is a real registered view label
    const rows = Array.from(document.getElementById('palette-list').children);
    assert.ok(rows.every(r => !r.classList.contains('palette-divider')),
        'a non-empty query must not render the panes-first divider');
    assert.ok(rows.some(r => r.querySelector('.pi-sub') && r.querySelector('.pi-sub').textContent === 'view'),
        'a view must still be reachable by fuzzy search once the user types — a query must never trap you in panes-only');
    window.chela.closePalette();
});

// 11 — THE "⋯" DROPDOWN'S ON-FILL OUTRANKS THE POPOVER-ITEM BASE ROW (CMX-116 fixes a
// CMX-114 regression). `.gs-keys button.popover-item { background: none }` (specificity
// 0,2,1) overrode a plain `.gs-share-btn.on` / `.gs-orch-btn.on` / `.gs-pin-btn.on`
// (0,2,0), so the active fill never painted. jsdom cannot resolve CSS cascade
// specificity (see the honest disclaimer above and in the PR) — this is a STATIC
// source-text fact: the higher-specificity form must exist, and the old
// lower-specificity form (which the popover-item rule can still outrank) must be gone.
test('the pane-menu ON-state fill rules repeat the `.gs-keys button` prefix so they outrank the popover-item base rule', () => {
    ['gs-share-btn', 'gs-orch-btn', 'gs-pin-btn'].forEach(cls => {
        const strong = new RegExp(String.raw`^\.gs-keys\s+button\.${cls}\.on\s*\{[^}]*background:`, 'm');
        assert.ok(strong.test(CSS),
            `.gs-keys button.${cls}.on { background: ... } must exist — higher specificity (0,3,1) than ` +
            '.gs-keys button.popover-item (0,2,1)');

        const bare = new RegExp(String.raw`^\.${cls}\.on\s*\{[^}]*background:`, 'm');
        assert.ok(!bare.test(CSS),
            `a bare .${cls}.on { background: ... } rule must not exist — it is the exact form (0,2,0) ` +
            'the popover-item base rule already outranks, which is how this regressed');
    });
});
