// CMX-301 — the touch tap-tip leaves what is clickable alone.
//
// On touchend the tap-tip (shared/popover installTapTips) walks up to the nearest
// [title] and preventDefault()s, which cancels the click, unless the target is
// inside something it recognises as interactive. A React row keeps its onClick in
// React, not an onclick attribute, so on an iPhone a tap on a sidebar row popped
// "window: cv" and the session never opened. What is clickable is drawn with the
// pointer cursor, and the tap-tip now bails on that too.
//
// Driven through the REAL touchend listener the islands' boot installs. The pixel
// version (tap() on a phone-sized touch Chromium, the committed bundle) is
// tests/browser/sidebar_phone_tap.test.mjs.
//
// Run: pnpm exec vitest run tests/tap_tip_clickable.test.mjs
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const BODY = `
<span class="res-item" id="readout" title="CPU: 12%">12%</span>
<div id="legacy-row" onclick="void 0"><span id="legacy-row-name" title="window: legacy">legacy</span></div>
<div class="agent-row" id="react-row" style="cursor: pointer"><span id="react-name" title="window: cv" style="cursor: pointer">cv</span></div>
<span id="pointer-pill" title="Open it" style="cursor: pointer">pill</span>
<span id="help-pill" title="What this is" style="cursor: help">?</span>`;

let win, doc;
beforeAll(async () => {
    ({ dom: { window: win } } = await bootDashboardDom({ body: BODY }));
    doc = win.document;
});
beforeEach(() => win.dispatchEvent(new win.Event('scroll')));   // hide

// One tap: whether the tap-tip cancelled it (the follow-up click never comes), and the tip shown.
function tap(id) {
    const ev = new win.Event('touchend', { bubbles: true, cancelable: true });
    doc.getElementById(id).dispatchEvent(ev);
    const tip = doc.querySelector('.tap-tip');
    return { cancelled: ev.defaultPrevented, tip: tip ? tip.textContent : null };
}

test('the real boot installed the tap-tip (else every pass below proves nothing)', () => {
    assert.deepEqual(tap('readout'), { cancelled: true, tip: 'CPU: 12%' });
});

test('a titled, non-clickable element with another cursor still gets its tip', () => {
    assert.deepEqual(tap('help-pill'), { cancelled: true, tip: 'What this is' });
});

test('a titled element inside a legacy onclick row: no tip, the tap goes through', () => {
    assert.deepEqual(tap('legacy-row-name'), { cancelled: false, tip: null });
});

test('a titled name inside a React row drawn with the pointer cursor: no tip, the tap goes through (the CMX-301 bug)', () => {
    assert.deepEqual(tap('react-name'), { cancelled: false, tip: null });
});

test('any titled element drawn with the pointer cursor: no tip, the tap goes through', () => {
    assert.deepEqual(tap('pointer-pill'), { cancelled: false, tip: null });
});

test('a tap on something clickable dismisses a tip already showing', () => {
    assert.equal(tap('readout').tip, 'CPU: 12%');
    assert.equal(tap('react-name').tip, null);
});
