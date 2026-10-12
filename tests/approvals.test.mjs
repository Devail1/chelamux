// ⚖️ CMX-103: the Pending approvals pill + sheet, in a real DOM.
//
//   1. The pill (#btn-approvals) shows only while a request waits, with its count.
//   2. Approve is TWO taps: the first only arms the row; the second posts `confirm: true`.
//   3. A request the server marks `blocked` (head moved, CI red…) says why — no Approve.
//   4. A share guest (`can_decide: false`) gets no Approve / Deny at all.
//   5. The browser notification is opt-in (off by default), fires once per NEW request,
//      and does nothing — without throwing — where the Notification API is missing.
//
// Run: node --test tests/approvals.test.mjs (pytest runs it via tests/test_js_suites.py).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

let LISTING = { approvals: [], can_decide: true };
let POSTS = [];
let NOTES = [];

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = {};
    if (method === 'POST') {
        POSTS.push({ path, body: JSON.parse(opts.body || '{}') });
        body = { ok: true, state: 'merged' };
    } else if (path.endsWith('/api/approvals')) body = LISTING;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

class FakeNotification {
    constructor(title, opts) { NOTES.push({ title, body: opts && opts.body }); }
}
FakeNotification.permission = 'granted';

const PILL = `<button id="btn-approvals" hidden><span class="ap-pill-text"></span></button>`;
let ap;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PILL}</body></html>`, { url: 'http://localhost:5005/' });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement', 'Element', 'Node', 'Event']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.fetch = fakeFetch;
    ap = await import('../chela/dashboard/static/js/approvals.js');
});

beforeEach(() => {
    POSTS = []; NOTES = [];
    ap.closeApprovals();
    localStorage.clear();
    window.Notification = FakeNotification;
});

const row = (over) => ({ kind: 'override', id: 'override-' + 'a'.repeat(24),
    what: 'override-merge cmx-1 past blocked on aaaaaaaaaaaa', actor: 'orchestrator',
    reason: 'judge flaked', age_s: 120, expires_in_s: 3600, pr_url: 'https://github.com/o/r/pull/1',
    blocked: null, ...over });
const tick = () => new Promise(r => setTimeout(r, 0));

test('the pill shows only while a request waits, with the count', async () => {
    LISTING = { approvals: [], can_decide: true };
    await ap.tickApprovals();
    assert.equal(document.getElementById('btn-approvals').hidden, true);
    LISTING = { approvals: [row(), row({ id: 'override-' + 'b'.repeat(24) })], can_decide: true };
    await ap.tickApprovals();
    const pill = document.getElementById('btn-approvals');
    assert.equal(pill.hidden, false);
    assert.match(pill.textContent, /2 pending approvals/);
});

test('a row shows what, who asked, the reason, age and time to expiry', async () => {
    LISTING = { approvals: [row()], can_decide: true };
    await ap.openApprovals();
    const text = document.querySelector('.ap-row').textContent;
    assert.match(text, /override-merge cmx-1 past blocked on aaaaaaaaaaaa/);
    assert.match(text, /asked by orchestrator/);
    assert.match(text, /judge flaked/);
    assert.match(text, /2 min ago/);
    assert.match(text, /expires in 1h 0m/);
});

test('approve needs a second, deliberate tap — the first posts nothing', async () => {
    LISTING = { approvals: [row()], can_decide: true };
    await ap.openApprovals();
    document.querySelector('.ap-approve').click();
    await tick();
    assert.equal(POSTS.length, 0, 'one tap must never decide');
    assert.ok(document.querySelector('.ap-confirm'), 'the first tap arms a confirm');
    document.querySelector('.ap-confirm').click();
    await tick();
    assert.deepEqual(POSTS[0], { path: '/api/approvals/' + row().id + '/approve', body: { confirm: true } });
});

test('cancel disarms the confirm', async () => {
    LISTING = { approvals: [row()], can_decide: true };
    await ap.openApprovals();
    document.querySelector('.ap-approve').click();
    document.querySelector('.ap-cancel').click();
    assert.equal(document.querySelector('.ap-confirm'), null);
    assert.ok(document.querySelector('.ap-approve'));
    assert.equal(POSTS.length, 0);
});

test('deny posts deny', async () => {
    LISTING = { approvals: [row()], can_decide: true };
    await ap.openApprovals();
    document.querySelector('.ap-deny').click();
    await tick();
    assert.deepEqual(POSTS[0], { path: '/api/approvals/' + row().id + '/deny', body: {} });
});

test('a stale request says why and offers no Approve', async () => {
    LISTING = { approvals: [row({ blocked: 'CI is failing, not green' })], can_decide: true };
    await ap.openApprovals();
    const r = document.querySelector('.ap-row');
    assert.equal(r.querySelector('.ap-approve'), null);
    assert.equal(r.querySelector('.ap-confirm'), null);
    assert.match(r.querySelector('.ap-blocked').textContent, /Can't be approved: CI is failing/);
});

test('a share guest (can_decide false) sees no Approve or Deny', async () => {
    LISTING = { approvals: [row()], can_decide: false };
    await ap.openApprovals();
    assert.ok(document.querySelector('.ap-row'));
    assert.equal(document.querySelectorAll('.ap-approve, .ap-deny, .ap-confirm').length, 0);
    assert.equal(ap.rowHTML(row(), false, true).includes('ap-confirm'), false);
});

test('notifications are off by default — a new request notifies nobody', async () => {
    LISTING = { approvals: [], can_decide: true };
    await ap.tickApprovals();
    LISTING = { approvals: [row({ id: 'override-' + 'c'.repeat(24) })], can_decide: true };
    await ap.tickApprovals();
    assert.equal(ap.notifyEnabled(), false);
    assert.equal(NOTES.length, 0);
});

test('opted in, ONE notification per new request, none for one already seen', async () => {
    assert.equal(await ap.setApprovalNotify(true), true);
    const id = 'override-' + 'd'.repeat(24);
    LISTING = { approvals: [row({ id, what: 'override-merge cmx-7 past blocked on dddd' })], can_decide: true };
    await ap.tickApprovals();
    await ap.tickApprovals();
    assert.equal(NOTES.length, 1);
    assert.match(NOTES[0].body, /cmx-7/);
});

test('without the Notification API it degrades silently', async () => {
    await ap.setApprovalNotify(true);
    delete window.Notification;
    LISTING = { approvals: [row({ id: 'override-' + 'e'.repeat(24) })], can_decide: true };
    await ap.tickApprovals();   // must not throw
    assert.equal(NOTES.length, 0);
    assert.equal(document.getElementById('btn-approvals').hidden, false);
});
