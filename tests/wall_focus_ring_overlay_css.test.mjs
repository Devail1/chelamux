// CMX-90: THE FOCUS RING IS AN OVERLAY, NOT A PAINT UNDER THE PANE'S CHILDREN.
// An inset box-shadow on .term-pane / .grid-stack-item-content paints beneath the
// pane's own children (header, terminal iframe, toolbar — each with its own
// background), so the focused pane only showed a ring around the recap line. The
// ring now rides a `::before` overlay above the terminal with pointer-events:none.
//
// jsdom's getComputedStyle cannot resolve pseudo-elements, so the pseudo rules are
// read through the CSSOM of the REAL style.css (parsed by jsdom), and the pane
// element's own cascaded box-shadow through getComputedStyle.
//
// Run: node --test tests/wall_focus_ring_overlay_css.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery; needs `pnpm install` for jsdom).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const here = path.dirname(fileURLToPath(import.meta.url));
const styleCss = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

const dom = new JSDOM(`<!doctype html><html><head><style>${styleCss}</style></head><body>
  <div class="grid-stack-item-content term-focused" id="wall"><iframe class="term-frame"></iframe></div>
  <div class="term-pane term-focused" id="single"><iframe class="term-frame"></iframe></div>
</body></html>`, { pretendToBeVisual: true });
const win = dom.window;

// Flat list of top-level style rules, in source order (media blocks skipped — the
// ring is not viewport-scoped).
const RULES = [...win.document.styleSheets[0].cssRules].filter((r) => r.selectorText);
const sel = (r) => r.selectorText.split(',').map((x) => x.trim());
const rulesFor = (selector) => RULES.filter((r) => sel(r).includes(selector));
const lastDecl = (selector, prop) => {
    const hits = rulesFor(selector).filter((r) => r.style.getPropertyValue(prop));
    return hits.length ? hits[hits.length - 1].style.getPropertyValue(prop) : '';
};

for (const host of ['.term-pane', '.grid-stack-item-content']) {
    test(`CMX-90: ${host}'s focus ring is an inset 2px accent ring on a ::before overlay`, () => {
        const ring = lastDecl(`${host}.term-focused::before`, 'box-shadow');
        assert.match(ring, /^inset\s+0(px)?\s+0(px)?\s+0(px)?\s+2px\s+var\(--accent\)$/,
            `${host}.term-focused::before must carry the ring (inset 0 0 0 2px var(--accent)), got "${ring}"`);
    });

    test(`CMX-90: ${host}::before is an absolutely-positioned full-pane overlay above the terminal`, () => {
        assert.match(lastDecl(`${host}::before`, 'content'), /^(''|"")$/,
            `${host}::before needs content: '' or the overlay never renders`);
        assert.equal(lastDecl(`${host}::before`, 'position'), 'absolute');
        const inset = lastDecl(`${host}::before`, 'inset') || lastDecl(`${host}::before`, 'top');
        assert.match(inset, /^0(px)?$/, `${host}::before must cover the whole pane (inset: 0), got "${inset}"`);
        const z = parseInt(lastDecl(`${host}::before`, 'z-index'), 10);
        // above the ctx bar (z-index 5) that itself sits over the iframe
        assert.ok(z > 5, `${host}::before z-index must stack above the terminal and its ctx bar, got ${z}`);
    });

    test(`CMX-90: ${host}::before overlay lets clicks through to the terminal (pointer-events: none)`, () => {
        assert.equal(lastDecl(`${host}::before`, 'pointer-events'), 'none',
            `${host}::before must be pointer-events:none — otherwise the overlay swallows every click on the terminal`);
    });

    test(`CMX-90: waiting still wins over focused on ${host}'s overlay`, () => {
        const idx = (s) => RULES.findLastIndex((r) => sel(r).includes(s) && r.style.getPropertyValue('box-shadow'));
        const focused = idx(`${host}.term-focused::before`);
        const waiting = idx(`${host}.term-waiting::before`);
        assert.ok(waiting >= 0, `${host}.term-waiting::before ring rule not found`);
        assert.ok(waiting > focused,
            `${host}.term-waiting::before must come AFTER .term-focused::before (same specificity) so a ` +
            'waiting+focused pane shows yellow');
        assert.match(lastDecl(`${host}.term-waiting::before`, 'box-shadow'), /var\(--yellow\)/,
            'the waiting overlay ring must be yellow');
    });
}

test('CMX-90: the focused pane element itself no longer carries an inset ring (it would sit under its children)', () => {
    for (const id of ['wall', 'single']) {
        const shadow = win.getComputedStyle(win.document.getElementById(id)).boxShadow;
        assert.doesNotMatch(shadow, /inset/,
            `#${id} (.term-focused) computes box-shadow "${shadow}" — an inset ring on the pane paints UNDER ` +
            'its header/iframe and only shows around the recap line');
    }
});
