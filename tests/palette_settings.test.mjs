// "Settings" IS A PALETTE ROW — SO THE PHONE CAN REACH IT (CMX-401).
//
// On a phone there is no Ctrl+, (CMX-385) and the sidebar's "Jump to session"
// box is the only search box: it drives the SAME #palette overlay
// (sidebarJumpInput, CMX-377). Before this, nothing in that list led to
// Settings — "i cant access the settings on mobile view".
//
// Runs the REAL main.js/nav.js in jsdom over the REAL settings drawer markup
// sliced out of index.html (same approach as tests/settings_shortcut.test.mjs).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - typing "sett" into the palette shows a "Settings" row, and running it
//     opens the Settings drawer (and closes the palette);
//   - the same, when the query comes from the sidebar's "Jump to session" input;
//   - running it with Settings already open leaves it open (not a toggle-off);
//   - MUST STILL PASS: every palette row that existed before is still offered.
//
// Run: node --test tests/palette_settings.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

const SETTINGS_START = HTML.indexOf('<div class="drawer-scrim" id="drawer-scrim"');
const SETTINGS_END = HTML.indexOf('<!-- "+ new" popover');
if (SETTINGS_START < 0 || SETTINGS_END < 0) throw new Error('index.html markers for the settings modal moved — update this test');
const BODY = `
<div class="palette-overlay" id="palette">
  <div class="palette"><input id="palette-input"><div id="palette-list"></div></div>
</div>
<input type="search" id="sidebar-jump-input">
${HTML.slice(SETTINGS_START, SETTINGS_END)}`;

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
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

const settingsOpen = () => document.getElementById('settings-drawer').classList.contains('open');
const paletteOpen = () => document.getElementById('palette').classList.contains('open');
const rowTitles = () => [...document.querySelectorAll('#palette-list .palette-item .pi-title')].map(e => e.textContent);
const settingsRow = () => [...document.querySelectorAll('#palette-list .palette-item')]
    .find(r => r.querySelector('.pi-title').textContent === 'Settings');

beforeEach(() => {
    document.getElementById('settings-drawer').classList.remove('open');
    document.getElementById('drawer-scrim').classList.remove('open');
    window.chela.closePalette();
});

test('typing "sett" into the palette shows "Settings", and running it opens the drawer', () => {
    window.chela.openPalette();
    window.chela._renderPalette('sett');
    const row = settingsRow();
    assert.ok(row, `no "Settings" row for "sett" — got ${JSON.stringify(rowTitles())}`);
    assert.equal(settingsOpen(), false, 'drawer must start closed, or the next assertion is vacuous');
    window.chela._palRun(Number(row.dataset.i));
    assert.ok(settingsOpen(), 'running the "Settings" row did not open #settings-drawer');
    assert.equal(paletteOpen(), false, 'running a palette row must close the palette');
});

test('the phone path: the sidebar "Jump to session" box reaches Settings the same way', () => {
    const inp = document.getElementById('sidebar-jump-input');
    inp.value = 'settings';
    window.chela.sidebarJumpInput(inp);
    assert.ok(paletteOpen(), 'the sidebar jump input did not open the palette overlay');
    const row = settingsRow();
    assert.ok(row, `no "Settings" row from the sidebar jump box — got ${JSON.stringify(rowTitles())}`);
    window.chela._palRun(Number(row.dataset.i));
    assert.ok(settingsOpen(), 'the sidebar jump box row did not open #settings-drawer');
});

test('running "Settings" while the drawer is already open leaves it open', () => {
    window.chela.toggleSettings();
    assert.ok(settingsOpen());
    window.chela.openPalette();
    window.chela._renderPalette('settings');
    window.chela._palRun(Number(settingsRow().dataset.i));
    assert.ok(settingsOpen(), 'the palette row toggled an open Settings drawer shut');
});

test('MUST STILL PASS: every pre-existing palette row is still offered', () => {
    window.chela.openPalette();
    const titles = rowTitles();
    for (const t of ['Wall', 'Work', 'New shell window', 'Add scheduled task', 'Keyboard shortcuts', 'Settings']) {
        assert.ok(titles.includes(t), `palette lost "${t}" — got ${JSON.stringify(titles)}`);
    }
});
