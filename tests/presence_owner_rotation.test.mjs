// OWNER PRESENCE FOLLOWS A RE-CREATED SHARE (CMX-427) — the real terminals.js agents
// poll driving the real presence-owner.js, with real WebCrypto and a fake relay socket.
//
// A share that is stopped and re-created keeps its relay ROOM (room_id is per window)
// but gets a NEW pairing code. A dashboard page that didn't run the stop + re-share
// itself (another tab, the phone) used to keep its owner-presence session keyed from
// the OLD code: it could no longer open a guest's presence frame, so the pane showed
// only the host's avatar until a page refresh.
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - after a code rotation seen on the agents poll, owner presence is re-created with
//     the new code, and a guest frame sealed with the NEW code renders a guest avatar;
//   - ⭐ an unchanged share keeps ONE session: polls with the same share_epoch open
//     no new socket and make no /share-info round trip;
//   - a share that stops removes its presence (no ghost avatars, socket closed).
//
// Run: node --test tests/presence_owner_rotation.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { b32encode, PresenceSession, secretFromCode, T_PRESENCE } from '../chela/dashboard/static/collab/e2e.js';

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

const ROOM = 'room-tty';
const JOIN = 'https://relay.test/j/' + ROOM;
const code = () => b32encode(crypto.getRandomValues(new Uint8Array(16)));

const AGENT = { name: 'shell', window_id: '@1', online: true, shared: false, share_epoch: null };
let SHARE_INFO = {};
let shareInfoFetches = 0;

function fakeFetch(url, opts) {
    const p = String(url);
    let body = {};
    if (p.endsWith('/api/agents')) body = [{ ...AGENT }];
    else if (p.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (p.includes('/api/term/ready')) body = { ready: true };
    else if (p.endsWith('/share-info')) { shareInfoFetches++; body = { ...SHARE_INFO }; }
    else if (p.endsWith('/api/term/shared')) body = {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

// The relay socket: records every connection; a test pushes frames in via deliver().
const sockets = [];
class FakeWebSocket {
    constructor(url) { this.url = url; this.readyState = 1; this.closed = false; this.sent = []; sockets.push(this); }
    send(b) { this.sent.push(b); }
    close() { this.closed = true; this.readyState = 3; }
    deliver(env) { this.onmessage && this.onmessage({ data: env.buffer.slice(env.byteOffset, env.byteOffset + env.byteLength) }); }
}
const open = () => sockets.filter(s => !s.closed);

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {}, removeWidget(el) { el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let terminals, util, owner;
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0)); };

before(async () => {
    // BASE_PATH is the page path; pointing it at chela/dashboard on disk makes the
    // lazy import(BASE_PATH + '/static/collab/presence-owner.js') in terminals.js load
    // the REAL module (the same instance this test imports below).
    const dashDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../chela/dashboard');
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005' + dashDir + '/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'sessionStorage', 'location', 'navigator',
        'HTMLElement', 'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    globalThis.WebSocket = FakeWebSocket;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({ media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;   // no heartbeat / prune timers: the test drives it
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    owner = await import(dashDir + '/static/collab/presence-owner.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache([{ ...AGENT }]);
    await terminals.renderTerminals();
    await settle();
});

// Another page shares @1 (epoch 1, code A); this page adopts it via Share → owner presence.
async function shareFromElsewhere(epoch) {
    const c = code();
    SHARE_INFO = { pairing_code: c, join_url: JOIN, share_epoch: epoch };
    Object.assign(AGENT, { shared: true, share_epoch: epoch });
    return c;
}

async function guestFrame(c, name) {
    const g = await PresenceSession.create(secretFromCode(c), ROOM);
    const msg = { id: 'guest-' + name, name, host: false, x: 0.5, y: 0.5 };
    return g.seal(T_PRESENCE, new TextEncoder().encode(JSON.stringify(msg)));
}

const avatars = () => [...document.querySelectorAll('.gs-presence[data-presence-for="@1"] .gs-owner-fp > div')]
    .map(a => a.title);

test('a share re-created elsewhere re-keys owner presence; the new guest renders; steady polls do not churn; stop clears it', async () => {
    assert.ok(document.querySelector('.gs-presence[data-presence-for="@1"]'), 'pane header must carry a presence slot');

    // 1) First share, adopted by this page (shareBtnClick reads /share-info).
    const codeA = await shareFromElsewhere(1);
    await terminals.shareBtnClick(null, '@1');
    await settle();
    window.chela.closeSharesSheet && window.chela.closeSharesSheet();
    assert.equal(open().length, 1, 'one owner-presence socket for the share');
    const first = open()[0];
    assert.equal(first.url, 'wss://relay.test/room/' + ROOM);
    first.deliver(await guestFrame(codeA, 'Aaron'));
    await settle();
    assert.ok(avatars().some(t => t === 'Aaron'), 'guest of the first share renders: ' + avatars());

    // 2) ⭐ Unchanged share: several polls ⇒ still ONE session, no /share-info refetch.
    const fetchesBefore = shareInfoFetches, socketsBefore = sockets.length;
    for (let i = 0; i < 3; i++) { await terminals.termTick(); await settle(); }
    assert.equal(sockets.length, socketsBefore, 'an unchanged share must not reconnect on every poll');
    assert.equal(open().length, 1);
    assert.equal(shareInfoFetches, fetchesBefore, 'an unchanged share_epoch must not refetch /share-info');
    // Clicking Share on the live share re-adopts it (startOwnerPresence with the SAME
    // code) — that must reuse the session, not reconnect.
    await terminals.shareBtnClick(null, '@1');
    await settle();
    window.chela.closeSharesSheet && window.chela.closeSharesSheet();
    assert.equal(sockets.length, socketsBefore, 're-adopting the same share must not reconnect');
    assert.equal(first.closed, false);

    // 3) Stopped + re-created on ANOTHER page: same room, new code, new epoch.
    const codeB = await shareFromElsewhere(2);
    await terminals.termTick(); await settle();
    assert.equal(first.closed, true, 'the session keyed from the OLD code must be torn down');
    assert.equal(open().length, 1, 'exactly one live owner-presence socket after the rotation');
    const second = open()[0];
    assert.notEqual(second, first, 'owner presence must be re-created for the new code');

    // A guest frame sealed with the OLD code is undecodable now; one sealed with the
    // NEW code is decoded and rendered.
    second.deliver(await guestFrame(codeA, 'Stale'));
    second.deliver(await guestFrame(codeB, 'Aaron2'));
    await settle();
    assert.ok(avatars().includes('Aaron2'), 'a guest of the NEW share must render after the rotation: ' + avatars());
    assert.ok(!avatars().includes('Stale'), 'an old-code frame must not decode under the new key');

    // 4) The share stops ⇒ presence removed, no ghost avatars.
    Object.assign(AGENT, { shared: false, share_epoch: null });
    SHARE_INFO = {};
    await terminals.termTick(); await settle();
    assert.equal(second.closed, true, 'a stopped share must close its presence socket');
    assert.equal(open().length, 0);
    assert.deepEqual(avatars(), [], 'no ghost avatars after the share stops');

    // The module-level reconcile is idempotent on an unshared wid.
    assert.equal(await owner.syncOwnerPresence('@1', false, null), null);
});
