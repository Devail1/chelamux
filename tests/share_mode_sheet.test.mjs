// CHANGE A LIVE SHARE'S MODE (CMX-421) — the Active-shares sheet's per-share mode control,
// in a REAL DOM running the real terminals.js (same harness as tests/share_dialog.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - each row shows its share's mode as a control (the live mode pressed), and says what
//     THAT share allows — a view-only row never claims guests can type;
//   - down (→ View only) POSTs /share-mode at once, no confirmation;
//   - up to Allow typing is disabled, with the dialog's reason, where the dialog would
//     disable it; enabled and POSTs mode:"typing" where it is allowed;
//   - up to UNSANDBOXED POSTs nothing until the window name is typed exactly;
//   - "Share current session" on an already-shared pane mints nothing and opens the sheet
//     pointed at that share (adopt-first, CMX-421 item 8);
//   - the pane's share pill shows the mode glyph (👁 / ⌨ / ⚠).
//
// Run: node --test tests/share_mode_sheet.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const PANEL = `
<button class="shares-indicator" id="btn-shares" hidden><span class="si-dot"></span><span class="si-text">0 sharing</span></button>
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
const INFO = { join_url: 'https://relay.test/j/abc', pairing_code: 'PAIR1' };

let MODE = 'view';     // the server's live mode for @1
let OPTIONS = {};      // GET /share-options
let modePosts = [];    // POST /share-mode bodies
let mintPosts = [];    // POST /share bodies

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (path.startsWith('/api/term/ready')) body = { ready: true };
    else if (path.endsWith('/share-options')) body = OPTIONS;
    else if (path.endsWith('/share-info')) body = INFO;
    else if (path.endsWith('/api/term/shared')) body = { '@1': { cols: 80, rows: 24, mode: MODE, expires_at: null } };
    else if (path.endsWith('/share-mode') && method === 'POST') {
        const b = JSON.parse(opts.body);
        modePosts.push(b);
        const from = MODE;
        MODE = b.mode;
        body = { ok: true, shared: true, ...INFO, from, to: b.mode, mode: b.mode, expires_at: b.mode === 'unsandboxed' ? 1e9 : null };
    } else if (/\/share$/.test(path) && method === 'POST') {
        mintPosts.push(JSON.parse(opts.body));
        body = { ok: true, shared: true, ...INFO, mode: 'view' };
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
const flush = () => new Promise(r => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 4; i++) await flush(); };

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
    dom.window.matchMedia = q => ({ media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

beforeEach(() => {
    terminals.closeSharesSheet();
    terminals.closeShareDialog();
    modePosts = []; mintPosts = [];
    terminals._sharedWids.clear();
    terminals._sharedWids.add('@1');
});

async function openSheet(mode, options) {
    MODE = mode;
    OPTIONS = options;
    await window.chela.openSharesSheet();
    await settle();
    const row = document.querySelector('.shares-sheet .ss-row[data-wid="@1"]');
    assert.ok(row, 'the sheet must render a row for the share');
    return row;
}
const opt = (row, m) => row.querySelector(`.ss-mode-opt[data-mode="${m}"]`);
const pressed = row => [...row.querySelectorAll('.ss-mode-opt[aria-pressed="true"]')].map(b => b.dataset.mode);
const freshRow = () => document.querySelector('.shares-sheet .ss-row[data-wid="@1"]');

test('each row shows its mode as a control, and a view-only row never says guests can type', async () => {
    const row = await openSheet('view', { share_typing: false, sandboxed: false, typing_allowed: false, window_name: 'shell-1' });
    assert.deepEqual(pressed(row), ['view']);
    assert.match(opt(row, 'view').textContent, /👁 View only/);
    assert.match(opt(row, 'typing').textContent, /⌨ Allow typing/);
    const sheetText = document.querySelector('.shares-sheet').textContent;
    assert.doesNotMatch(sheetText, /watch and type/, 'a view-only share must not claim guests can type');
    assert.match(row.querySelector('.ss-mode-desc').textContent, /Nothing they type reaches this machine/);
});

test('a typing share says guests can type, and the pane pill shows ⌨', async () => {
    const row = await openSheet('typing', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    assert.deepEqual(pressed(row), ['typing']);
    assert.match(row.querySelector('.ss-mode-desc').textContent, /watch and type/);
    assert.equal(document.querySelector('.gs-share-btn[data-wid="@1"] .gs-share-mode').textContent, '⌨');
});

test('setting off ⇒ Allow typing disabled with the dialog\'s reason, and nothing is POSTed', async () => {
    const row = await openSheet('view', { share_typing: false, sandboxed: true, typing_allowed: false, window_name: 'shell-1' });
    assert.equal(opt(row, 'typing').disabled, true);
    assert.equal(row.querySelector('.ss-mode-reason').textContent, 'Typing is disabled in Settings');
    opt(row, 'typing').click();
    await settle();
    assert.deepEqual(modePosts, []);
});

test('not sandboxed ⇒ Allow typing disabled with the New session → Sandboxed hint', async () => {
    const row = await openSheet('view', { share_typing: true, sandboxed: false, typing_allowed: false,
                                          unsandboxed_offered: true, window_name: 'shell-1' });
    assert.equal(opt(row, 'typing').disabled, true);
    assert.equal(row.querySelector('.ss-mode-reason').textContent,
        'Not a sandboxed session — start one from New session → Sandboxed');
});

test('⭐ view → typing where allowed POSTs /share-mode and the pills follow', async () => {
    const row = await openSheet('view', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    assert.equal(opt(row, 'typing').disabled, false);
    assert.equal(document.querySelector('.gs-share-btn[data-wid="@1"] .gs-share-mode').textContent, '👁');
    opt(row, 'typing').click();
    await settle();
    assert.deepEqual(modePosts, [{ mode: 'typing' }]);
    assert.deepEqual(mintPosts, [], 'a mode change never re-mints the share');
    assert.deepEqual(pressed(freshRow()), ['typing']);
    assert.match(document.querySelector('#btn-shares .si-text').textContent, /⌨/);
    assert.equal(document.querySelector('.gs-share-btn[data-wid="@1"] .gs-share-mode').textContent, '⌨');
    assert.ok([...freshRow().querySelectorAll('.tsp-in')].some(i => i.value === 'PAIR1'), 'same code shown');
});

test('down to View only applies at once, with no confirmation', async () => {
    const row = await openSheet('typing', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    opt(row, 'view').click();
    await settle();
    assert.deepEqual(modePosts, [{ mode: 'view' }]);
    assert.deepEqual(pressed(freshRow()), ['view']);
});

test('up to UNSANDBOXED POSTs nothing until the window name is typed', async () => {
    const row = await openSheet('view', { share_typing: true, sandboxed: false, typing_allowed: false,
                                          unsandboxed_offered: true, unsandboxed_minutes: 30, window_name: 'shell-1' });
    const u = opt(row, 'unsandboxed');
    assert.ok(u, 'the override is offered: setting on, window not sandboxed');
    u.click();
    await settle();
    assert.deepEqual(modePosts, [], 'choosing UNSANDBOXED alone must not POST');
    const box = row.querySelector('.ss-unsafe-confirm');
    assert.equal(box.hidden, false, 'the typed-name confirmation must appear');
    const input = row.querySelector('.ss-confirm-in');
    const go = row.querySelector('.ss-confirm-go');
    assert.equal(go.disabled, true);
    input.value = 'shell-2'; input.oninput();
    assert.equal(go.disabled, true, 'a wrong name must not arm it');
    go.click();
    await settle();
    assert.deepEqual(modePosts, []);
    input.value = 'shell-1'; input.oninput();
    assert.equal(go.disabled, false);
    go.click();
    await settle();
    assert.deepEqual(modePosts, [{ mode: 'unsandboxed', confirm: 'shell-1' }]);
    assert.equal(document.querySelector('.gs-share-btn[data-wid="@1"] .gs-share-mode').textContent, '⚠');
});

test('the override is not offered with the setting off', async () => {
    const row = await openSheet('view', { share_typing: false, sandboxed: false, typing_allowed: false, window_name: 'shell-1' });
    assert.equal(opt(row, 'unsandboxed'), null);
});

test('Share on an already-shared pane mints nothing and points the sheet at that share', async () => {
    MODE = 'view';
    OPTIONS = { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' };
    await terminals.shareBtnClick(null, '@1');
    await settle();
    assert.equal(document.getElementById('share-dialog-backdrop'), null, 'adopt-first: no new-share dialog');
    assert.deepEqual(mintPosts, [], 'adopt-first: never re-mint');
    const row = freshRow();
    assert.ok(row.classList.contains('ss-row-focus'), 'the existing share must be the highlighted row');
    assert.match(row.querySelector('.ss-already').textContent, /already shared/);
    assert.ok(opt(row, 'typing'), 'the control to change its mode is right there');
});
