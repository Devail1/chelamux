// SHARE LINK/CODE RETRIEVAL — real-DOM regression guards for CMX-144.
//
// Two properties, both invisible from source-reading alone:
//
//   1. 🔴 THE POPOVER MUST ANCHOR TO THE RECT CAPTURED BEFORE THE MENU CLOSES.
//      The share button lives inside the pane's "⋯" overflow menu. Clicking it
//      fires shareBtnClick — an async function that awaits a network round trip —
//      and the SAME click event keeps bubbling past the button up to a
//      document-level {once:true} listener that togglePaneOverflow armed on open
//      (_closeAllPaneOverflows), which hides the menu SYNCHRONOUSLY, in the same
//      event-dispatch call, before shareBtnClick's await resolves. Reading the
//      button's rect only after that await (the old code, inside _sharePopover)
//      reads it hidden — in a real browser, a zeroed rect — so the popover was
//      pinned to the top-left corner instead of the button. jsdom computes no
//      layout at all, so it can't reproduce "hidden -> zero rect" by itself; this
//      test ties a stubbed getBoundingClientRect() to the menu's actual `.hidden`
//      state (exactly what the [hidden] CSS rule does in a real browser) so the
//      same synchronous race the browser hits also drives the stub here.
//
//   2. 🔴 THE "ACTIVE SHARES" SHEET MUST SHOW THE LINK + CODE, NOT ONLY STOP.
//      /api/term/shared deliberately omits join_url/pairing_code (they're
//      owner-only secrets — see api_term_shared in app.py); the sheet has to make
//      its own per-wid /share-info round trip to have anything to show.
//
// Run: node --test tests/share_popover.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom).
import { before, test } from 'node:test';
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
</div>`;

const AGENTS = [{ name: 'shell', window_id: '@1', online: true }];

// Mutable per-test fixtures for the share endpoints.
let SHARE_INFO = {};   // GET /api/term/<wid>/share-info
let SHARED = {};       // GET /api/term/shared

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/agents/context')) body = {};
    else if (path.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (path.startsWith('/api/term/ready')) body = { ready: true };
    else if (path.endsWith('/share-info')) body = SHARE_INFO;
    else if (path.endsWith('/api/term/shared')) body = SHARED;
    else if (/\/api\/term\/.+\/share$/.test(path) && method === 'POST') {
        body = { ok: true, shared: true, join_url: 'https://relay.test/j/abc', pairing_code: 'PAIRXYZ' };
    }
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

let terminals, util;

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
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

const flush = () => new Promise(r => setTimeout(r, 0));

function openPaneMenu(wid) {
    const shareBtn = document.querySelector(`.gs-share-btn[data-wid="${wid}"]`);
    assert.ok(shareBtn, 'pane must render a share button');
    const menu = shareBtn.closest('.pane-overflow-menu');
    const trigger = menu.previousElementSibling;
    window.chela.togglePaneOverflow({ stopPropagation() {} }, trigger);
    return { shareBtn, menu };
}

test('share popover anchors to the rect captured BEFORE the overflow menu closes', async () => {
    SHARE_INFO = {};   // not shared yet -> shareBtnClick takes the mint-a-share path
    SHARED = {};
    const { shareBtn, menu } = openPaneMenu('@1');
    // togglePaneOverflow arms the outside-click listener via setTimeout(fn, 0) —
    // flush it before dispatching the click that should trigger it.
    await flush();
    assert.equal(menu.hidden, false, 'menu must be open before the click for this race to be real');

    const GOOD = { top: 100, bottom: 130, left: 300, right: 400, width: 100, height: 30 };
    const ZERO = { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };
    // Faithful stand-in for real layout: a hidden button ([hidden] -> display:none)
    // measures as a zero rect. Without tying this to the real `menu.hidden` state,
    // every rect read (early or late) would be zero and the test would pass
    // regardless of when shareBtnClick actually reads it.
    shareBtn.getBoundingClientRect = () => (menu.hidden ? ZERO : GOOD);

    // Wire the click the way inline onclick="chela.shareBtnClick(this,'@1')" would
    // (jsdom doesn't execute inline handler attributes), then dispatch a REAL
    // bubbling click — this is what makes the outside-click listener actually fire
    // mid-flight, exactly as it does in a live browser.
    shareBtn.addEventListener('click', () => { terminals.shareBtnClick(shareBtn, '@1'); });
    shareBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));

    assert.equal(menu.hidden, true,
        'the outside-click listener must have fired SYNCHRONOUSLY during dispatch — otherwise this test proves nothing');

    await flush();   // let shareBtnClick's awaited /share POST resolve

    const pop = document.querySelector('.term-share-pop');
    assert.ok(pop, 'popover must render');
    assert.equal(pop.style.top, (GOOD.bottom + 6) + 'px',
        'popover must anchor to the rect captured before the menu hid the button, not the zeroed one read after');
    assert.equal(pop.style.right, Math.max(8, window.innerWidth - GOOD.right) + 'px');
});

test('the Active shares sheet shows the join link + pairing code, not just Stop', async () => {
    terminals._sharedWids.clear();
    terminals._sharedWids.add('@1');
    SHARED = { '@1': { cols: 80, rows: 24 } };
    SHARE_INFO = { join_url: 'https://relay.test/j/xyz', pairing_code: 'PAIR456' };

    await window.chela.openSharesSheet();
    await flush();   // _buildSharesSheet's per-wid /share-info fetch is async too

    const row = document.querySelector('.shares-sheet .ss-row[data-wid="@1"]');
    assert.ok(row, 'sheet must render a row for the shared pane');
    const values = Array.from(row.querySelectorAll('.tsp-in')).map(i => i.value);
    assert.ok(values.includes('https://relay.test/j/xyz'), 'row must show the join link');
    assert.ok(values.includes('PAIR456'), 'row must show the pairing code');
    assert.ok(row.querySelector('.ss-stop'), 'Stop must still be present');
});
