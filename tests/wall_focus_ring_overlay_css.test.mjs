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
// Run: pnpm exec vitest run tests/wall_focus_ring_overlay_css.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery; needs `pnpm install` for jsdom).
import { test } from 'vitest';
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

    // CMX-104 supersedes CMX-90's "waiting wins over focused": waiting no longer draws
    // on the overlay at all (it was the focus ring's same-shape, hue-only twin), so a
    // waiting+focused pane keeps the focus ring inside and the waiting glow outside.
    test(`CMX-104: waiting does not repaint ${host}'s focus overlay`, () => {
        assert.equal(lastDecl(`${host}.term-waiting::before`, 'box-shadow'), '',
            `${host}.term-waiting::before must not paint the overlay — the inset ring there means focus only`);
        assert.match(lastDecl(`${host}.term-waiting`, 'box-shadow'), /var\(--yellow\)/,
            'the waiting pane keeps its yellow glow on the pane itself');
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
