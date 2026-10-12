// ⚖️🔔 CMX-103: the dashboard's Pending-approvals pill + sheet, in a real DOM.
//
//   1. The pill (#btn-approvals) shows only while a request is pending, with its count.
//   2. A request the server marks `blocked` says WHY and offers no Approve — only Deny.
//   3. Approve is a deliberate SECOND action: the first click only arms it ("Confirm
//      merge"); the second posts `{confirm: true}` to the shared decide API.
//   4. Deny posts to the same API's deny action.
//   5. The browser notification is OFF by default; opted in (and granted) it fires once per
//      NEW request — never for what was already waiting on load — and a missing
//      Notification API is silent.
//   6. A release request (CMX-87's record kind) renders as "Release X.Y.Z".
//
// Run: node --test tests/approvals.test.mjs (pytest runs it via tests/test_js_suites.py).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

let LISTING = { requests: [] };
let POSTS = [];

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = {};
    if (method === 'POST') {
        POSTS.push({ path, body: JSON.parse(opts.body || '{}') });
        body = { ok: true, state: 'denied' };
    } else if (path.endsWith('/api/overrides')) body = LISTING;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

const PILL = `<button id="btn-approvals" hidden><span class="pa-pill-text"></span></button>`;
let pa;
let NOTES = [];

class FakeNotification {
    static permission = 'granted';
    static requestPermission() { return Promise.resolve('granted'); }
    constructor(title, opts) { NOTES.push({ title, opts }); }
}

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PILL}</body></html>`, { url: 'http://localhost:5005/' });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement', 'Element', 'Node', 'Event']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.fetch = fakeFetch;
    pa = await import('../chela/dashboard/static/js/approvals.js');
});

beforeEach(() => {
    POSTS = []; NOTES = [];
    pa.closeApprovals();
    try { localStorage.removeItem(pa.NOTIFY_KEY); } catch (_) { /* ignore */ }
    window.Notification = FakeNotification;
});

const req = (over) => ({ id: 'override-' + 'a'.repeat(24), kind: 'override', task_id: 't1', label: 'cmx-1',
    head_sha: 'b'.repeat(40), judge_state: 'blocked', base: 'dev', reason: 'judge flaked',
    actor: 'orchestrator', age_s: 120, expires_in_s: 3600, blocked: null, pr_url: 'https://github.com/o/r/pull/1', ...over });

const tick = () => new Promise(r => setTimeout(r, 0));

test('the pill shows only while a request is pending, with the count', async () => {
    LISTING = { requests: [] };
    await pa.tickApprovals();
    assert.equal(document.getElementById('btn-approvals').hidden, true);
    LISTING = { requests: [req(), req({ id: 'override-' + 'c'.repeat(24) })] };
    await pa.tickApprovals();
    const pill = document.getElementById('btn-approvals');
    assert.equal(pill.hidden, false);
    assert.match(pill.textContent, /2 pending approvals/);
});

test('a row says what, who, why, age and expiry', async () => {
    LISTING = { requests: [req()] };
    await pa.openApprovals();
    const row = document.querySelector('.pa-row');
    assert.match(row.querySelector('.pa-what').textContent, /Override-merge cmx-1 past the judge’s blocked on bbbbbbbbbbbb/);
    const meta = row.querySelector('.pa-meta').textContent;
    assert.match(meta, /asked by orchestrator/);
    assert.match(meta, /2m ago/);
    assert.match(meta, /expires in 1h 0m/);
    assert.match(row.querySelector('.pa-why').textContent, /judge flaked/);
});

test('a blocked request says why and offers no Approve', async () => {
    LISTING = { requests: [req({ blocked: 'CI is failing on this head' })] };
    await pa.openApprovals();
    const row = document.querySelector('.pa-row');
    assert.equal(row.querySelector('.pa-approve'), null);
    assert.ok(row.querySelector('.pa-deny'));
    assert.match(row.querySelector('.pa-blocked').textContent, /CI is failing/);
});

test('approve needs a second, confirming click and posts confirm:true', async () => {
    LISTING = { requests: [req()] };
    await pa.openApprovals();
    const ap = document.querySelector('.pa-approve');
    ap.click();
    await tick();
    assert.equal(POSTS.length, 0, 'one tap must never approve');
    assert.match(ap.textContent, /Confirm merge/);
    ap.click();
    await tick();
    assert.deepEqual(POSTS[0], { path: '/api/override/' + req().id + '/approve', body: { confirm: true } });
});

test('deny posts to the deny action', async () => {
    LISTING = { requests: [req()] };
    await pa.openApprovals();
    document.querySelector('.pa-deny').click();
    await tick();
    assert.deepEqual(POSTS[0], { path: '/api/override/' + req().id + '/deny', body: {} });
});

test('browser notifications are off by default', async () => {
    LISTING = { requests: [] };
    await pa.tickApprovals();
    LISTING = { requests: [req({ id: 'override-' + 'd'.repeat(24) })] };
    await pa.tickApprovals();
    assert.equal(NOTES.length, 0);
});

test('opted in, a NEW request notifies once; what was already waiting does not', async () => {
    LISTING = { requests: [req()] };
    await pa.tickApprovals();                      // primes: 'a…' already seen
    assert.equal(await pa.setApprovalNotify('on'), true);
    const fresh = req({ id: 'override-' + 'e'.repeat(24) });
    LISTING = { requests: [req(), fresh] };
    await pa.tickApprovals();
    await pa.tickApprovals();
    assert.equal(NOTES.length, 1);
    assert.equal(NOTES[0].opts.tag, 'chela-approval-' + fresh.id);
});

test('with no Notification API, opting in stays off and nothing throws', async () => {
    delete window.Notification;
    assert.equal(await pa.setApprovalNotify('on'), false);
    assert.equal(pa.notifyEnabled(), false);
    LISTING = { requests: [req({ id: 'override-' + 'f'.repeat(24) })] };
    await pa.tickApprovals();
    assert.equal(NOTES.length, 0);
});

test('a release request renders as a release', () => {
    assert.equal(pa.whatText(req({ kind: 'release', version: '0.9.0' })), 'Release 0.9.0');
});
