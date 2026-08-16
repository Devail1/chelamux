// THE FILES CHIP'S pointer-events PUNCH-THROUGH (CMX-299 rework round 3, PR #373) — a
// pure CSS-cascade property that no JS test could ever see: jsdom's DOM assertions in
// tests/diff_modal_wiring.test.mjs prove the `.gs-files` chip renders and that its
// `onclick` fires WHEN INVOKED DIRECTLY, but a real mouse click never reaches an element
// whose computed `pointer-events` is `none` — and `.term-ctx-bar` sets exactly that for
// its ENTIRE bar ("never intercept gridstack's bottom resize handle", style.css). The
// chip only receives real clicks because its own rule punches back through with
// `pointer-events: auto`. Flip that one declaration and every assertion in
// diff_modal_wiring.test.mjs still passes (jsdom's click() calls and dispatched
// MouseEvents ignore pointer-events entirely) while the chip goes dead in a real browser.
//
// This runs the REAL style.css through jsdom and reads the CASCADED value with
// getComputedStyle, the same technique tests/wire_live_css.test.mjs uses for the
// identical class of bug (a `position` override silently clobbered by cascade).
//
// Run: node --test tests/gs_files_pointer_events_css.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery; needs `npm ci` for jsdom).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const here = path.dirname(fileURLToPath(import.meta.url));
const styleCss = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

test('the .gs-files "Files" chip stays clickable (`pointer-events: auto`) despite its ' +
     'ancestor .term-ctx-bar setting `pointer-events: none` for the whole bar', () => {
    const dom = new JSDOM(`<!doctype html><html><head>
<style>${styleCss}</style>
</head><body>
  <div class="term-ctx-bar">
    <button type="button" class="gs-files" title="Changed files"></button>
  </div>
</body></html>`, { pretendToBeVisual: true });
    const bar = dom.window.document.querySelector('.term-ctx-bar');
    const chip = dom.window.document.querySelector('.gs-files');

    assert.equal(dom.window.getComputedStyle(bar).pointerEvents, 'none',
        'setup: .term-ctx-bar is no longer pointer-events:none — this test\'s premise changed');

    // 🔴 GUARD: dropping (or flipping to `none`) .gs-files's own
    // `pointer-events: auto` leaves the chip visually present but
    // UNCLICKABLE — it inherits `none` from .term-ctx-bar and a real click
    // never reaches it, while every jsdom DOM assertion in
    // diff_modal_wiring.test.mjs stays green (jsdom's dispatched clicks
    // don't consult pointer-events at all).
    assert.equal(dom.window.getComputedStyle(chip).pointerEvents, 'auto',
        '.gs-files does not compute to pointer-events:auto — a real click on it would be ' +
        "swallowed by .term-ctx-bar's blanket pointer-events:none (style.css, " +
        "'never intercept gridstack's bottom resize handle')");
});
