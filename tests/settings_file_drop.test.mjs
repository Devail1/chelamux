// SETTINGS → GENERAL → "File drop into terminals" (CMX-412), in a REAL DOM over the REAL
// settings markup + style.css (same idiom as tests/settings_share_typing.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - the switch lives in the General tab and reflects GET — ON by default;
//   - env-locked (CHELA_FILE_DROP set) ⇒ disabled and names the env var;
//   - toggling POSTs {file_drop: <bool>};
//   - the size cap from GET is shown;
//   - Settings search finds it by a keyword-only term ("drag") and hides the
//     unrelated Guest typing row (negative control in the same query).
//
// Run: node --test tests/settings_file_drop.test.mjs (pytest runs it via
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
                body = { ...configGet, ...sent, file_drop_source: 'dashboard' };
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
    return { file_drop: true, file_drop_source: 'default', file_drop_env_locked: false,
             file_drop_env: 'CHELA_FILE_DROP', upload_max_mb: 25, share_typing: false, ...over };
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

test('the File drop switch sits in the General tab and is ON by default', async () => {
    configGet = cfg({});
    await openDrawer();
    const box = document.getElementById('file-drop-toggle');
    assert.ok(box, 'no File drop switch rendered');
    assert.equal(box.closest('.settings-tabpanel').dataset.tab, 'general');
    assert.equal(box.checked, true, 'file_drop must default ON');
    assert.equal(box.disabled, false);
    assert.match(document.getElementById('file-drop-source').textContent, /On — the built-in default/);
});

test('the switch reflects file_drop=false from GET and shows the size cap', async () => {
    configGet = cfg({ file_drop: false, file_drop_source: 'dashboard', upload_max_mb: 7 });
    await openDrawer();
    assert.equal(document.getElementById('file-drop-toggle').checked, false);
    assert.equal(document.getElementById('file-drop-cap').textContent, '7');
});

test('env-locked: disabled and names CHELA_FILE_DROP', async () => {
    configGet = cfg({ file_drop: false, file_drop_source: 'env', file_drop_env_locked: true });
    await openDrawer();
    assert.equal(document.getElementById('file-drop-toggle').disabled, true);
    assert.match(document.getElementById('file-drop-source').textContent, /set by CHELA_FILE_DROP/);
});

test('toggling POSTs {file_drop: bool}', async () => {
    configGet = cfg({});
    await openDrawer();
    await window.chela.setFileDrop(false);
    await flush();
    assert.deepEqual(posts, [{ file_drop: false }]);
    assert.equal(document.getElementById('file-drop-toggle').checked, false);
    assert.match(document.getElementById('file-drop-msg').textContent, /Saved · file drop into terminals off/);
});

test('Settings search finds File drop by a keyword-only term, and hides unrelated rows', async () => {
    configGet = cfg({});
    await openDrawer();
    const search = document.getElementById('settings-search');
    search.value = 'drag';
    new Function('chela', search.getAttribute('oninput')).call(search, window.chela);
    assert.equal(visible(document.getElementById('file-drop-toggle')), true,
        '"drag" (only in data-keywords) must surface the File drop row');
    assert.equal(visible(document.getElementById('share-typing-toggle')), false,
        'negative control: an unrelated row must be filtered out by the same query');
    window.chela.clearSettingsSearch();
});
