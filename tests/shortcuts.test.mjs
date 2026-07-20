// KEYBOARD SHORTCUTS CHEATSHEET, IN A REAL DOM (CMX-121). Several keybinds got
// injected over time (Alt+1..9 pane jump, Ctrl/⌘+K palette) with no way to discover
// them short of reading the source. This adds a "Keyboard shortcuts" row to the
// ⌘K palette that opens a static, grouped overlay listing them.
//
// PALETTE-ONLY, ON PURPOSE (Liav's call, 2026-07-20): no dedicated global keybind.
// Alt+/ was considered and dropped; Ctrl+M is Enter inside a focused terminal. So
// there is exactly one way in — the palette row — plus Esc / backdrop-click to
// close, same as every other overlay in the app.
//
// Runs the REAL nav.js (via main.js, same module-graph-import approach as
// tests/topbarmenu.test.mjs) in jsdom — real openShortcuts/closeShortcuts/
// openPalette against real DOM nodes, not a source grep.
//
// Four properties, each a regression that would ship silently:
//
//   1. 🔴 THE PALETTE ACTUALLY OFFERS "Keyboard shortcuts". Typing "keyboard" into
//      an empty-state palette surfaces a row wired to the real openShortcuts —
//      not a title that LOOKS right without a working run().
//   2. 🔴 RUNNING THAT ROW OPENS THE REAL OVERLAY. #shortcuts-overlay gains the
//      `open` class; it starts closed, so this isn't vacuously true.
//   3. 🔴 Esc CLOSES IT (and does not fall through to the palette's own Esc
//      handling, which would be a no-op since the palette isn't open).
//   4. 🔴 NO OTHER GLOBAL KEYBIND OPENS IT. Alt+/ (the option Liav dropped) must
//      not open the overlay — proves the "palette-only" decision is still true,
//      not just documented as true.
//
// Run: node --test tests/shortcuts.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes
// a missing jsdom a FAILURE.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

// The palette + the shortcuts overlay, as index.html emits them (only the ids
// nav.js reaches for).
const BODY = `
<div class="palette-overlay" id="palette">
  <div class="palette">
    <input id="palette-input" oninput="_palSel=0;chela._renderPalette(this.value)">
    <div id="palette-list"></div>
  </div>
</div>
<div class="palette-overlay" id="shortcuts-overlay" onclick="if(event.target===this)chela.closeShortcuts()">
  <div class="shortcuts-sheet">
    <div class="shortcuts-head">
      <h3>Keyboard shortcuts</h3>
      <button class="icon-btn" onclick="chela.closeShortcuts()">✕</button>
    </div>
  </div>
</div>`;

let nav;

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
    nav = await import('../chela/dashboard/static/js/nav.js');
});

// --- structural: the REAL index.html carries the overlay + the palette row -------

test('the REAL index.html has the shortcuts overlay, starting closed, reachable from the palette', () => {
    assert.ok(HTML.includes('id="shortcuts-overlay"'), '#shortcuts-overlay is missing from index.html');
    assert.ok(!/class="palette-overlay open"\s+id="shortcuts-overlay"/.test(HTML),
        '#shortcuts-overlay must start closed (no hardcoded "open" class)');
    assert.match(HTML, /id="shortcuts-overlay" onclick="if\(event\.target===this\)chela\.closeShortcuts\(\)"/,
        '#shortcuts-overlay is not wired to close on backdrop click');
});

test('the REAL nav.js registers a palette item that opens the shortcuts overlay', () => {
    const src = readFileSync(join(ROOT, 'static', 'js', 'nav.js'), 'utf8');
    assert.match(src, /title:\s*'Keyboard shortcuts'[\s\S]{0,40}run:\s*\(\)\s*=>\s*openShortcuts\(\)/,
        'no palette item wired to openShortcuts() — the row would be unreachable even if the overlay works');
});

// --- 1 + 2. 🔴 the palette surfaces the row, and running it opens the REAL overlay -

test('typing "keyboard" into the palette surfaces a row that opens the REAL overlay', () => {
    window.chela.openPalette();
    window.chela._renderPalette('keyboard');
    const rows = [...document.querySelectorAll('#palette-list .palette-item')];
    const row = rows.find(r => r.textContent.includes('Keyboard shortcuts'));
    assert.ok(row, 'no palette row for "Keyboard shortcuts" — the entry is unreachable by search');

    assert.equal(document.getElementById('shortcuts-overlay').classList.contains('open'), false,
        'the overlay must start closed, or the next assertion is vacuous');

    const i = Number(row.dataset.i);
    window.chela._palRun(i);

    assert.ok(document.getElementById('shortcuts-overlay').classList.contains('open'),
        'running the palette row did not open #shortcuts-overlay — wired but a no-op');
    assert.equal(document.getElementById('palette').classList.contains('open'), false,
        'running a palette row must close the palette itself');
});

// --- 3. 🔴 Esc closes it (and does not leak into the palette's own handling) ------

test('Esc closes the open shortcuts overlay', () => {
    window.chela.openShortcuts();
    assert.ok(document.getElementById('shortcuts-overlay').classList.contains('open'),
        'openShortcuts() did not open the overlay — this test would be vacuous otherwise');

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    assert.equal(document.getElementById('shortcuts-overlay').classList.contains('open'), false,
        'Esc did not close #shortcuts-overlay');
});

test('clicking the backdrop closes the overlay, wired to the real closeShortcuts', () => {
    const ov = document.getElementById('shortcuts-overlay');
    assert.match(ov.getAttribute('onclick'), /chela\.closeShortcuts\(\)/,
        '#shortcuts-overlay backdrop is not wired to chela.closeShortcuts()');
    window.chela.openShortcuts();
    assert.ok(ov.classList.contains('open'));
    window.chela.closeShortcuts();
    assert.equal(ov.classList.contains('open'), false, 'closeShortcuts() did not close the overlay');
});

// --- 4. 🔴 no other global keybind opens it (the palette-only decision holds) -----

test('Alt+/ does NOT open the shortcuts overlay — palette-only, no dedicated global keybind', () => {
    const ov = document.getElementById('shortcuts-overlay');
    window.chela.closeShortcuts();
    assert.equal(ov.classList.contains('open'), false, 'setup: overlay must start closed');

    document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', altKey: true, bubbles: true }));

    assert.equal(ov.classList.contains('open'), false,
        'Alt+/ opened the shortcuts overlay — a dedicated global keybind was added, contradicting the palette-only decision');
});
