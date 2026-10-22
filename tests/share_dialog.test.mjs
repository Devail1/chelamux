// SHARE DIALOG (CMX-403), page level: a pane's Share button opens the access dialog, and
// what it mints lands on the page, in a REAL DOM running the real terminals.js with the share
// islands mounted. Since CMX-239/240 the dialog, the pill and the Active shares sheet are React
// (frontend/src/features/share): their own rules are tested there (ShareDialog, SharesPill,
// SharesSheet .test.tsx). This suite keeps what crosses between them and the pane header,
// itself React since CMX-241 (frontend/src/features/panehead; its Share row's mode glyph and
// first-render rules are tested in PaneHead.test.tsx).
//
// Guards here:
//   - Share on an unshared pane opens the dialog; View only mints mode:"view" with the
//     pane's live size;
//   - "Allow typing" mints mode:"typing" and the pill reads typing;
//   - the override, armed by the typed window name, POSTs mode/confirm/minutes;
//   - the share pill reads view only, and the red "UNSANDBOXED: guest can type" banner
//     shows on the pill AND the live pane header while an override is live, and not
//     otherwise, following the share state;
//   - CMX-419: the time left shows on the red banner and in Active shares.
//
// Run: pnpm exec vitest run tests/share_dialog.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { closePalette, exposeDomConstructors, searchPalette } from './js_helpers/dashboard_dom.mjs';

const PANEL = `
<span id="shares-pill-float"></span><div id="shares-sheet-island"></div>
<div id="overlays-island"></div>
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button>
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

let terminals, agentsStore, viewStore, share;
const flush = () => new Promise(r => setTimeout(r, 0));

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    exposeDomConstructors(dom.window);   // CMX-239: the share dialog is React on Radix
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({ media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    (await import('../chela/dashboard/frontend/src/boot.ts')).start();
    ({ agents: agentsStore } = await import('../chela/dashboard/frontend/src/shared/store/agents.ts'));
    ({ view: viewStore } = await import('../chela/dashboard/frontend/src/shared/store/view.ts'));
    terminals = await import('../chela/dashboard/frontend/src/features/wall/index.ts');
    share = await import('../chela/dashboard/frontend/src/features/share/index.ts');
    (await import('../chela/dashboard/frontend/src/features/shell/index.ts')).mountSlots(document);   // the shares pills and sheet
    viewStore.set('terminals');
    agentsStore.set(AGENTS);
    await terminals.renderTerminals();
});

beforeEach(async () => {
    MINT_MODE = 'view';
    MINT_EXPIRES = null;
    share.closeShareDialog();
    share.closeSharesSheet();
    if (share.isShared('@1')) await share.stopShare('@1');
    await flush();
    posts = [];
});

const dialog = () => document.querySelector('.share-dialog');
const radio = v => dialog().querySelector(`[role="radio"][value="${v}"]`);
const shareBtn = () => dialog().querySelector('.sd-share');

// React reads a typed value through the input's native setter, then commits after the event.
async function typeIn(labelStart, v) {
    const label = [...dialog().querySelectorAll('label.field-label')].find(l => l.textContent.startsWith(labelStart));
    const el = document.getElementById(label.htmlFor);
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(el, v);
    el.dispatchEvent(new window.Event('input', { bubbles: true }));
    await flush();
}
async function pick(v) { radio(v).click(); await flush(); }
async function mint(mode) {
    MINT_MODE = mode;
    shareBtn().click();
    await flush(); await flush(); await flush();
}

async function open(options) {
    OPTIONS = options;
    await share.shareSession('@1');   // what the pane header's Share row runs
    assert.ok(dialog(), 'Share on an unshared pane must open the access dialog');
}

test('"View only" is the default and mints mode:"view", then opens Active shares', async () => {
    await open({ share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    assert.equal(radio('view').getAttribute('aria-checked'), 'true', 'View only must be the default choice');
    assert.equal(radio('typing').getAttribute('aria-checked'), 'false');
    await mint('view');
    assert.equal(posts.length, 1);
    assert.equal(posts[0].mode, 'view');
    assert.equal(dialog(), null, 'the dialog closes once the share is minted');
    await flush();
    assert.equal(document.querySelector('.term-share-pop'), null, 'no per-pane popover: the sheet is the one place (CMX-152)');
    assert.ok(document.querySelector('.shares-sheet .ss-row[data-wid="@1"]'), 'Active shares lists the new share');
});

test('setting off ⇒ Allow typing disabled with "Typing is disabled in Settings"', async () => {
    await open({ share_typing: false, sandboxed: true, typing_allowed: false, window_name: 'shell-1' });
    assert.equal(radio('typing').disabled, true);
    assert.equal(dialog().querySelector('.sd-reason').textContent, 'Typing is disabled in Settings');
    assert.equal(radio('unsandboxed'), null, 'the override must not be offered with the setting off');
});

test('⭐ setting on + verified sandbox ⇒ Allow typing mints mode:"typing" and the pills say typing', async () => {
    await open({ share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    assert.equal(radio('typing').disabled, false);
    await pick('typing');
    await mint('typing');
    assert.equal(posts[0].mode, 'typing');
    assert.equal(document.getElementById('btn-shares').dataset.mode, 'typing');
    assert.equal(share.shareMode('@1'), 'typing', 'the state the pane header\'s Share row draws');
});

test('UNSANDBOXED override: armed only by the typed window name; POSTs mode, confirm, minutes', async () => {
    await open({ share_typing: true, sandboxed: false, typing_allowed: false,
                 unsandboxed_offered: true, unsandboxed_minutes: 30, window_name: 'shell-1' });
    assert.ok(radio('unsandboxed'), 'the override must be offered: setting on, window not sandboxed');
    await pick('unsandboxed');
    assert.equal(shareBtn().disabled, true, 'no typed confirmation ⇒ Share must be disabled');
    await typeIn('Type shell-1', 'shell-2');
    assert.equal(shareBtn().disabled, true, 'a WRONG name must not arm it');
    await typeIn('Type shell-1', 'shell-1');
    assert.equal(shareBtn().disabled, false, 'the exact window name arms it');
    await mint('unsandboxed');
    assert.deepEqual([posts[0].mode, posts[0].confirm, posts[0].minutes], ['unsandboxed', 'shell-1', 30]);
});

test('the red UNSANDBOXED banner shows on the pill and the pane while an override is live', async () => {
    await open({ share_typing: true, sandboxed: false, unsandboxed_offered: true, window_name: 'shell-1' });
    await pick('unsandboxed');
    await typeIn('Type shell-1', 'shell-1');
    await mint('unsandboxed');
    const pill = document.getElementById('btn-shares');
    assert.equal(pill.classList.contains('si-unsandboxed'), true);
    assert.match(pill.textContent, /UNSANDBOXED: guest can type/);
    const banner = document.querySelector('.gs-unsafe-banner[data-banner-for="@1"]');
    assert.ok(banner, 'the pane header must carry the banner slot');
    assert.equal(banner.hidden, false, 'the pane banner must show while the override is live');
    // The kill switch path clears it.
    await share.stopShare('@1');
    assert.equal(banner.hidden, true);
});

test('negative control: a view-only share says view only and shows no UNSANDBOXED banner', async () => {
    await open({ share_typing: false, window_name: 'shell-1' });
    await mint('view');
    const pill = document.getElementById('btn-shares');
    assert.equal(pill.dataset.mode, 'view');
    assert.equal(share.shareMode('@1'), 'view');
    assert.equal(pill.classList.contains('si-unsandboxed'), false);
    assert.equal(document.querySelector('.gs-unsafe-banner[data-banner-for="@1"]').hidden, true);
});

test('a refused mint leaves the page unshared and the dialog open with the reason', async () => {
    await open({ share_typing: false, window_name: 'shell-1' });
    const realFetch = globalThis.fetch;
    globalThis.fetch = (url, opts) => /\/share$/.test(String(url))
        ? Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: false, error: 'relay down' }) })
        : realFetch(url, opts);
    try {
        await mint('view');
    } finally { globalThis.fetch = realFetch; }
    assert.equal(dialog().querySelector('[role="alert"]').textContent, 'relay down');
    assert.equal(share.isShared('@1'), false);
    assert.equal(document.getElementById('btn-shares').hidden, true);
});

test('time left reads "12d 4h left" on the red banner and in Active shares', async () => {
    await open({ share_typing: true, sandboxed: false, unsandboxed_offered: true, window_name: 'shell-1' });
    await pick('unsandboxed');
    await typeIn('Type shell-1', 'shell-1');
    MINT_EXPIRES = Date.now() / 1000 + (12 * 1440 + 4 * 60) * 60 + 30;
    await mint('unsandboxed');
    const banner = document.querySelector('.gs-unsafe-banner[data-banner-for="@1"]');
    assert.equal(banner.hidden, false);
    assert.match(banner.textContent, /UNSANDBOXED: guest can type · 12d 4h left/);
    await flush();
    const row = document.querySelector('.shares-sheet .ss-row[data-wid="@1"]');
    assert.ok(row, 'Active shares must list the share');
    assert.match(row.textContent, /12d 4h left/);
    await share.stopShare('@1');
});

// --- the live pane header follows the share state --------------------------------------

test('⭐ a change to the share state repaints the live pane\'s banner, and a stop clears it', () => {
    const banner = document.querySelector('.gs-unsafe-banner[data-banner-for="@1"]');
    assert.ok(banner, 'the pane header must carry the banner');
    share.noteShare('@1', 'typing', null);
    assert.equal(banner.hidden, true, 'a typing share shows no banner');
    share.noteShare('@1', 'unsandboxed', null);
    assert.equal(banner.hidden, false);
    assert.equal(banner.textContent.trim(), 'UNSANDBOXED: guest can type');
    share.dropShare('@1');
    assert.equal(banner.hidden, true, 'a stop clears it');
});

test('the palette offers Stop sharing on a shared session and Share on an unshared one', async () => {
    // CMX-221: the palette is a React dialog, in the overlays slot this page mounts.
    const click = r => r.el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    share.noteShare('@1', 'view', null);
    const stop = (await searchPalette('sharing')).find(r => /Stop sharing/.test(r.title));
    assert.ok(stop, 'a shared session must offer Stop sharing');
    click(stop);
    await flush(); await flush();
    assert.deepEqual(posts, [{ on: false }]);
    assert.equal(dialog(), null, 'Stop must not open the share dialog');
    assert.equal(share.isShared('@1'), false);
    const go = (await searchPalette('share')).find(r => /^Share shell$/.test(r.title));
    assert.ok(go, 'an unshared session must offer Share');
    OPTIONS = { share_typing: false, window_name: 'shell-1' };
    click(go);
    await flush(); await flush();
    assert.ok(dialog(), 'Share from the palette opens the access dialog');
    await closePalette();
});

test("the mint carries THIS pane's live size, read from its own ttyd frame", async () => {
    const ifr = [...document.querySelectorAll('#term-stage iframe.term-frame')].find(f => /\/term\/%401\//.test(f.getAttribute('src')));
    assert.ok(ifr, 'the pane must render its ttyd frame');
    ifr.contentWindow.term = { cols: 99, rows: 33 };
    await open({ share_typing: false, window_name: 'shell-1' });
    await mint('view');
    assert.deepEqual([posts[0].cols, posts[0].rows], [99, 33]);
    delete ifr.contentWindow.term;
});
