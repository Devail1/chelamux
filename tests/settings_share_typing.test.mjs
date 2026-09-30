// SETTINGS → COLLABORATION → "Guest typing" (CMX-403), in a REAL DOM over the REAL
// settings markup + style.css (same idiom as tests/settings_search.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - the switch lives in the Collaboration tab and reflects GET — OFF by default;
//   - env-locked (CHELA_SHARE_TYPING set) ⇒ disabled and names the env var;
//   - toggling POSTs {share_typing: <bool>};
//   - Settings search finds it by a keyword-only term ("keyboard") and hides the
//     unrelated Remote Control row (negative control in the same query).
//
// Run: node --test tests/settings_share_typing.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');
const CSS = readFileSync(join(ROOT, 'static', 'style.css'), 'utf8');
const SETTINGS_START = HTML.indexOf('<div class="drawer-scrim" id="drawer-scrim"');
const SETTINGS_END = HTML.indexOf('<!-- "+ new" popover');
if (SETTINGS_START < 0 || SETTINGS_END < 0) throw new Error('index.html markers for the settings modal moved — update this test');

let configGet = {};
let posts = [];
const flush = () => new Promise(r => setTimeout(r, 0));

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><head><style>${CSS}</style></head><body>${HTML.slice(SETTINGS_START, SETTINGS_END)}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.matchMedia = q => ({ media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    globalThis.fetch = (url, opts) => {
        const path = new URL(String(url), 'http://localhost:5005/').pathname;
        let body = {};
        if (path === '/api/config') {
            if (opts && opts.method === 'POST') {
                const sent = JSON.parse(opts.body);
                posts.push(sent);
                body = { ...configGet, ...sent, share_typing_source: 'dashboard' };
            } else body = configGet;
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(() => {
    const m = document.getElementById('settings-drawer');
    if (m.classList.contains('open')) window.chela.toggleSettings();
    posts = [];
});

function cfg(over) {
    return { share_typing: false, share_typing_source: 'default', share_typing_env_locked: false,
             share_typing_env: 'CHELA_SHARE_TYPING', remote_control: true, ...over };
}

async function openDrawer() {
    window.chela.toggleSettings();
    await flush();
    await flush();
}

function visible(el) {
    assert.ok(el, 'visible(): element not found');
    for (let n = el; n && n !== document.body; n = n.parentElement) {
        if (getComputedStyle(n).display === 'none' || n.hidden) return false;
    }
    return true;
}

test('the Guest typing switch sits in the Collaboration tab and is OFF by default', async () => {
    configGet = cfg({});
    await openDrawer();
    const box = document.getElementById('share-typing-toggle');
    assert.ok(box, 'no Guest typing switch rendered');
    assert.equal(box.closest('.settings-tabpanel').dataset.tab, 'collaboration');
    assert.equal(box.checked, false, 'share_typing must default OFF');
    assert.equal(box.disabled, false);
    assert.match(document.getElementById('share-typing-source').textContent, /Off — the built-in default/);
});

test('the switch reflects share_typing=true from GET', async () => {
    configGet = cfg({ share_typing: true, share_typing_source: 'dashboard' });
    await openDrawer();
    assert.equal(document.getElementById('share-typing-toggle').checked, true);
});

test('env-locked: disabled and names CHELA_SHARE_TYPING', async () => {
    configGet = cfg({ share_typing: true, share_typing_source: 'env', share_typing_env_locked: true });
    await openDrawer();
    assert.equal(document.getElementById('share-typing-toggle').disabled, true);
    assert.match(document.getElementById('share-typing-source').textContent, /set by CHELA_SHARE_TYPING/);
});

test('toggling POSTs {share_typing: bool}', async () => {
    configGet = cfg({});
    await openDrawer();
    await window.chela.setShareTyping(true);
    await flush();
    assert.deepEqual(posts, [{ share_typing: true }]);
    assert.equal(document.getElementById('share-typing-toggle').checked, true);
    assert.match(document.getElementById('share-typing-msg').textContent, /Saved/);
});

test('Settings search finds Guest typing by a keyword-only term, and hides unrelated rows', async () => {
    configGet = cfg({});
    await openDrawer();
    const search = document.getElementById('settings-search');
    search.value = 'keyboard';
    new Function('chela', search.getAttribute('oninput')).call(search, window.chela);
    assert.equal(visible(document.getElementById('share-typing-toggle')), true,
        '"keyboard" (only in data-keywords) must surface the Guest typing row');
    assert.equal(visible(document.getElementById('remote-control-toggle')), false,
        'negative control: an unrelated row must be filtered out by the same query');
    window.chela.clearSettingsSearch();
});
