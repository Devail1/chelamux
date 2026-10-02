// 🙋 CMX-7: the operator's access-request sheet, in a real DOM.
//
//   1. The pill (#btn-access-req) shows only while a request is PENDING, with its count.
//   2. A request the server refuses (a secrets dir, $HOME, /mnt/*) shows WHY and offers
//      no Approve button — only Deny.
//   3. Approve defaults: 60 min, read-only. "allow write" exists only when the guest asked
//      for write, and starts unticked. The click posts exactly those defaults.
//   4. With Guest typing off, Approve is disabled (the server refuses it too).
//
// Run: node --test tests/access_requests.test.mjs (pytest runs it via tests/test_js_suites.py).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

let LISTING = { requests: [], share_typing: true, default_minutes: 60 };
let POSTS = [];

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = {};
    if (method === 'POST') {
        POSTS.push({ path, body: JSON.parse(opts.body || '{}') });
        body = { ok: true };
    } else if (path.endsWith('/api/share-requests')) body = LISTING;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

const PILL = `<button id="btn-access-req" hidden><span class="ar-pill-text"></span></button>`;
let ar;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PILL}</body></html>`, { url: 'http://localhost:5005/' });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement', 'Element', 'Node', 'Event']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.fetch = fakeFetch;
    ar = await import('../chela/dashboard/static/js/accessreq.js');
});

beforeEach(() => { POSTS = []; ar.closeAccessRequests(); });

const pending = (over) => ({ id: 'abcdefabcdef-0', sid: 'abcdefabcdef', kind: 'mount', target: '/srv/data',
    access: 'ro', status: 'pending', window: 'sandbox-1', description: 'mount /srv/data (read-only)',
    reason: 'need the dataset', refusal: null, ...over });

test('the pill shows only while a request is pending, with the count', async () => {
    LISTING = { requests: [], share_typing: true };
    await ar.tickAccessRequests();
    assert.equal(document.getElementById('btn-access-req').hidden, true);
    LISTING = { requests: [pending(), pending({ id: 'x-1' }), pending({ id: 'x-2', status: 'denied' })], share_typing: true };
    await ar.tickAccessRequests();
    const pill = document.getElementById('btn-access-req');
    assert.equal(pill.hidden, false);
    assert.match(pill.textContent, /2 access requests/);
});

test('a refused request says why and offers no Approve', async () => {
    LISTING = { requests: [pending({ target: '~/.ssh', refusal: '/home/u/.ssh holds secrets or system state — it can never be mounted' })], share_typing: true };
    await ar.openAccessRequests();
    const row = document.querySelector('.ar-row');
    assert.equal(row.querySelector('.ar-approve'), null);
    assert.ok(row.querySelector('.ar-deny'));
    assert.match(row.querySelector('.ar-refused').textContent, /can never be mounted/);
});

test('approve defaults to 60 min, read-only; write only offered when asked, unticked', async () => {
    LISTING = { requests: [pending(), pending({ id: 'rw-1', access: 'rw' })], share_typing: true, default_minutes: 60 };
    await ar.openAccessRequests();
    const [ro, rw] = document.querySelectorAll('.ar-row');
    assert.equal(ro.querySelector('.ar-dur').value, '60');
    assert.equal(ro.querySelector('.ar-rw-in'), null, 'a read-only request offers no write');
    assert.equal(rw.querySelector('.ar-rw-in').checked, false, 'write starts unticked');
    ro.querySelector('.ar-approve').click();
    await new Promise(r => setTimeout(r, 0));
    assert.deepEqual(POSTS[0], { path: '/api/share-requests/abcdefabcdef-0/approve', body: { minutes: 60, rw: false } });
});

test('with Guest typing off, Approve is disabled', async () => {
    LISTING = { requests: [pending()], share_typing: false };
    await ar.openAccessRequests();
    assert.equal(document.querySelector('.ar-approve').disabled, true);
    assert.match(document.querySelector('.ar-row').textContent, /Guest typing is off/);
});

test('an approved request shows its time left and a Revoke', async () => {
    LISTING = { requests: [pending({ status: 'approved', seconds_left: 1800, rw: false })], share_typing: true };
    await ar.openAccessRequests();
    const row = document.querySelector('.ar-row');
    assert.match(row.textContent, /30 min left/);
    assert.match(row.textContent, /read-only/);
    row.querySelector('.ar-revoke').click();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(POSTS[0].path, '/api/share-requests/abcdefabcdef-0/revoke');
});
