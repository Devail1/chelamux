// SHARE DIALOG (CMX-403) — the access choice made BEFORE a share is minted, in a REAL
// DOM running the real terminals.js (same harness as tests/share_sheet.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - "View only" is the default choice, and minting with it POSTs mode:"view";
//   - "Allow typing" is DISABLED with "Typing is disabled in Settings" when the setting
//     is off, and with "Not a sandboxed session — start one from New session →
//     Sandboxed" when the window isn't one; ENABLED with no reason when both hold
//     (the accepted case, and the negative control for the two disabled ones);
//   - the UNSANDBOXED override is offered only with the setting on AND a non-sandboxed
//     window, and Share stays disabled until the window name is TYPED exactly;
//   - CMX-419: the override's duration picker preselects the configured default and its
//     choice is POSTed as `minutes`; a pick above 4 h keeps Share disabled until the
//     window name is typed a SECOND time; the time left shows on the red banner and in
//     Active shares;
//   - the share pill reads 👁 for view only, and the red "UNSANDBOXED — guest can type"
//     banner shows on the pill AND the pane while an override is live — and not otherwise.
//
// Run: node --test tests/share_dialog.test.mjs (pytest runs it via
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

let OPTIONS = {};      // GET /api/term/<wid>/share-options
let MINT_MODE = 'view';
let MINT_EXPIRES = null;   // the override's expires_at (epoch s) the mint returns
let posts = [];

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (path.startsWith('/api/term/ready')) body = { ready: true };
    else if (path.endsWith('/share-options')) body = OPTIONS;
    else if (path.endsWith('/share-info')) body = {};
    else if (path.endsWith('/api/term/shared')) body = posts.length ? { '@1': { cols: 80, rows: 24, mode: MINT_MODE, expires_at: MINT_EXPIRES } } : {};
    else if (/\/share$/.test(path) && method === 'POST') {
        posts.push(JSON.parse(opts.body));
        body = { ok: true, shared: true, join_url: 'https://relay.test/j/abc', pairing_code: 'P', mode: MINT_MODE, expires_at: MINT_EXPIRES };
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

beforeEach(async () => {
    MINT_MODE = 'view';
    MINT_EXPIRES = null;
    terminals.closeShareDialog();
    window.chela.closeSharesSheet && window.chela.closeSharesSheet();
    if (terminals._sharedWids.has('@1')) await terminals._stopShare('@1');
    posts = [];
});

const dialog = () => document.querySelector('#share-dialog-backdrop .share-dialog');
const radio = v => dialog().querySelector(`input[name="sd-mode"][value="${v}"]`);
const reasonText = () => (dialog().querySelector('.sd-reason') || { textContent: '' }).textContent;

async function open(options) {
    OPTIONS = options;
    await terminals.shareBtnClick(null, '@1');
    assert.ok(dialog(), 'Share on an unshared pane must open the access dialog');
}

test('"View only" is the default and mints mode:"view"', async () => {
    await open({ share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    assert.equal(radio('view').checked, true, 'View only must be the default choice');
    assert.equal(radio('typing').checked, false);
    dialog().querySelector('.sd-share').click();
    await flush(); await flush();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].mode, 'view');
});

test('setting off ⇒ Allow typing disabled with "Typing is disabled in Settings"', async () => {
    await open({ share_typing: false, sandboxed: true, typing_allowed: false, window_name: 'shell-1' });
    assert.equal(radio('typing').disabled, true);
    assert.equal(reasonText(), 'Typing is disabled in Settings');
    assert.equal(radio('unsandboxed'), null, 'the override must not be offered with the setting off');
});

test('not a sandboxed session ⇒ Allow typing disabled with the New session → Sandboxed hint', async () => {
    await open({ share_typing: true, sandboxed: false, typing_allowed: false,
                 unsandboxed_offered: true, window_name: 'shell-1' });
    assert.equal(radio('typing').disabled, true);
    assert.equal(reasonText(), 'Not a sandboxed session — start one from New session → Sandboxed');
});

test('⭐ setting on + verified sandbox ⇒ Allow typing enabled, no reason, mints mode:"typing"', async () => {
    await open({ share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    assert.equal(radio('typing').disabled, false);
    assert.equal(reasonText(), '');
    assert.equal(radio('unsandboxed'), null, 'no override on a window that is already sandboxed');
    radio('typing').checked = true;
    radio('typing').onchange();
    MINT_MODE = 'typing';
    dialog().querySelector('.sd-share').click();
    await flush(); await flush();
    assert.equal(posts[0].mode, 'typing');
    assert.match(document.querySelector('#btn-shares .si-text').textContent, /⌨/);
});

test('UNSANDBOXED override: Share stays disabled until the window name is typed', async () => {
    await open({ share_typing: true, sandboxed: false, typing_allowed: false,
                 unsandboxed_offered: true, unsandboxed_minutes: 30, window_name: 'shell-1' });
    const r = radio('unsandboxed');
    assert.ok(r, 'the override must be offered: setting on, window not sandboxed');
    assert.match(r.closest('label').textContent,
        /Full access — UNSANDBOXED: the guest can type into a real shell on this machine/);
    r.checked = true; r.onchange();
    const share = dialog().querySelector('.sd-share');
    const confirmIn = dialog().querySelector('#sd-confirm-in');
    assert.equal(share.disabled, true, 'no typed confirmation ⇒ Share must be disabled');
    confirmIn.value = 'shell-2'; confirmIn.oninput();
    assert.equal(share.disabled, true, 'a WRONG name must not arm it');
    confirmIn.value = 'shell-1'; confirmIn.oninput();
    assert.equal(share.disabled, false, 'the exact window name arms it');
    MINT_MODE = 'unsandboxed';
    share.click();
    await flush(); await flush();
    assert.deepEqual([posts[0].mode, posts[0].confirm], ['unsandboxed', 'shell-1']);
});

test('the red UNSANDBOXED banner shows on the pill and the pane while an override is live', async () => {
    await open({ share_typing: true, sandboxed: false, unsandboxed_offered: true, window_name: 'shell-1' });
    radio('unsandboxed').checked = true; radio('unsandboxed').onchange();
    const c = dialog().querySelector('#sd-confirm-in'); c.value = 'shell-1'; c.oninput();
    MINT_MODE = 'unsandboxed';
    dialog().querySelector('.sd-share').click();
    await flush(); await flush();
    const pill = document.getElementById('btn-shares');
    assert.equal(pill.classList.contains('si-unsandboxed'), true);
    assert.match(pill.textContent, /UNSANDBOXED — guest can type/);
    const banner = document.querySelector('.gs-unsafe-banner[data-banner-for="@1"]');
    assert.ok(banner, 'the pane header must carry the banner slot');
    assert.equal(banner.hidden, false, 'the pane banner must show while the override is live');
    // The kill switch path clears it.
    await terminals._stopShare('@1');
    assert.equal(banner.hidden, true);
});

test('negative control: a view-only share shows 👁 and no UNSANDBOXED banner', async () => {
    await open({ share_typing: false, window_name: 'shell-1' });
    dialog().querySelector('.sd-share').click();
    await flush(); await flush();
    const pill = document.getElementById('btn-shares');
    assert.match(pill.querySelector('.si-text').textContent, /👁/);
    assert.equal(pill.classList.contains('si-unsandboxed'), false);
    assert.equal(document.querySelector('.gs-unsafe-banner[data-banner-for="@1"]').hidden, true);
});


// --- CMX-419: the override's duration picker ------------------------------------------
const UNSAFE_OPTS = { share_typing: true, sandboxed: false, typing_allowed: false, unsandboxed_offered: true,
    unsandboxed_minutes: 30, unsandboxed_choices: [30, 240, 1440, 10080, 20160],
    unsandboxed_long_minutes: 240, window_name: 'shell-1' };

function pickUnsafe() {
    radio('unsandboxed').checked = true; radio('unsandboxed').onchange();
}
function typeIn(sel, v) { const el = dialog().querySelector(sel); el.value = v; el.oninput(); }
function pickDur(n) { const s = dialog().querySelector('#sd-dur'); s.value = String(n); s.onchange(); }

test('⭐ the picker preselects the configured default and the default mints as before', async () => {
    await open(UNSAFE_OPTS);
    pickUnsafe();
    const sel = dialog().querySelector('#sd-dur');
    assert.deepEqual([...sel.options].map(o => o.textContent), ['30 min', '4 h', '1 day', '7 days', '14 days']);
    assert.equal(sel.value, '30');
    assert.equal(dialog().querySelector('.sd-confirm-long').hidden, true, 'no second confirmation at 30 min');
    typeIn('#sd-confirm-in', 'shell-1');
    assert.equal(dialog().querySelector('.sd-share').disabled, false);
    MINT_MODE = 'unsandboxed';
    dialog().querySelector('.sd-share').click();
    await flush(); await flush();
    assert.equal(posts[0].minutes, 30);
    assert.equal('confirm_long' in posts[0], false);
});

test('the picker preselects a non-30 configured default', async () => {
    await open({ ...UNSAFE_OPTS, unsandboxed_minutes: 240 });
    assert.equal(dialog().querySelector('#sd-dur').value, '240');
});

test('a 7-day pick needs the window name typed twice, and POSTs minutes:10080', async () => {
    await open(UNSAFE_OPTS);
    pickUnsafe();
    pickDur(10080);
    const share = dialog().querySelector('.sd-share');
    assert.equal(dialog().querySelector('.sd-confirm-long').hidden, false, '>4 h shows the second confirmation');
    typeIn('#sd-confirm-in', 'shell-1');
    assert.equal(share.disabled, true, 'one confirmation must not arm a >4 h grant');
    typeIn('#sd-confirm-long-in', 'shell-2');
    assert.equal(share.disabled, true, 'a WRONG second name must not arm it');
    typeIn('#sd-confirm-long-in', 'shell-1');
    assert.equal(share.disabled, false);
    MINT_MODE = 'unsandboxed';
    share.click();
    await flush(); await flush();
    assert.deepEqual([posts[0].minutes, posts[0].confirm, posts[0].confirm_long], [10080, 'shell-1', 'shell-1']);
});

test('time left reads "12d 4h left" on the red banner and in Active shares', async () => {
    await open(UNSAFE_OPTS);
    pickUnsafe(); pickDur(20160);
    typeIn('#sd-confirm-in', 'shell-1'); typeIn('#sd-confirm-long-in', 'shell-1');
    MINT_MODE = 'unsandboxed';
    MINT_EXPIRES = Date.now() / 1000 + (12 * 1440 + 4 * 60) * 60 + 30;
    dialog().querySelector('.sd-share').click();
    await flush(); await flush(); await flush();
    const banner = document.querySelector('.gs-unsafe-banner[data-banner-for="@1"]');
    assert.equal(banner.hidden, false);
    assert.match(banner.textContent, /UNSANDBOXED — guest can type · 12d 4h left/);
    const row = document.querySelector('#shares-sheet-backdrop .ss-row[data-wid="@1"]');
    assert.ok(row, 'Active shares must list the share');
    assert.match(row.textContent, /12d 4h left/);
    await terminals._stopShare('@1');
});

test('time-left formatting', () => {
    const now = 1_700_000_000_000;
    terminals._shareExpires.set('@x', now / 1000 + 3 * 3600 + 5 * 60 + 10);
    assert.equal(terminals._shareTimeLeft('@x', now), '3h 5m left');
    terminals._shareExpires.set('@x', now / 1000 + 25 * 60 + 10);
    assert.equal(terminals._shareTimeLeft('@x', now), '25m left');
    terminals._shareExpires.set('@x', now / 1000 + 20);
    assert.equal(terminals._shareTimeLeft('@x', now), '<1m left');
    terminals._shareExpires.delete('@x');
    assert.equal(terminals._shareTimeLeft('@x', now), '');
});
