// SHARE LINK/CODE RETRIEVAL — real-DOM regression guards for CMX-144 / CMX-152.
//
// Two properties, both invisible from source-reading alone:
//
//   1. 🔴 CLICKING SHARE OPENS THE "ACTIVE SHARES" SHEET, NOT A SEPARATE POPOVER.
//      CMX-152 deleted the fragile per-pane popover (`_sharePopover`) — the sheet is
//      now the ONE place a share's join link + pairing code are shown. A regression
//      that resurrects a `.term-share-pop` element (or fails to open the sheet)
//      would silently re-introduce the redundant second surface this consolidated.
//
//   2. 🔴 THE "ACTIVE SHARES" SHEET MUST SHOW THE LINK + CODE, NOT ONLY STOP.
//      /api/term/shared deliberately omits join_url/pairing_code (they're
//      owner-only secrets — see api_term_shared in app.py); the sheet has to make
//      its own per-wid /share-info round trip to have anything to show.
//
// Run: node --test tests/share_sheet.test.mjs (pytest runs it via
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
    else if (/\/api\/term\/(.+)\/share$/.test(path) && method === 'POST') {
        const wid = decodeURIComponent(RegExp.$1);
        // Mirror the real server: minting a share makes it show up in BOTH
        // /api/term/shared (what openSharesSheet reconciles against) and
        // /share-info (what the sheet fetches per-row) from then on.
        SHARED = { ...SHARED, [wid]: { cols: 80, rows: 24 } };
        SHARE_INFO = { join_url: 'https://relay.test/j/abc', pairing_code: 'PAIRXYZ' };
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

test('clicking Share mints a share and opens the Active shares sheet, not a popover', async () => {
    SHARE_INFO = {};   // not shared yet -> shareBtnClick takes the mint-a-share path
    SHARED = {};
    const { shareBtn } = openPaneMenu('@1');
    await flush();   // togglePaneOverflow arms its outside-click listener via setTimeout

    await terminals.shareBtnClick(shareBtn, '@1');
    await flush();   // let the /share POST + openSharesSheet's own reconcile fetch resolve

    assert.equal(document.querySelector('.term-share-pop'), null,
        'the per-pane popover must be gone — Share now opens the sheet directly');
    const row = document.querySelector('.shares-sheet .ss-row[data-wid="@1"]');
    assert.ok(row, 'the Active shares sheet must open and show a row for the just-shared pane');
    const values = Array.from(row.querySelectorAll('.tsp-in')).map(i => i.value);
    assert.ok(values.includes('https://relay.test/j/abc'), 'row must show the freshly-minted join link');
    assert.ok(values.includes('PAIRXYZ'), 'row must show the freshly-minted pairing code');
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
