// TOPBAR PRIMARIES → ONE MENU (CMX-109 / CMX-108 Part A re-filed), the page half.
//
// Three topbar primaries (#btn-palette "Jump to…", #btn-new "New…", #btn-overflow "More")
// fold into ONE control (#btn-primary-menu, the sidebar foot's gear since CMX-377/CMX-393)
// opening ONE menu. Since CMX-219 that menu is the primary-menu island
// (frontend/src/features/primarymenu): what its rows do, and that the safety kill switch is
// never one of them, is its own RTL suite (features/primarymenu/tests/PrimaryMenu.test.tsx).
// This file keeps what only the REAL page can show:
//
//   1. 🔴 ONE TRIGGER, NOT THREE, on the real page: the foot holds the menu's slot and
//      no other button; the old ids are gone; #btn-shares stays in .safety-float.
//   2. 🔴 ⌘K still opens the palette directly, independent of the menu.
//   3. 🔴 CMX-377: the rendered DOM has no topbar, and the inbox, the wordmark, the
//      CPU/RAM/Disk readouts and the gear all live inside .sidebar.
//
// Run: pnpm exec vitest run tests/topbarmenu.test.mjs (pytest runs it via tests/test_js_suites.py;
// it needs `pnpm install` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { APP_BODY, bootDashboardDom, closePalette, flush, paletteOpen } from './js_helpers/dashboard_dom.mjs';

// --- 1. 🔴 one trigger, not three, on the real page (the shell, CMX-248) ----------------

beforeAll(async () => {
    // CMX-221: the palette is the overlays' React dialog.
    await bootDashboardDom({ body: APP_BODY });
    // The islands commit on a later turn than the boot's own reads: wait for the inbox's.
    for (let i = 0; i < 100 && !document.querySelector('#btn-decisions'); i++) await flush();
    for (let i = 0; i < 5; i++) await flush();
});

/** The ids of `el`'s controls and slots, not counting what a slot draws inside itself. */
const ownIds = (el, sel) => [...el.querySelectorAll(sel)]
    .filter(n => !n.parentElement.closest('.island-slot, .decisions-island'))
    .map(n => n.id);

test('the old three primaries are gone; the gear is the primary-menu island', () => {
    assert.ok(document.querySelector('#primary-menu-island #btn-primary-menu'), 'the gear is not in the primary-menu slot');
    for (const id of ['btn-palette', 'btn-new', 'btn-overflow']) {
        assert.equal(document.getElementById(id), null, `#${id} is back on the page: not folded`);
    }
});

test('.sidebar-foot holds the readouts and the ONE menu, no other button', () => {
    const foot = document.querySelector('.sidebar-foot');
    assert.deepEqual(ownIds(foot, 'button[id]'), [], '.sidebar-foot has a button of its own besides the menu island');
    assert.deepEqual(ownIds(foot, '.island-slot[id]'), ['resources-island', 'primary-menu-island']);
});

test('#btn-decisions lives in .sidebar-head (CMX-393)', () => {
    // CMX-228: the Decisions inbox draws the button into its slot, after the toggle.
    const head = document.querySelector('.sidebar-head');
    assert.deepEqual(ownIds(head, 'button[id], span[id]'), ['btn-menu', 'decisions-island'], '.sidebar-head has unexpected controls');
    assert.ok(head.querySelector('#decisions-island #btn-decisions'));
});

test('#btn-shares lives in .safety-float, not .sidebar-foot', () => {
    assert.equal(document.querySelector('.sidebar-foot #shares-pill-float, .sidebar-foot #btn-shares'), null,
        '#btn-shares is inside .sidebar-foot: it must stay in .safety-float');
    // CMX-240: #btn-shares is the share feature's pill, drawn into this slot.
    assert.ok(document.querySelector('.safety-float > #shares-pill-float > #btn-shares'), "#btn-shares is not in .safety-float's slot");
});

// --- 2. 🔴 ⌘K, independent of the menu ------------------------------------------------

test('⌘K opens the palette directly — independent of the primary menu', async () => {
    assert.equal(await paletteOpen(), false);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }));
    await flush();
    assert.ok(await paletteOpen(), '⌘K no longer opens the palette: the direct shortcut regressed');
    assert.ok(document.getElementById('palette-input'), 'the open palette is not drawn');
    await closePalette();
});

// --- 3. 🔴 CMX-377's own required GUARD: the REAL rendered DOM ----------------------------
//
// The top bar is GONE, and each of the four controls it used to hold (inbox, the "chela"
// wordmark, the CPU/RAM/Disk readouts, the settings entry point) is present inside the real
// `<aside class="sidebar">` as the page renders it (the islands included), not merely in the
// file.
test('CMX-377: the REAL rendered DOM has no <header class="topbar">, and inbox/wordmark/CPU-RAM-Disk/settings all live inside .sidebar', () => {
    assert.equal(document.querySelector('header.topbar'), null,
        'a <header class="topbar"> element still exists in the real rendered DOM — CMX-377 must remove it entirely');
    assert.equal(document.querySelector('.topbar'), null,
        'a .topbar element still exists somewhere in the real rendered DOM');

    const sidebar = document.querySelector('.sidebar');
    assert.ok(sidebar, 'no <aside class="sidebar"> found in the real rendered DOM');

    assert.ok(sidebar.querySelector('#btn-decisions'),
        'the Decisions inbox button (#btn-decisions) is not inside .sidebar');
    const brand = sidebar.querySelector('.brand');
    assert.ok(brand && /chela/i.test(brand.textContent),
        'the "chela" wordmark (.brand) is not inside .sidebar, or lost its text');
    assert.ok(sidebar.querySelector('#res-cpu') && sidebar.querySelector('#res-mem') && sidebar.querySelector('#res-disk'),
        'the CPU/RAM/Disk readouts (#res-cpu/#res-mem/#res-disk) are not all inside .sidebar');
    assert.ok(sidebar.querySelector('.sidebar-foot #btn-primary-menu'),
        'the menu button that reaches Settings (#btn-primary-menu) is not inside the sidebar foot');
    assert.ok(sidebar.querySelector('.sidebar-quick .sidebar-new-btn'),
        'the New session button is not inside .sidebar-quick');
});
