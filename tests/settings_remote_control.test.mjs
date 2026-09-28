// SETTINGS "Remote Control" SWITCH (CMX-382), in a REAL DOM — same idiom as
// tests/settings_timing.test.mjs: run the real nav.js (via main.js) against a mocked
// /api/config and assert what renderSettings / _loadRemoteControlSetting /
// setRemoteControl actually produce in the DOM.
//
//   1. 🔴 THE SWITCH RENDERS AND REFLECTS GET — checked iff `remote_control` is true.
//   2. 🔴 ENV-LOCKED ⇒ DISABLED, still showing the effective value, and the copy names
//      CHELA_REMOTE_CONTROL (env wins server-side; an editable switch would lie).
//   3. 🔴 NOT env-locked ⇒ ENABLED, and toggling POSTs {remote_control: <bool>}.
//
// Run: node --test tests/settings_remote_control.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = `
<div class="drawer-scrim" id="drawer-scrim" onclick="chela.toggleSettings()"></div>
<aside class="drawer" id="settings-drawer">
  <div class="drawer-body" id="drawer-body"></div>
</aside>`;

let configGet;
let configPost;
let fetchCalls = [];

function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.fetch = (url, opts) => {
        const u = String(url);
        fetchCalls.push({ url: u, opts: opts || null });
        if (u.endsWith('/api/config')) {
            const body = (opts && opts.method === 'POST') ? configPost : configGet;
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
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
    fetchCalls = [];
});

async function openDrawer() {
    window.chela.toggleSettings();
    await flush();
    await flush();
}

function cfg(over) {
    return { remote_control: true, remote_control_source: 'default',
             remote_control_env_locked: false, remote_control_env: 'CHELA_REMOTE_CONTROL', ...over };
}

test('the switch renders and reflects GET (off from the dashboard)', async () => {
    configGet = cfg({ remote_control: false, remote_control_source: 'dashboard' });
    await openDrawer();
    const box = document.getElementById('remote-control-toggle');
    assert.ok(box, 'no Remote Control switch rendered');
    assert.equal(box.type, 'checkbox');
    assert.equal(box.checked, false, 'switch does not reflect remote_control=false');
    assert.equal(box.disabled, false, 'switch disabled though not env-locked');
    const section = document.getElementById('settings-remote-control');
    assert.match(section.textContent, /reachable from claude\.ai/);
    assert.match(section.textContent, /new/i);
});

test('the switch reflects GET (on by default)', async () => {
    configGet = cfg({});
    await openDrawer();
    assert.equal(document.getElementById('remote-control-toggle').checked, true);
});

test('env-locked: disabled, shows the effective value, names CHELA_REMOTE_CONTROL', async () => {
    configGet = cfg({ remote_control: false, remote_control_source: 'env', remote_control_env_locked: true });
    await openDrawer();
    const box = document.getElementById('remote-control-toggle');
    assert.equal(box.disabled, true, 'env-locked switch is still editable');
    assert.equal(box.checked, false);
    assert.match(document.getElementById('remote-control-source').textContent,
        /set by CHELA_REMOTE_CONTROL/);
});

test('toggling POSTs {remote_control: bool}', async () => {
    configGet = cfg({});
    configPost = cfg({ remote_control: false, remote_control_source: 'dashboard' });
    await openDrawer();
    await window.chela.setRemoteControl(false);
    await flush();
    const post = fetchCalls.find(c => c.url.endsWith('/api/config') && c.opts && c.opts.method === 'POST');
    assert.ok(post, 'toggle did not POST /api/config');
    assert.deepEqual(JSON.parse(post.opts.body), { remote_control: false });
    assert.equal(document.getElementById('remote-control-toggle').checked, false);
    assert.match(document.getElementById('remote-control-msg').textContent, /Saved/);
});
