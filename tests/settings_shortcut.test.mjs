// CTRL/⌘+, TOGGLES SETTINGS — BOTH WIRES, IN A REAL DOM (CMX-385).
//
// Like Ctrl/⌘+K, the shortcut needs two wires because a focused pane's keydown
// fires in the ttyd iframe's OWN document and never reaches the parent:
//
//   1. nav.js's document keydown handler (the dashboard chrome has focus) —
//      exercised here by importing the REAL main.js/nav.js into jsdom over the
//      REAL settings modal markup sliced out of index.html;
//   2. app.py's _TERM_PALETTE_KEY_SHIM (a pane has focus) — its text is lifted
//      straight out of app.py and evaluated inside a REAL jsdom iframe whose
//      parent carries a stub `chela`, so `window.parent` is the genuine
//      iframe→parent relationship, not a hand-set property.
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - Ctrl+, opens Settings, a second press closes it; Shift+Ctrl+, does nothing.
//   - Esc closes Settings.
//   - the shim calls parent.chela.toggleSettings() on Ctrl+, and preventDefaults it.
//   - MUST STILL PASS: Ctrl+K opens the palette from both wires; Ctrl+V is not
//     swallowed by the shim (the paste shim owns it).
//
// Run: node --test tests/settings_shortcut.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');
const APP_PY = readFileSync(join(ROOT, 'app.py'), 'utf8');

const SETTINGS_START = HTML.indexOf('<div class="drawer-scrim" id="drawer-scrim"');
const SETTINGS_END = HTML.indexOf('<!-- "+ new" popover');
if (SETTINGS_START < 0 || SETTINGS_END < 0) throw new Error('index.html markers for the settings modal moved — update this test');
const BODY = `
<div class="palette-overlay" id="palette">
  <div class="palette"><input id="palette-input"><div id="palette-list"></div></div>
</div>
${HTML.slice(SETTINGS_START, SETTINGS_END)}`;

// The shim as app.py defines it: the concatenated Python string literals of the
// `_TERM_PALETTE_KEY_SHIM = ( ... )` tuple, comment lines dropped.
function shimFromAppPy() {
    const m = /^_TERM_PALETTE_KEY_SHIM = \(\n([\s\S]*?)\n\)\n/m.exec(APP_PY);
    if (!m) throw new Error('_TERM_PALETTE_KEY_SHIM moved in app.py — update this test');
    const parts = m[1].split('\n').filter(l => !/^\s*#/.test(l))
        .map(l => /^\s*"((?:[^"\\]|\\.)*)"\s*$/.exec(l))
        .map((x, i) => { if (!x) throw new Error(`unparsed shim line ${i}`); return x[1]; });
    const html = parts.join('');
    const js = /^<script>([\s\S]*)<\/script>$/.exec(html);
    if (!js) throw new Error('shim is not a single <script> block');
    return js[1];
}

let dom;

before(async () => {
    dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
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
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

const settingsOpen = () => document.getElementById('settings-drawer').classList.contains('open');
const paletteOpen = () => document.getElementById('palette').classList.contains('open');
const key = (init) => {
    const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    document.dispatchEvent(ev);
    return ev;
};

beforeEach(() => {
    document.getElementById('settings-drawer').classList.remove('open');
    document.getElementById('drawer-scrim').classList.remove('open');
    window.chela.closePalette();
});

// --- wire 1: the dashboard document -------------------------------------------

test('Ctrl+, opens Settings and a second Ctrl+, closes it', () => {
    assert.equal(settingsOpen(), false, 'setup: Settings must start closed');
    const ev = key({ key: ',', ctrlKey: true });
    assert.equal(settingsOpen(), true, 'Ctrl+, did not open Settings');
    assert.equal(ev.defaultPrevented, true, 'Ctrl+, must be swallowed (browser default)');
    key({ key: ',', ctrlKey: true });
    assert.equal(settingsOpen(), false, 'a second Ctrl+, did not close Settings');
});

test('⌘+, (metaKey) toggles Settings too', () => {
    key({ key: ',', metaKey: true });
    assert.equal(settingsOpen(), true, '⌘+, did not open Settings');
});

test('Shift+Ctrl+, and Alt+Ctrl+, do NOT toggle Settings', () => {
    const ev = key({ key: ',', ctrlKey: true, shiftKey: true });
    assert.equal(settingsOpen(), false, 'Shift+Ctrl+, opened Settings');
    assert.equal(ev.defaultPrevented, false);
    key({ key: ',', ctrlKey: true, altKey: true });
    assert.equal(settingsOpen(), false, 'Alt+Ctrl+, opened Settings');
    key({ key: ',' });
    assert.equal(settingsOpen(), false, 'a bare "," opened Settings');
});

test('Esc closes an open Settings', () => {
    window.chela.toggleSettings();
    assert.equal(settingsOpen(), true, 'setup: toggleSettings() did not open Settings');
    key({ key: 'Escape' });
    assert.equal(settingsOpen(), false, 'Esc did not close Settings');
});

test('Esc with the palette over Settings closes only the palette', () => {
    window.chela.toggleSettings();
    window.chela.openPalette();
    key({ key: 'Escape' });
    assert.equal(paletteOpen(), false, 'Esc did not close the palette');
    assert.equal(settingsOpen(), true, 'one Esc closed both layers');
});

test('MUST STILL PASS: Ctrl+K still toggles the palette from the document', () => {
    key({ key: 'k', ctrlKey: true });
    assert.equal(paletteOpen(), true, 'Ctrl+K no longer opens the palette');
    assert.equal(settingsOpen(), false, 'Ctrl+K opened Settings');
    key({ key: 'k', ctrlKey: true });
    assert.equal(paletteOpen(), false, 'Ctrl+K no longer closes the palette');
});

test('toggleSettings is on the window.chela namespace (the shim reaches it there)', () => {
    assert.equal(typeof window.chela.toggleSettings, 'function');
});

// --- wire 2: the pane shim from app.py, inside a real iframe ------------------

function paneWithShim() {
    const outer = new JSDOM('<!doctype html><html><body><iframe></iframe></body></html>',
        { url: 'http://localhost:5005/', runScripts: 'outside-only' });
    const calls = [];
    outer.window.chela = {
        toggleSettings: () => calls.push('toggleSettings'),
        openPalette: () => calls.push('openPalette'),
    };
    const inner = outer.window.document.querySelector('iframe').contentWindow;
    assert.equal(inner.parent, outer.window, 'setup: iframe parent is not the outer window');
    inner.eval(shimFromAppPy());
    const press = (init) => {
        const ev = new inner.KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
        inner.document.body.dispatchEvent(ev);
        return ev;
    };
    return { calls, press };
}

test('the app.py shim calls parent.chela.toggleSettings() on Ctrl+, and swallows it', () => {
    const { calls, press } = paneWithShim();
    const ev = press({ key: ',', ctrlKey: true });
    assert.deepEqual(calls, ['toggleSettings'], 'the shim did not call parent.chela.toggleSettings()');
    assert.equal(ev.defaultPrevented, true, 'the shim did not preventDefault Ctrl+,');
    press({ key: ',', metaKey: true });
    assert.deepEqual(calls, ['toggleSettings', 'toggleSettings'], '⌘+, did not reach toggleSettings()');
});

test('the shim ignores Shift+Ctrl+, and a bare ","', () => {
    const { calls, press } = paneWithShim();
    const a = press({ key: ',', ctrlKey: true, shiftKey: true });
    const b = press({ key: ',' });
    assert.deepEqual(calls, [], 'the shim fired on a non-matching combo');
    assert.equal(a.defaultPrevented || b.defaultPrevented, false);
});

test('MUST STILL PASS: the shim still opens the palette on Ctrl+K', () => {
    const { calls, press } = paneWithShim();
    const ev = press({ key: 'k', ctrlKey: true });
    assert.deepEqual(calls, ['openPalette'], 'Ctrl+K no longer reaches parent.chela.openPalette()');
    assert.equal(ev.defaultPrevented, true);
});

test('MUST STILL PASS: the shim leaves Ctrl+V alone (the paste shim owns it)', () => {
    const { calls, press } = paneWithShim();
    const ev = press({ key: 'v', ctrlKey: true });
    assert.deepEqual(calls, [], 'the palette/settings shim reacted to Ctrl+V');
    assert.equal(ev.defaultPrevented, false, 'the palette/settings shim swallowed Ctrl+V');
});

test('the ttyd page is actually served with the shim', () => {
    assert.match(APP_PY, /\+ _TERM_PASTE_KEY_SHIM \+ _TERM_PALETTE_KEY_SHIM/,
        'term_http no longer injects _TERM_PALETTE_KEY_SHIM');
});
