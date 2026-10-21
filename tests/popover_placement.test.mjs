// CMX-398 — anchored popovers open ABOVE a control at the bottom of the screen.
//
// CMX-377 moved the Settings/menu trigger into the sidebar FOOT, where a menu opened below it
// ran off the screen. The gear menu and the launch menu are kit popovers since CMX-219 (Radix
// places them: below when it fits, else above, 8px inside the viewport); their pixel claims are
// tests/browser/dashboard.test.mjs, and shared/popover's placement math is its own unit tests
// (frontend/src/shared/popover/tests). What is left here: the touch-only tap tooltips, still
// placed by placePopover, driven through the REAL touchend listener (shared/popover
// installTapTips, installed by the islands' boot).
//
// Run: pnpm exec vitest run tests/popover_placement.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const BODY = `
<div class="sidebar-foot">
  <span class="foot-stat" id="foot-cpu" title="CPU 12%">12%</span>
</div>`;

let win, doc;
beforeAll(async () => {
    ({ dom: { window: win } } = await bootDashboardDom({ body: BODY }));
    doc = win.document;
});

function viewport(width, height) {
    Object.defineProperty(win, 'innerWidth', { value: width, configurable: true });
    Object.defineProperty(win, 'innerHeight', { value: height, configurable: true });
}

function rect(el, { top, left, width, height }) {
    el.getBoundingClientRect = () => ({ top, left, width, height, bottom: top + height, right: left + width });
}

function placed(m) {
    return { top: parseFloat(m.style.top), left: parseFloat(m.style.left) };
}

// The tap tooltips (touch-only `title` bubbles) share placePopover, CENTRED on
// their target. The sidebar foot's CPU/RAM/Disk readouts sit at the bottom of
// the screen, so a tip under them would be off-screen: it must flip above.
// Driven through the REAL touchend listener, not by calling placePopover.
function tapTip(target, { tipW, tipH }) {
    // A freshly created .tap-tip has no layout in jsdom — size it by class.
    const proto = win.HTMLElement.prototype;
    const origW = Object.getOwnPropertyDescriptor(proto, 'offsetWidth');
    const origH = Object.getOwnPropertyDescriptor(proto, 'offsetHeight');
    Object.defineProperty(proto, 'offsetWidth', { configurable: true,
        get() { return this.classList.contains('tap-tip') ? tipW : origW.get.call(this); } });
    Object.defineProperty(proto, 'offsetHeight', { configurable: true,
        get() { return this.classList.contains('tap-tip') ? tipH : origH.get.call(this); } });
    try {
        target.dispatchEvent(new win.Event('touchend', { bubbles: true, cancelable: true }));
    } finally {
        Object.defineProperty(proto, 'offsetWidth', origW);
        Object.defineProperty(proto, 'offsetHeight', origH);
    }
    const tip = doc.querySelector('.tap-tip');
    assert.ok(tip, 'tapping a titled foot readout showed no .tap-tip');
    const out = { tip, ...placed(tip) };
    win.dispatchEvent(new win.Event('scroll'));   // hideTip — clears its 4s timer
    return out;
}

test('a tap tooltip on a foot readout flips ABOVE it and is CENTRED on it', () => {
    viewport(1920, 916);
    const stat = doc.getElementById('foot-cpu');
    // Anchor 60px wide centred at x=130; the tip is 120px wide, so centred ⇒
    // left 70, whereas right-aligned would be 40 and left-aligned 100.
    rect(stat, { top: 872, left: 100, width: 60, height: 30 });
    const { tip, top, left } = tapTip(stat, { tipW: 120, tipH: 40 });
    assert.equal(tip.textContent, 'CPU 12%');
    assert.equal(top, 872 - 6 - 40, `tip top ${top} is not a 6px gap above the readout (top 872)`);
    assert.ok(top + 40 <= 916 - 8, 'the tip runs past the viewport bottom');
    assert.equal(left, 130 - 120 / 2, `tip left ${left} is not centred on the readout (centre x=130)`);
});

test('a tap tooltip near the top opens BELOW its target, still centred', () => {
    viewport(1440, 900);
    const stat = doc.getElementById('foot-cpu');
    rect(stat, { top: 20, left: 300, width: 60, height: 30 });
    const { top, left } = tapTip(stat, { tipW: 120, tipH: 40 });
    assert.equal(top, 50 + 6, `tip top ${top} is not 6px below the target (bottom 50)`);
    assert.equal(left, 330 - 60, `tip left ${left} is not centred on the target (centre x=330)`);
});
