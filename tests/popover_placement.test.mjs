// CMX-398 — anchored popovers open ABOVE a control at the bottom of the screen.
//
// CMX-377 moved the Settings/menu trigger (#btn-primary-menu) from the removed
// topbar into the sidebar FOOT, but openPrimaryMenu kept the topbar's math
// (`top = r.bottom + 6`): measured on the live dashboard at 1920×916 the button
// sits at y=868–906 and the 324px menu opened at top=912, almost entirely below
// the viewport. nav.js's placePopover is now the one placement for every
// popover anchored to a sidebar/footer control: below when it fits, else above,
// clamped 8px inside the viewport, scrolling when taller than the viewport, and
// re-placed on window resize while open.
//
// jsdom has no layout, so the viewport, the anchor's rect and the menu's size
// are fed in; the REAL nav.js (via main.js) does the placing, reached through
// the REAL onclick attributes. The pixel version of the same claims — a real
// Chromium at 1920×916, 1440×900 and 390×844 — is tests/browser/dashboard.test.mjs.
//
// Run: node --test tests/popover_placement.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const BODY = `
<div class="sidebar-foot">
  <button class="icon-btn" id="btn-primary-menu" onclick="chela.openPrimaryMenu(event)"></button>
  <span class="foot-stat" id="foot-cpu" title="CPU 12%">12%</span>
</div>
<div class="popover launch-menu" id="new-menu" style="display:none;"><div id="new-menu-launch"></div></div>
<div class="popover overflow-menu" id="primary-menu" style="display:none;">
  <div class="popover-item ov-item" id="pm-new" onclick="chela.openNewMenuFromPrimary()"><span>New…</span></div>
</div>`;

let win, doc;
before(async () => {
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

function size(el, width, height) {
    Object.defineProperty(el, 'offsetWidth', { value: width, configurable: true });
    Object.defineProperty(el, 'offsetHeight', { value: height, configurable: true });
}

function click(el) {
    const ev = { stopPropagation() {}, currentTarget: el, target: el };
    return new Function('chela', 'event', el.getAttribute('onclick')).call(el, win.chela, ev);
}

function placed(m) {
    return { top: parseFloat(m.style.top), left: parseFloat(m.style.left) };
}

function hideAll() {
    win.chela.hidePrimaryMenu();
    win.chela.hideNewMenu();
}

// The live measurement from the task: gear at y=868–906 in a 1920×916 window.
function footGear() {
    viewport(1920, 916);
    const btn = doc.getElementById('btn-primary-menu');
    rect(btn, { top: 868, left: 214, width: 38, height: 38 });
    return btn;
}

test('foot gear near the viewport bottom: #primary-menu opens ABOVE it, wholly on-screen', () => {
    hideAll();
    const btn = footGear();
    const m = doc.getElementById('primary-menu');
    size(m, 210, 324);
    click(btn);
    assert.equal(m.style.display, 'block');
    const { top, left } = placed(m);
    assert.ok(top + 324 <= 916 - 8, `menu bottom ${top + 324} runs past the 916px viewport (−8px margin)`);
    assert.ok(top + 324 <= 868, `menu (bottom ${top + 324}) is not above the gear (top 868)`);
    assert.equal(top, 868 - 6 - 324, 'the menu does not sit a 6px gap above the gear');
    assert.ok(top >= 8, `menu top ${top} is above the viewport's 8px margin`);
    assert.equal(left, 252 - 210, 'the menu is not right-aligned to the gear');
    hideAll();
});

test('anchor near the top: the menu still opens BELOW it', () => {
    hideAll();
    viewport(1440, 900);
    const btn = doc.getElementById('btn-primary-menu');
    rect(btn, { top: 20, left: 400, width: 38, height: 38 });
    const m = doc.getElementById('primary-menu');
    size(m, 210, 324);
    click(btn);
    const { top, left } = placed(m);
    assert.equal(top, 58 + 6, `menu top ${top} is not 6px below the anchor (bottom 58)`);
    assert.equal(left, 438 - 210, 'the menu is not right-aligned to its anchor');
    hideAll();
});

test('a menu taller than the space both ways is clamped at 8px and scrolls', () => {
    hideAll();
    const btn = footGear();
    const m = doc.getElementById('primary-menu');
    size(m, 210, 2000);
    click(btn);
    const { top } = placed(m);
    assert.equal(top, 8, `a too-tall menu is not clamped at the 8px top margin (top ${top})`);
    assert.equal(m.style.maxHeight, (916 - 16) + 'px', 'the too-tall menu got no max-height');
    assert.equal(m.style.overflowY, 'auto', 'the too-tall menu does not scroll');
    // …and a short menu opened afterwards does not inherit that cap.
    hideAll();
    size(m, 210, 100);
    click(btn);
    assert.equal(m.style.maxHeight, '', 'a stale max-height survived onto a menu that fits');
    hideAll();
});

test('the menu never runs off the RIGHT edge either', () => {
    hideAll();
    viewport(1440, 900);
    const btn = doc.getElementById('btn-primary-menu');
    rect(btn, { top: 20, left: 1420, width: 38, height: 38 });   // spills past 1440
    const m = doc.getElementById('primary-menu');
    size(m, 210, 100);
    click(btn);
    assert.equal(placed(m).left, 1440 - 8 - 210, 'the menu is not clamped 8px inside the right edge');
    hideAll();
});

test('New… from the primary menu reuses the foot anchor and also opens ABOVE it', () => {
    hideAll();
    const btn = footGear();
    size(doc.getElementById('primary-menu'), 210, 324);
    click(btn);
    const nm = doc.getElementById('new-menu');
    size(nm, 232, 400);
    click(doc.getElementById('pm-new'));
    assert.equal(nm.style.display, 'block', 'New… did not open #new-menu');
    const { top } = placed(nm);
    assert.ok(top + 400 <= 868 && top >= 8, `#new-menu top ${top} (h 400) is not above the gear, on-screen`);
    hideAll();
});

test('an open menu is re-placed when the window resizes', () => {
    hideAll();
    viewport(1440, 900);
    const btn = doc.getElementById('btn-primary-menu');
    rect(btn, { top: 400, left: 400, width: 38, height: 38 });
    const m = doc.getElementById('primary-menu');
    size(m, 210, 324);
    click(btn);
    assert.equal(placed(m).top, 438 + 6, 'precondition: there is room below at 900px');
    viewport(1440, 700);   // now 438+6+324 = 768 > 692 → must flip above
    win.dispatchEvent(new win.Event('resize'));
    assert.equal(placed(m).top, 400 - 6 - 324, 'the open menu was not re-placed above after the resize');
    hideAll();
});

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
    hideAll();
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
    hideAll();
    viewport(1440, 900);
    const stat = doc.getElementById('foot-cpu');
    rect(stat, { top: 20, left: 300, width: 60, height: 30 });
    const { top, left } = tapTip(stat, { tipW: 120, tipH: 40 });
    assert.equal(top, 50 + 6, `tip top ${top} is not 6px below the target (bottom 50)`);
    assert.equal(left, 330 - 60, `tip left ${left} is not centred on the target (centre x=330)`);
});
