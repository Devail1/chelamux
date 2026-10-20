// SETTINGS → TERMINAL → "Size" (CMX-153: an 11px option), in a REAL DOM over the
// REAL settings markup + style.css (same idiom as tests/settings_share_typing.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - the Size select offers 11px, between 10px and 12px;
//   - choosing it stores '11' in localStorage.chela_term_fontsize and pushes the
//     new prefs to every terminal iframe (chelaApplyTermPrefs);
//   - a stored '11' renders with 11 selected after a reload, not the default.
// That the in-iframe shim then sets xterm's fontSize to 11 is guarded in
// tests/test_term_font_atlas.py.
//
// Run: pnpm exec vitest run tests/settings_term_size.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { beforeAll, beforeEach, test } from 'vitest';
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

const flush = () => new Promise(r => setTimeout(r, 0));

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><head><style>${CSS}</style></head><body>${HTML.slice(SETTINGS_START, SETTINGS_END)}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.matchMedia = q => ({ media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/frontend/src/boot.ts');
});

beforeEach(() => {
    const m = document.getElementById('settings-drawer');
    if (m.classList.contains('open')) window.chela.toggleSettings();
    localStorage.removeItem('chela_term_fontsize');
    document.querySelectorAll('iframe').forEach(f => f.remove());
});

async function openDrawer() {
    window.chela.toggleSettings();
    await flush();
    await flush();
}

const sizeSelect = () => document.getElementById('term-size-select');

test('the Size select offers 11px, between 10px and 12px', async () => {
    await openDrawer();
    const sel = sizeSelect();
    assert.ok(sel, 'no terminal Size select rendered');
    const opts = [...sel.options].map(o => [o.value, o.textContent]);
    const i = opts.findIndex(([v]) => v === '11');
    assert.ok(i >= 0, `11 is not offered: ${JSON.stringify(opts)}`);
    assert.deepEqual(opts[i], ['11', '11px']);
    assert.equal(opts[i - 1][0], '10');
    assert.equal(opts[i + 1][0], '12');
});

test("choosing 11 stores '11' and pushes it to the terminal iframes", async () => {
    await openDrawer();
    const frame = document.createElement('iframe');
    document.body.appendChild(frame);
    const seen = [];
    frame.contentWindow.chelaApplyTermPrefs = () => seen.push(localStorage.getItem('chela_term_fontsize'));
    const sel = sizeSelect();
    assert.equal(sel.getAttribute('onchange'), 'chela.setTermSize(this.value)');
    sel.value = '11';
    assert.equal(sel.value, '11', 'the select has no 11 option to choose');
    window.chela.setTermSize(sel.value);
    assert.equal(localStorage.getItem('chela_term_fontsize'), '11');
    assert.deepEqual(seen, ['11'], 'the terminal iframe was not re-applied at 11');
});

test("a stored '11' renders with 11 selected after reload, not the default", async () => {
    localStorage.setItem('chela_term_fontsize', '11');
    await openDrawer();
    const sel = sizeSelect();
    assert.equal(sel.value, '11');
    assert.equal(sel.selectedOptions[0].textContent, '11px');
});
