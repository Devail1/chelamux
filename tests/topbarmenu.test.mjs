// TOPBAR PRIMARIES → ONE MENU (CMX-109 / CMX-108 Part A re-filed), the page half.
//
// Three topbar primaries (#btn-palette "Jump to…", #btn-new "New…", #btn-overflow "More")
// fold into ONE control (#btn-primary-menu, the sidebar foot's gear since CMX-377/CMX-393)
// opening ONE menu. Since CMX-219 that menu is the primary-menu island
// (frontend/src/features/primarymenu): what its rows do, and that the safety kill switch is
// never one of them, is its own RTL suite (features/primarymenu/tests/PrimaryMenu.test.tsx).
// This file keeps what only the REAL page can show:
//
//   1. 🔴 ONE TRIGGER, NOT THREE, in the real template: the foot holds the menu's slot and
//      no other button; the old ids are gone; #btn-shares stays in .safety-float.
//   2. 🔴 ⌘K still opens the palette directly, independent of the menu.
//   3. 🔴 CMX-377: the rendered DOM has no topbar, and the inbox, the wordmark, the
//      CPU/RAM/Disk readouts and the gear all live inside .sidebar.
//
// Run: pnpm exec vitest run tests/topbarmenu.test.mjs (pytest runs it via tests/test_js_suites.py;
// it needs `pnpm install` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { bootDashboardDom, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');

// --- 1. 🔴 one trigger, not three, in the real template ------------------------------

test('the old three primaries are gone; the gear is the primary-menu island', () => {
    assert.ok(HTML.includes('id="primary-menu-island"'), 'the primary-menu island slot is missing from index.html');
    assert.ok(!HTML.includes('id="btn-palette"'), '#btn-palette is still in index.html: not folded');
    assert.ok(!HTML.includes('id="btn-new"'), '#btn-new is still in index.html: not folded');
    assert.ok(!HTML.includes('id="btn-overflow"'), '#btn-overflow is still in index.html: not folded');
});

test('.sidebar-foot in the REAL index.html holds the readouts and the ONE menu, no other button', () => {
    const foot = HTML.match(/<div class="sidebar-foot">[\s\S]*?\n    <\/div>/)[0];
    assert.deepEqual([...foot.matchAll(/<button[^>]*\bid="([^"]+)"/g)].map(m => m[1]), [],
        '.sidebar-foot has a button of its own besides the menu island');
    assert.deepEqual([...foot.matchAll(/<span class="island-slot" id="([^"]+)"/g)].map(m => m[1]),
        ['resources-island', 'primary-menu-island']);
});

test('#btn-decisions lives in .sidebar-head in the REAL index.html (CMX-393)', () => {
    // CMX-228: the Decisions island draws the button into its slot, after the toggle.
    const head = HTML.match(/<div class="sidebar-head">[\s\S]*?\n    <\/div>/)[0];
    const ids = [...head.matchAll(/<(?:button|span)[^>]*\bid="([^"]+)"/g)].map(m => m[1]);
    assert.deepEqual(ids, ['btn-menu', 'decisions-island'],
        `.sidebar-head has unexpected controls: ${ids.join(', ')}`);
});

test('#btn-shares lives in .safety-float, not .sidebar-foot, in the REAL index.html', () => {
    const foot = HTML.match(/<div class="sidebar-foot">[\s\S]*?\n    <\/div>/)[0];
    assert.ok(!foot.includes('btn-shares'), '#btn-shares is inside .sidebar-foot: it must stay in .safety-float');
    const safetyFloat = HTML.match(/<div class="safety-float">[\s\S]*?\n<\/div>/);
    assert.ok(safetyFloat, '.safety-float block not found in index.html');
    assert.ok(safetyFloat[0].includes('id="btn-shares"'), '#btn-shares is missing from .safety-float');
});

// --- the real page ----------------------------------------------------------------------

beforeAll(async () => {
    const APP_HTML = sliceTemplate('<div class="app">', '</main>\n\n</div>');
    const PALETTE = sliceTemplate('<div class="palette-overlay" id="palette"', '</div>\n\n<!-- Keyboard shortcuts')
        .replace(/\n\n<!-- Keyboard shortcuts$/, '');
    await bootDashboardDom({ body: APP_HTML + PALETTE });
    // The islands commit on a later turn than the boot's own reads: wait for the inbox's.
    for (let i = 0; i < 100 && !document.querySelector('#btn-decisions'); i++) await flush();
    for (let i = 0; i < 5; i++) await flush();
});

// --- 2. 🔴 ⌘K, independent of the menu ------------------------------------------------

test('⌘K opens the palette directly — independent of the primary menu', () => {
    assert.equal(document.getElementById('palette').classList.contains('open'), false);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }));
    assert.ok(document.getElementById('palette').classList.contains('open'),
        '⌘K no longer opens the palette: the direct shortcut regressed');
    window.chela.closePalette();
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
