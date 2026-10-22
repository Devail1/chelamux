// CMX-242 — the tile footer shows in EVERY pane mode (Liav 2026-10-19: its window id, model
// and context % beat the Claude Code status line), and its context fill sits inside the focus
// ring, never under it. The cascade half, run through the REAL style.css at each viewport
// (js_helpers/css_viewport.mjs unwraps the @media blocks jsdom would skip); the measured half
// is tests/browser/wall_footer_tray.test.mjs.
//
// Run: pnpm exec vitest run tests/tile_footer_modes_css.test.mjs (tests/test_js_suites.py runs it).
import { test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { cssForViewport, DESKTOP, PHONE } from './js_helpers/css_viewport.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const CSS = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

const BAR = '<div class="island-slot"><div class="term-ctx-bar"><button class="gs-wid">@1</button><span class="gs-model">m</span><span class="gs-ctx">42%</span><i class="term-ctx-fill"></i></div></div>';
const MODES = {
    'a wall tile': [DESKTOP, '', `<div class="grid-stack-item"><div class="grid-stack-item-content">${BAR}</div></div>`],
    'a maximized wall tile': [DESKTOP, 'pane-is-maximized', `<div class="grid-stack-item"><div class="grid-stack-item-content pane-maximized">${BAR}</div></div>`],
    'the phone pane': [PHONE, '', `<div id="term-stage" class="term-single"><div class="term-pane">${BAR}</div></div>`],
    'the phone pane filling the screen': [PHONE, 'term-mobile-full', `<div id="term-stage" class="term-single"><div class="term-pane">${BAR}</div></div>`],
    'a maximized phone pane': [PHONE, 'pane-is-maximized', `<div id="term-stage" class="term-single"><div class="term-pane pane-maximized">${BAR}</div></div>`],
};

function page(vp, bodyClass, html) {
    const dom = new JSDOM(`<!doctype html><html><head><style>${cssForViewport(CSS, vp)}</style></head>
        <body class="${bodyClass}">${html}</body></html>`, { pretendToBeVisual: true });
    return dom.window;
}

for (const [mode, [vp, bodyClass, html]] of Object.entries(MODES)) {
    test(`the footer and its id, model and context % show on ${mode}`, () => {
        const win = page(vp, bodyClass, html);
        for (const sel of ['.term-ctx-bar', '.gs-wid', '.gs-model', '.gs-ctx']) {
            const el = win.document.querySelector(sel);
            const cs = win.getComputedStyle(el);
            assert.notEqual(cs.display, 'none', `${mode}: ${sel} is display:none`);
            assert.notEqual(cs.visibility, 'hidden', `${mode}: ${sel} is visibility:hidden`);
        }
    });
}

test('the phone pane filling the screen keeps the terminal\'s room for the footer under it', () => {
    const win = page(PHONE, 'term-mobile-full',
        `<div id="term-stage" class="term-single"><div class="term-pane"><iframe class="term-frame"></iframe>${BAR}</div></div>`);
    assert.equal(win.getComputedStyle(win.document.querySelector('.term-frame')).marginBottom, 'var(--term-ctx-bar-h)',
        'the terminal gives up its bottom margin, so the footer would cover its last row');
});

// The focus ring: the pane's ::before overlay, `inset 0 0 0 2px`, drawn ABOVE the footer.
test('the context fill is inset by the focus ring\'s 2px on its bottom, left and right', () => {
    const win = page(DESKTOP, '', `<div class="grid-stack-item-content term-focused">${BAR}</div>`);
    const fill = win.getComputedStyle(win.document.querySelector('.term-ctx-fill'));
    assert.equal(fill.position, 'absolute');
    for (const side of ['bottom', 'left', 'right']) {
        assert.equal(fill[side], '2px', `the fill's ${side} is ${fill[side]}: under the 2px focus ring`);
    }
});
