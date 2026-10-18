// SETTINGS "Concurrency" rows (CMX-164), in a REAL DOM: the real nav.js (via main.js)
// against a mocked /api/config/concurrency. Same harness as settings_dispatch.test.mjs.
//
// Run: pnpm exec vitest run tests/settings_concurrency.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = `
<div class="drawer-scrim" id="drawer-scrim" onclick="chela.toggleSettings()"></div>
<aside class="drawer" id="settings-drawer">
  <div class="drawer-body" id="drawer-body"></div>
</aside>`;

const STATE = {
    min: 1, max: 8, judge_peak_bytes: 1024 ** 3, available_bytes: null, available_source: 'unknown',
    judges: { effective: 2, source: 'env' },
    workflows: [
        { path: '/r/alpha/WORKFLOW.md', name: 'alpha', file_value: 3, override: null, effective: 3 },
        { path: '/r/beta/WORKFLOW.md', name: 'beta', file_value: 1, override: 4, effective: 4 },
    ],
};

let getResponse;      // a body, or an Error to reject with
let postResponse;
let fetchCalls = [];

const flush = () => new Promise(resolve => setTimeout(resolve, 0));

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.fetch = (url, opts) => {
        const u = String(url);
        if (u.endsWith('/api/config/concurrency')) {
            fetchCalls.push({ url: u, opts: opts || null });
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

beforeEach(() => {
    document.getElementById('settings-drawer').classList.remove('open');
    window.chela.clearSettingsSearch();
    getResponse = STATE;
    postResponse = STATE;
    fetchCalls = [];
});

async function openDrawer() {
    window.chela.toggleSettings();
    await flush();
    await flush();
}

const rows = () => [...document.querySelectorAll('#concurrency-rows .s-row')];
const resetButtons = () => rows().map(r => [...r.querySelectorAll('button')].some(b => b.textContent === 'Reset'));
const msg = () => document.getElementById('concurrency-msg').textContent;

test('renders one row per workflow plus judges; Reset only where an override is stored', async () => {
    await openDrawer();
    assert.deepEqual(rows().map(r => r.querySelector('.s-rowlabel').textContent),
        ['Agents at once: alpha', 'Agents at once: beta', 'Judges at once']);
    assert.deepEqual(rows().map(r => r.querySelector('.s-num').textContent), ['3', '4', '2']);
    assert.deepEqual(resetButtons(), [false, true, false]);      // judges come from env here
    assert.match(document.getElementById('concurrency-rows').textContent, /Source: env\./);
});

test('judges set in Settings get a Reset button', async () => {
    getResponse = { ...STATE, judges: { effective: 3, source: 'settings' } };
    await openDrawer();
    assert.deepEqual(resetButtons(), [false, true, true]);
});

test('a click POSTs the stepped value and re-renders from the response', async () => {
    await openDrawer();
    postResponse = { ...STATE, workflows: [{ ...STATE.workflows[0], override: 4, effective: 4 }, STATE.workflows[1]] };
    document.getElementById('concurrency-msg').textContent = 'stale';
    await window.chela.setConcurrency('agents', 0, 1);
    const post = fetchCalls.find(c => c.opts && c.opts.method === 'POST');
    assert.deepEqual(JSON.parse(post.opts.body), { workflow: '/r/alpha/WORKFLOW.md', value: 4 });
    assert.equal(rows()[0].querySelector('.s-num').textContent, '4');
    assert.deepEqual(resetButtons(), [true, true, false]);
    assert.equal(msg(), '');
});

test('a server rejection is shown and the rows are left as they were', async () => {
    await openDrawer();
    postResponse = { error: 'judges at once must be an integer from 1 to 8' };
    await window.chela.setConcurrency('judges', 0, 1);
    assert.equal(msg(), 'Not saved: judges at once must be an integer from 1 to 8');
    assert.equal(rows()[2].querySelector('.s-num').textContent, '2');
});

test('a failed request says nothing changed', async () => {
    await openDrawer();
    postResponse = new Error('network down');
    await window.chela.setConcurrency('judges', 0, 1);
    assert.equal(msg(), 'Save failed. Nothing changed.');
});

test('a failed load says unavailable', async () => {
    getResponse = new Error('network down');
    await openDrawer();
    assert.equal(document.getElementById('concurrency-rows').textContent, '(unavailable)');
});

test('an active settings search is re-applied to freshly rendered rows', async () => {
    await openDrawer();
    window.chela.settingsSearch('beta');
    await window.chela.setConcurrency('judges', 0, 1);           // re-renders the rows
    const hidden = rows().map(r => r.classList.contains('s-search-hide'));
    assert.deepEqual(hidden, [true, false, true]);
});
