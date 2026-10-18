// SETTINGS "Telegram topics" (CMX-204), in a REAL DOM: the real nav.js (via main.js)
// against a mocked /api/config. Same harness as settings_concurrency.test.mjs.
//
// Run: node --test tests/settings_telegram_topics.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const CFG = { file_drop: true, telegram_delete_on_close: false, telegram_archived_topics: 3,
    telegram_cleanup_pending: false };
let getResponse, postResponse, posts, confirmAnswer;
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>
        <aside class="drawer" id="settings-drawer"><div class="drawer-body" id="drawer-body"></div></aside>
        </body></html>`, { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.matchMedia = q => ({ media: q, matches: false, addEventListener() {},
        removeEventListener() {}, addListener() {}, removeListener() {} });
    globalThis.confirm = dom.window.confirm = () => confirmAnswer;
    globalThis.fetch = (url, opts) => {
        if (String(url).endsWith('/api/config')) {
            if (opts && opts.method === 'POST') posts.push(JSON.parse(opts.body));
            const r = opts && opts.method === 'POST' ? postResponse : getResponse;
            if (r instanceof Error) return Promise.reject(r);
            return Promise.resolve({ ok: true, status: r.error ? 400 : 200, json: () => Promise.resolve(r) });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(async () => {
    document.getElementById('settings-drawer').classList.remove('open');
    getResponse = CFG;
    postResponse = CFG;
    posts = [];
    confirmAnswer = true;
    window.chela.toggleSettings();
    await flush();
    await flush();
});

const btn = () => document.getElementById('tg-cleanup-btn');
const box = () => document.getElementById('tg-delete-toggle');
const msg = () => document.getElementById('tg-topics-msg').textContent;

test('the switch and the cleanup button render from /api/config', () => {
    assert.equal(box().checked, false);
    assert.equal(box().disabled, false);
    assert.equal(btn().textContent, 'Delete archived topics (3)');
    assert.equal(btn().disabled, false);
});

test('toggling the switch POSTs it and re-renders from the response', async () => {
    postResponse = { ...CFG, telegram_delete_on_close: true };
    await window.chela.setTelegramDelete(true);
    assert.deepEqual(posts, [{ telegram_delete_on_close: true }]);
    assert.equal(box().checked, true);
    assert.equal(msg(), 'Saved · delete on close on');
});

test('cleanup asks first; a cancel posts nothing', async () => {
    confirmAnswer = false;
    await window.chela.deleteArchivedTopics();
    assert.deepEqual(posts, []);
});

test('a confirmed cleanup posts the request and shows it pending', async () => {
    postResponse = { ...CFG, telegram_cleanup_pending: true };
    await window.chela.deleteArchivedTopics();
    assert.deepEqual(posts, [{ telegram_cleanup: true }]);
    assert.equal(btn().disabled, true);
    assert.equal(msg(), 'Requested. The Telegram bridge deletes them on its next tick.');
});

test('a rejection or a failed request leaves things as they were', async () => {
    postResponse = { error: 'invalid telegram_delete_on_close' };
    await window.chela.setTelegramDelete(true);
    assert.equal(msg(), 'Rejected. Unchanged.');
    assert.equal(box().checked, false);
    postResponse = new Error('down');
    await window.chela.setTelegramDelete(true);
    assert.equal(msg(), 'Save failed. Unchanged.');
});

test('nothing archived disables the button', async () => {
    getResponse = { ...CFG, telegram_archived_topics: 0 };
    document.getElementById('settings-drawer').classList.remove('open');
    window.chela.toggleSettings();
    await flush();
    await flush();
    assert.equal(btn().textContent, 'Delete archived topics (0)');
    assert.equal(btn().disabled, true);
});
