// CMX-301 — nav.js's touch tap-tip leaves what React draws alone.
//
// On touchend the tap-tip walks up to the nearest [title] and preventDefault()s,
// which cancels the click, unless the target is inside something it recognises
// as interactive. A React row keeps its onClick in React, not an onclick
// attribute, so on an iPhone a tap on a sidebar row popped "window: cv" and the
// session never opened. Every island root and kit overlay is marked [data-island]
// (shared/islands createIslandRoot), and the tap-tip bails there.
//
// Driven through the REAL touchend listener of the REAL nav.js. The pixel version
// (tap() on a phone-sized touch Chromium, the committed bundle) is
// tests/browser/sidebar_phone_tap.test.mjs.
//
// Run: pnpm exec vitest run tests/tap_tip_islands.test.mjs
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const BODY = `
<span class="foot-stat" id="legacy-pill" title="CPU 12%">12%</span>
<div id="legacy-row" onclick="void 0"><span id="legacy-row-name" title="window: legacy">legacy</span></div>
<div id="react-island" data-island><div class="agent-row"><span id="react-name" title="window: cv">cv</span></div></div>
<div class="popover ui-floating" data-island><div role="menuitem"><span id="kit-item" title="Rename it">Rename</span></div></div>`;

let win, doc;
beforeAll(async () => {
    ({ dom: { window: win } } = await bootDashboardDom({ body: BODY }));
    doc = win.document;
});
beforeEach(() => win.dispatchEvent(new win.Event('scroll')));   // hideTip

// One tap: whether the tap-tip cancelled it (the follow-up click never comes), and the tip shown.
function tap(id) {
    const ev = new win.Event('touchend', { bubbles: true, cancelable: true });
    doc.getElementById(id).dispatchEvent(ev);
    const tip = doc.querySelector('.tap-tip');
    return { cancelled: ev.defaultPrevented, tip: tip ? tip.textContent : null };
}

test('a titled legacy pill gets its tap-tip (and the tap is consumed by it)', () => {
    assert.deepEqual(tap('legacy-pill'), { cancelled: true, tip: 'CPU 12%' });
});

test('a titled element inside a legacy onclick row: no tip, the tap goes through', () => {
    assert.deepEqual(tap('legacy-row-name'), { cancelled: false, tip: null });
});

test('a titled element inside a React island root: no tip, the tap goes through (the CMX-301 bug)', () => {
    assert.deepEqual(tap('react-name'), { cancelled: false, tip: null });
});

test('a titled element inside a kit overlay portalled to <body>: no tip, the tap goes through', () => {
    assert.deepEqual(tap('kit-item'), { cancelled: false, tip: null });
});

test('a tap inside an island dismisses a tip already showing', () => {
    assert.equal(tap('legacy-pill').tip, 'CPU 12%');
    assert.equal(tap('react-name').tip, null);
});
