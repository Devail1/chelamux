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
let REFUSE = null;     // when set, POST /share-mode answers {ok:false, error: REFUSE}
let SERVER_MODE = null; // when set, the mode the server reports back (it is the truth)
const EXPIRES = 1.9e9; // the UNSANDBOXED override's wall-clock end (epoch seconds)
const endsAt = t => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
// A far-off end (not today) names the day too (CMX-419: an override can last 14 days).
const endsOn = t => new Date(t * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + endsAt(t);

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (path.startsWith('/api/term/ready')) body = { ready: true };
    else if (path.endsWith('/share-options')) body = OPTIONS;
    else if (path.endsWith('/share-info')) body = INFO;
    else if (path.endsWith('/api/term/shared')) body = { '@1': { cols: 80, rows: 24, mode: MODE, expires_at: MODE === 'unsandboxed' ? EXPIRES : null } };
    else if (path.endsWith('/share-mode') && method === 'POST') {
        const b = JSON.parse(opts.body);
        modePosts.push(b);
        if (REFUSE) return Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({ ok: false, error: REFUSE }) });
        const from = MODE;
        MODE = SERVER_MODE || b.mode;
        body = { ok: true, shared: true, ...INFO, from, to: MODE, mode: MODE, expires_at: MODE === 'unsandboxed' ? 1e9 : null };
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
    modePosts = []; mintPosts = []; REFUSE = null; SERVER_MODE = null;
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
    assert.deepEqual(modePosts, [{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 30 }]);
    assert.equal(document.querySelector('.gs-share-btn[data-wid="@1"] .gs-share-mode').textContent, '⚠');
    assert.ok(freshRow().querySelector('.ss-mode-desc').textContent.includes('Ends ' + endsOn(1e9)),
        'right after the grant the row must say when full access ends');
});

// --- the duration picker on the upgrade path (CMX-419) ---------------------------------
// Upgrading a live share to UNSANDBOXED is a second road to the same grant, so it gets the
// share dialog's picker AND its >4 h second confirmation.

const UPGRADE = { share_typing: true, sandboxed: false, typing_allowed: false, unsandboxed_offered: true,
                  unsandboxed_minutes: 30, unsandboxed_choices: [30, 240, 1440, 10080, 20160],
                  unsandboxed_long_minutes: 240, window_name: 'shell-1' };

test('the upgrade offers the picker, preselected to the configured default', async () => {
    const row = await openSheet('view', UPGRADE);
    opt(row, 'unsandboxed').click();
    const sel = row.querySelector('.ss-dur');
    assert.ok(sel, 'the upgrade must offer the duration picker');
    assert.deepEqual([...sel.options].map(o => o.textContent), ['30 min', '4 h', '1 day', '7 days', '14 days']);
    assert.equal(sel.value, '30');
    assert.equal(row.querySelector('.ss-confirm-long').hidden, true, '30 min needs no second confirmation');
});

test('⭐ an upgrade past 4 h POSTs nothing until the window name is typed TWICE', async () => {
    const row = await openSheet('view', UPGRADE);
    opt(row, 'unsandboxed').click();
    const sel = row.querySelector('.ss-dur');
    sel.value = '20160'; sel.onchange();
    const longBox = row.querySelector('.ss-confirm-long');
    assert.equal(longBox.hidden, false, 'a >4 h pick must ask for the name again');
    const input = row.querySelector('.ss-confirm-in');
    const go = row.querySelector('.ss-confirm-go');
    input.value = 'shell-1'; input.oninput();
    assert.equal(go.disabled, true, 'one typed name must not arm a 14-day grant');
    go.click();
    await settle();
    assert.deepEqual(modePosts, [], 'no POST without the second confirmation');
    const longIn = row.querySelector('.ss-confirm-long-in');
    longIn.value = 'shell-1'; longIn.oninput();
    assert.equal(go.disabled, false);
    go.click();
    await settle();
    assert.deepEqual(modePosts, [{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 20160, confirm_long: 'shell-1' }],
        'the picked duration reaches the server with both confirmations');
});

test('a 4 h upgrade (not longer than 4 h) needs one confirmation and carries its minutes', async () => {
    const row = await openSheet('view', UPGRADE);
    opt(row, 'unsandboxed').click();
    const sel = row.querySelector('.ss-dur');
    sel.value = '240'; sel.onchange();
    assert.equal(row.querySelector('.ss-confirm-long').hidden, true);
    const input = row.querySelector('.ss-confirm-in');
    input.value = 'shell-1'; input.oninput();
    row.querySelector('.ss-confirm-go').click();
    await settle();
    assert.deepEqual(modePosts, [{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 240 }]);
});

test('a multi-day UNSANDBOXED row names the day it ends and the time left', async () => {
    const realNow = Date.now;
    Date.now = () => (EXPIRES - (12 * 86400 + 4 * 3600 + 30)) * 1000;
    try {
        const row = await openSheet('unsandboxed', { ...UPGRADE });
        const desc = row.querySelector('.ss-mode-desc').textContent;
        assert.ok(desc.includes('Ends ' + endsOn(EXPIRES) + ' (12d 4h left).'), `got: ${desc}`);
        assert.match(row.querySelector('.ss-mode-unsafe').textContent, /12d 4h left/);
    } finally { Date.now = realNow; }
});

test('an UNSANDBOXED row says when full access ends', async () => {
    const row = await openSheet('unsandboxed', { share_typing: true, sandboxed: false, typing_allowed: false,
                                                 unsandboxed_offered: true, window_name: 'shell-1' });
    assert.deepEqual(pressed(row), ['unsandboxed']);
    const desc = row.querySelector('.ss-mode-desc').textContent;
    assert.match(desc, /UNSANDBOXED/);
    assert.ok(desc.includes('Ends ' + endsOn(EXPIRES)), `the row must name the end time, got: ${desc}`);
    assert.ok(row.querySelector('.ss-mode-desc').classList.contains('ss-mode-desc-unsafe'));
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


// --- invariants pinned whole (judge round 2: held-out survivors) -----------------------

const ALL_OFFERS = { share_typing: true, sandboxed: false, typing_allowed: false, unsandboxed_offered: true, window_name: 'shell-1' };

test('the UNSANDBOXED override is offered ONLY when every condition holds', async () => {
    // Each condition dropped alone must hide it — as the share dialog does.
    for (const [why, o] of [
        ['window is sandboxed', { ...ALL_OFFERS, sandboxed: true, typing_allowed: true }],
        ['server does not offer it', { ...ALL_OFFERS, unsandboxed_offered: false }],
        ['no window name to confirm', { ...ALL_OFFERS, window_name: '' }],
        ['setting off', { ...ALL_OFFERS, share_typing: false }],
    ]) {
        const row = await openSheet('view', o);
        assert.equal(opt(row, 'unsandboxed'), null, `override must not be offered: ${why}`);
        assert.equal(row.querySelector('.ss-unsafe-confirm'), null, `no confirm box: ${why}`);
        terminals.closeSharesSheet();
    }
    const row = await openSheet('view', ALL_OFFERS);
    assert.ok(opt(row, 'unsandboxed'), 'offered when all hold');
});

test('a live UNSANDBOXED share always shows its pressed mode, never a re-grant box', async () => {
    // Even when the options no longer offer it (window since sandboxed / setting off).
    const row = await openSheet('unsandboxed', { share_typing: false, sandboxed: true, typing_allowed: false, window_name: 'shell-1' });
    assert.deepEqual(pressed(row), ['unsandboxed']);
    assert.equal(row.querySelector('.ss-unsafe-confirm'), null);
});

test('a refused change shows the server\'s reason and leaves the mode as it was', async () => {
    const row = await openSheet('view', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    REFUSE = 'Not a sandboxed session — start one from New session → Sandboxed';
    opt(row, 'typing').click();
    await settle();
    assert.deepEqual(modePosts, [{ mode: 'typing' }]);
    const err = row.querySelector('.ss-mode-err');
    assert.equal(err.hidden, false, 'the refusal must be visible');
    assert.equal(err.textContent, REFUSE);
    assert.deepEqual(pressed(row), ['view']);
    assert.equal(terminals._shareModes.get('@1'), 'view', 'a refusal must not move the local mode');
    assert.equal(document.querySelector('.gs-share-btn[data-wid="@1"] .gs-share-mode').textContent, '👁');
});

test('the mode shown after a change is what the SERVER reports, not what was asked', async () => {
    const row = await openSheet('view', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    SERVER_MODE = 'view';
    opt(row, 'typing').click();
    await settle();
    assert.equal(terminals._shareModes.get('@1'), 'view');
    assert.deepEqual(pressed(freshRow()), ['view']);
});

test('clicking the mode already in force POSTs nothing', async () => {
    const row = await openSheet('typing', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    opt(row, 'typing').click();
    await settle();
    assert.deepEqual(modePosts, []);
});

test('the grant handler itself refuses a wrong name (not only the disabled button)', async () => {
    const row = await openSheet('view', ALL_OFFERS);
    opt(row, 'unsandboxed').click();
    const input = row.querySelector('.ss-confirm-in');
    const go = row.querySelector('.ss-confirm-go');
    input.value = 'shell-2';
    go.onclick();
    await settle();
    assert.deepEqual(modePosts, [], 'a wrong name must never POST, whatever the button state');
    input.value = 'shell-1'; input.oninput();
    go.onclick();
    assert.equal(go.disabled, true, 'the grant button locks while the request is in flight');
    await settle();
    assert.deepEqual(modePosts, [{ mode: 'unsandboxed', confirm: 'shell-1', minutes: 30 }]);
});

test('the UNSANDBOXED confirmation names the window and the time box, and takes focus', async () => {
    const row = await openSheet('view', { ...ALL_OFFERS, unsandboxed_minutes: 45 });
    opt(row, 'unsandboxed').click();
    assert.match(row.querySelector('.ss-unsafe-confirm label').textContent, /shell-1.*45 min/);
    assert.equal(document.activeElement, row.querySelector('.ss-confirm-in'));
});

test('the "why not" reason is shown only where typing is unavailable and not in force', async () => {
    let row = await openSheet('view', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    assert.equal(row.querySelector('.ss-mode-reason'), null);
    terminals.closeSharesSheet();
    // A typing share whose window just stopped verifying: still pressed, still downgradable.
    row = await openSheet('typing', { share_typing: true, sandboxed: false, typing_allowed: false, window_name: 'shell-1' });
    assert.equal(row.querySelector('.ss-mode-reason'), null);
    assert.equal(opt(row, 'typing').disabled, false, 'the live mode stays shown as live, not greyed out');
    assert.equal(opt(row, 'view').disabled, false);
});

test('the expiry is forgotten when the share leaves UNSANDBOXED or stops', async () => {
    const row = await openSheet('unsandboxed', { ...ALL_OFFERS });
    assert.equal(terminals._shareExpiry.get('@1'), EXPIRES);
    opt(row, 'view').click();                 // the change response carries no expiry
    await settle();
    assert.equal(terminals._shareExpiry.has('@1'), false, 'a downgrade must drop the end time');
    await openSheet('unsandboxed', { ...ALL_OFFERS });
    assert.equal(terminals._shareExpiry.get('@1'), EXPIRES);
    MODE = 'view';                            // /api/term/shared now reports no expiry
    await window.chela.openSharesSheet();
    await settle();
    assert.equal(terminals._shareExpiry.has('@1'), false, 'a reconcile without an expiry must drop it');
    MODE = 'unsandboxed';
    await window.chela.openSharesSheet();
    await settle();
    await terminals._stopShare('@1');
    assert.equal(terminals._shareExpiry.has('@1'), false, 'stopping the share must drop it');
});

test('the sheet forgets its focused share on close', async () => {
    MODE = 'view';
    OPTIONS = { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' };
    await terminals.shareBtnClick(null, '@1');
    await settle();
    assert.ok(freshRow().classList.contains('ss-row-focus'));
    terminals.closeSharesSheet();
    await window.chela.openSharesSheet();
    await settle();
    assert.equal(freshRow().classList.contains('ss-row-focus'), false, 'a plain reopen highlights nothing');
    assert.equal(document.querySelector('.ss-already'), null);
});

test('a focus on a share that is no longer listed highlights nothing', async () => {
    await window.chela.openSharesSheet('@404');
    await settle();
    assert.equal(document.querySelector('.ss-row-focus'), null);
    assert.equal(document.querySelector('.ss-already'), null);
});

test('the pane pill carries the mode as glyph, title and data-mode, and hides when unshared', async () => {
    await openSheet('typing', { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' });
    const btn = document.querySelector('.gs-share-btn[data-wid="@1"]');
    const g = btn.querySelector('.gs-share-mode');
    assert.equal(g.hidden, false);
    assert.equal(g.title, '⌨ Allow typing');
    assert.equal(btn.dataset.mode, 'typing');
    await terminals._stopShare('@1');
    assert.equal(g.hidden, true, 'an unshared pane shows no mode');
    assert.equal(g.textContent, '');
    assert.equal(btn.dataset.mode, '');
});

test('adopt-first scrolls the existing share\'s row into view', async () => {
    MODE = 'view';
    OPTIONS = { share_typing: true, sandboxed: true, typing_allowed: true, window_name: 'shell-1' };
    const scrolled = [];
    const proto = window.HTMLElement.prototype;
    const had = Object.prototype.hasOwnProperty.call(proto, 'scrollIntoView');
    const prev = proto.scrollIntoView;
    proto.scrollIntoView = function () { scrolled.push(this); };
    try {
        await terminals.shareBtnClick(null, '@1');
        await settle();
    } finally {
        if (had) proto.scrollIntoView = prev; else delete proto.scrollIntoView;
    }
    assert.ok(scrolled.includes(freshRow()), 'the focused share must be scrolled to');
});


// --- judge round 3: the pill's FIRST render, pinned against the updated state ----------

// The pane's Share row as _shareBtnHTML renders it — before any _updateShareBtns runs.
function freshPill(wid) {
    const host = document.createElement('div');
    host.innerHTML = terminals._shareBtnHTML(wid);
    document.body.appendChild(host);
    const btn = host.querySelector('.gs-share-btn');
    const g = btn.querySelector('.gs-share-mode');
    const snap = () => ({ glyph: g.textContent, hidden: g.hidden, title: g.title || '',
                          mode: btn.dataset.mode, pressed: btn.getAttribute('aria-pressed'),
                          on: btn.classList.contains('on') });
    return { host, snap };
}

test('the pane pill carries the mode from its FIRST render, identical to after an update', async () => {
    const want = {
        view: { glyph: '👁', title: '👁 View only' },
        typing: { glyph: '⌨', title: '⌨ Allow typing' },
        unsandboxed: { glyph: '⚠', title: '⚠ Full access — UNSANDBOXED' },
    };
    for (const [m, w] of Object.entries(want)) {
        terminals._sharedWids.add('@7');
        terminals._shareModes.set('@7', m);
        const { host, snap } = freshPill('@7');
        const first = snap();
        assert.deepEqual(first, { glyph: w.glyph, hidden: false, title: w.title, mode: m, pressed: 'true', on: true },
            `a ${m} share's pill must show its mode on first render`);
        terminals._updateShareBtns('@7');
        assert.deepEqual(snap(), first, `first render and update must agree (${m})`);
        host.remove();
    }
    // Unshared: no glyph, hidden, no mode — and again identical after an update.
    terminals._sharedWids.delete('@7');
    terminals._shareModes.delete('@7');
    const { host, snap } = freshPill('@7');
    const first = snap();
    assert.deepEqual(first, { glyph: '', hidden: true, title: '', mode: '', pressed: 'false', on: false });
    terminals._updateShareBtns('@7');
    assert.deepEqual(snap(), first);
    host.remove();
});

test('a shared pane with no recorded mode renders as View only, never typing', async () => {
    terminals._sharedWids.add('@7');
    terminals._shareModes.delete('@7');
    const { host, snap } = freshPill('@7');
    assert.equal(snap().glyph, '👁');
    assert.equal(snap().mode, 'view');
    host.remove();
    terminals._sharedWids.delete('@7');
});

test('the UNSANDBOXED confirmation defaults to the 30-minute time box', async () => {
    const row = await openSheet('view', { ...ALL_OFFERS });   // no unsandboxed_minutes
    opt(row, 'unsandboxed').click();
    assert.match(row.querySelector('.ss-unsafe-confirm label').textContent, /for 30 min/);
});

test('only the UNSANDBOXED option wears the danger style', async () => {
    const row = await openSheet('view', { ...ALL_OFFERS });
    const unsafe = [...row.querySelectorAll('.ss-mode-opt-unsafe')].map(b => b.dataset.mode);
    assert.deepEqual(unsafe, ['unsandboxed']);
});
