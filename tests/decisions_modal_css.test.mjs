// THE DECISIONS DIALOG'S VISUAL CONTRACT (CMX-288; the dialog is React since CMX-228): what
// Liav's screenshot was about, as CASCADED values under the REAL style.css, on the REAL
// Decisions island opened on the REAL dashboard DOM (jsdom resolves the cascade; it cannot do
// layout geometry, and width/max-height/overflow here are cascade values):
//   1. width-capped, never full-width: the anchored popover's uncapped width spanning the whole
//      Wall is the literal bug CMX-288 fixed;
//   2. it is visible once open (the kit Dialog's `.modal-overlay.active` cascades to flex);
//   3. the body SCROLLS: the sheet is `max-height: 80vh` + `overflow: hidden`, so the body's
//      `overflow-y: auto` is the only thing keeping decisions below the fold reachable;
//   4. its island slot in the sidebar head takes no box (`display: contents`), so the head's
//      flex row lays the button out exactly as the old static markup;
//   5. the owner chip's own rule applies: a comment above it once held `.feed-cls-*/`, which
//      closed the comment early and swallowed the next rule into a garbage selector.
// The phone viewport is checked too: no phone rule may turn the cap or the scroll area off.
//
// Run: pnpm exec vitest run tests/decisions_modal_css.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest; needs `pnpm install` for jsdom).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { bootDashboardDom, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, DESKTOP, PHONE } from './js_helpers/css_viewport.mjs';

const STYLE_CSS = fs.readFileSync(new URL('../chela/dashboard/static/style.css', import.meta.url), 'utf8');
const SLOT = sliceTemplate('<span class="decisions-island" id="decisions-island"', '</span>');

let decisions;

beforeAll(async () => {
    await bootDashboardDom({ body: `<div class="sidebar-head-actions">${SLOT}</div>` });
    decisions = await import('../chela/dashboard/frontend/src/features/decisions/index.ts');
    decisions.openDecisions();
    await flush();
});

function styled(viewport, fn) {
    const style = document.createElement('style');
    style.textContent = cssForViewport(STYLE_CSS, viewport);
    document.head.appendChild(style);
    try {
        return fn(sel => window.getComputedStyle(document.querySelector(sel)));
    } finally {
        style.remove();
    }
}

for (const [name, vp] of [['desktop', DESKTOP], ['phone', PHONE]]) {
    test(`${name}: the open dialog is visible, width-capped, and its body is the one scroll area`, () => {
        assert.ok(document.querySelector('[role="dialog"].modal-decisions'), 'setup: the dialog did not open');
        styled(vp, css => {
            assert.equal(css('.modal-overlay').display, 'flex', 'the open dialog\'s overlay is not shown');
            const sheet = css('.modal-decisions');
            assert.equal(sheet.width, '480px', 'the dialog is not width-capped');
            assert.equal(sheet.maxWidth, '92vw');
            assert.equal(sheet.maxHeight, '80vh');
            assert.equal(sheet.overflow, 'hidden');
            assert.equal(sheet.display, 'flex');
            assert.equal(css('.modal-decisions .decisions-body').overflowY, 'auto',
                'the dialog body does not scroll: decisions below the fold are unreachable');
            assert.equal(css('.decisions-list').overflowY, 'auto');
            assert.equal(css('.decisions-chip').display, 'flex', 'the owner chip\'s rule does not apply');
        });
    });
}

test('the island slot takes no box of its own; the button is the head\'s flex item', () => {
    styled(DESKTOP, css => {
        assert.equal(css('#decisions-island').display, 'contents');
        assert.equal(css('#btn-decisions').position, 'relative', 'the dot and badge anchor on the button');
    });
});
