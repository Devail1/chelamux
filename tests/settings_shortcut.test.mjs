// CTRL/⌘+, AND CTRL/⌘+K INSIDE A FOCUSED PANE (CMX-385), IN A REAL IFRAME.
//
// A focused pane's keydown fires in the ttyd iframe's OWN document and never reaches the
// parent, so app.py's _TERM_PALETTE_KEY_SHIM catches both keys there and calls
// `window.parent.chela.toggleSettings()` / `.openPalette()` (the iframe contract,
// frontend/src/iframeBridge.ts). Its text is lifted straight out of app.py and evaluated inside
// a REAL jsdom iframe whose parent carries a stub `chela`, so `window.parent` is the genuine
// iframe→parent relationship, not a hand-set property.
//
// The dashboard document's own wire (Ctrl+, / Ctrl+K / Esc) and the cheatsheet row are the
// palette feature's (frontend/src/features/palette/tests/Palette.test.tsx, CMX-221).
//
// Guards (each corrupt→RED):
//   - the shim calls parent.chela.toggleSettings() on Ctrl+, and preventDefaults it.
//   - the shim ignores Shift+Ctrl+, and a bare ",".
//   - MUST STILL PASS: Ctrl+K reaches parent.chela.openPalette(); Ctrl+V is not swallowed.
//
// Run: pnpm exec vitest run tests/settings_shortcut.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const APP_PY = readFileSync(join(ROOT, 'app.py'), 'utf8');

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

// That term_http actually SERVES this shim is guarded from the rendered response in
// tests/test_term_palette_key_shim.py — not from app.py's source text here (defeat
// shape 05: a `[:0]` slice at the call site kept a source-substring check green).
