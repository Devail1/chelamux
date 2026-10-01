// MOBILE KEYBAR "Paste" — real-DOM guard for CMX-423.
//
// The keybar's Paste button (index.html → chela.termPaste) reads the DEVICE clipboard in
// the parent page and POSTs it to /api/term/paste for the active pane (#term-agent). After
// CMX-412 it "stopped working": every early exit was silent, and a phone clipboard holding
// a screenshot came back empty from readText(), so the tap did nothing visible.
//
// Guards (each corrupt→RED):
//   - ⭐ a tap with TEXT on the clipboard delivers exactly that text to /api/term/paste for
//     the active pane (both the clipboard.read() path and the readText()-only path);
//   - an IMAGE on the clipboard takes the image path (/api/term/paste-image, then its path
//     typed), never a silent no-op;
//   - an empty clipboard says so on the button and posts nothing.
//
// Run: node --test tests/keybar_paste.test.mjs (pytest runs it via tests/test_js_suites.py).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

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
  <div id="term-keybar"><button class="kb2-key" id="paste-btn">Paste</button></div>
</div>`;

const AGENTS = [{ name: 'shell', window_id: '@1', online: true }];
let calls = [];

function fakeFetch(url, opts) {
    const path = String(url);
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/agents/context')) body = {};
    else if (path.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (path.startsWith('/api/term/ready')) body = { ready: true };
    else if (path === '/api/term/paste' || path === '/api/term/paste-image') {
        calls.push({ path, opts });
        body = path === '/api/term/paste-image' ? { path: '/tmp/chela-paste-images/abc.png' } : { pasted: 1 };
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

function setClipboard(value) {
    Object.defineProperty(globalThis.navigator, 'clipboard', { configurable: true, value });
}

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'FormData', 'Blob']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    const terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

beforeEach(() => {
    calls = [];
    const sel = document.getElementById('term-agent');
    if (![...sel.options].some(o => o.value === '@1')) sel.add(new window.Option('shell', '@1'));
    sel.value = '@1';
});

const btn = () => document.getElementById('paste-btn');
const pastes = () => calls.filter(c => c.path === '/api/term/paste').map(c => JSON.parse(c.opts.body));

test('⭐ tapping Paste with text (readText-only clipboard) delivers it to /api/term/paste for the active pane', async () => {
    setClipboard({ readText: async () => 'hello from the phone' });
    await window.chela.termPaste(btn());
    assert.deepEqual(pastes(), [{ agent: '@1', text: 'hello from the phone' }]);
});

test('⭐ tapping Paste with text via clipboard.read() delivers it to /api/term/paste', async () => {
    setClipboard({
        read: async () => [{ types: ['text/plain'], getType: async () => new Blob(['ls -la'], { type: 'text/plain' }) }],
        readText: async () => { throw new Error('read() should have answered'); },
    });
    await window.chela.termPaste(btn());
    assert.deepEqual(pastes(), [{ agent: '@1', text: 'ls -la' }]);
});

test('a refused clipboard.read() falls back to readText()', async () => {
    setClipboard({ read: async () => { throw new Error('NotAllowedError'); }, readText: async () => 'fallback' });
    await window.chela.termPaste(btn());
    assert.deepEqual(pastes(), [{ agent: '@1', text: 'fallback' }]);
});

test('an image on the clipboard takes the image path and types its path', async () => {
    setClipboard({
        read: async () => [{ types: ['image/png'], getType: async () => new Blob(['png'], { type: 'image/png' }) }],
        readText: async () => '',
    });
    await window.chela.termPaste(btn());
    assert.deepEqual(calls.map(c => c.path), ['/api/term/paste-image', '/api/term/paste']);
    assert.equal(calls[0].opts.body.get('agent'), '@1');
    assert.deepEqual(pastes(), [{ agent: '@1', text: '/tmp/chela-paste-images/abc.png' }]);
});

test('an empty clipboard posts nothing and says so on the button', async () => {
    setClipboard({ readText: async () => '' });
    await window.chela.termPaste(btn());
    assert.deepEqual(calls, []);
    assert.equal(btn().textContent, 'Empty');
});
