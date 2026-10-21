// CMX-386 — a phone's New session was two taps deep (☰, then "New session"
// inside the drawer) once #529 removed the topbar. #btn-new-mobile is the
// phone-only "+" beside the ☰ fab that opens the SAME #new-menu with the
// drawer left CLOSED.
//
// The load-bearing property is WHERE the button lives: the phone sidebar is an
// off-canvas drawer slid away with `transform`, and a transformed ancestor
// becomes the containing block for every position:fixed descendant — so a "+"
// inside the sidebar would be trapped off-screen with it (the trap #529
// documented for .safety-float). These guards boot the REAL index.html shell
// and the REAL module graph, and resolve styles through cssForViewport at a
// 390px phone and a 1440px desktop (jsdom alone ignores every @media rule).
//
// jsdom does no layout, so the pixel check ("the + is visible and the menu is on-screen at
// 390px") belongs to the real-browser (Playwright) suite. Since CMX-219 the "+" and the menu
// it opens are the launcher island's (frontend/src/features/launcher): the menu is a kit
// popover portaled to <body>, placed by Radix inside the viewport, so the hand-rolled
// placement arithmetic this file used to feed is gone with it.
//
// Run: pnpm exec vitest run tests/mobile_new_fab.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { test, beforeAll } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootDashboardDom, clickIsland, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, PHONE, DESKTOP } from './js_helpers/css_viewport.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const CSS = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

let win, doc, sheet;
let phone = true;
beforeAll(async () => {
    // The REAL markup from the app shell through the overlays island: the sidebar (with its
    // New session button), both fabs, the scrim and the launch menu's island.
    const body = sliceTemplate('<div class="app">', '<div id="overlays-island"></div>');
    const { dom } = await bootDashboardDom({ body, phone: () => phone });
    win = dom.window;
    doc = win.document;
    sheet = doc.createElement('style');
    doc.head.appendChild(sheet);
});

function mount(vp) { sheet.textContent = cssForViewport(CSS, vp); }

// The nearest ancestor whose resolved `transform` is not `none` — the element a
// position:fixed descendant would be positioned (and clipped) against.
function transformedAncestor(el) {
    for (let n = el.parentElement; n && n.nodeType === 1; n = n.parentElement) {
        const t = win.getComputedStyle(n).transform;
        if (t && t !== 'none') return n;
    }
    return null;
}

const menu = () => doc.querySelector('.popover.launch-menu');
async function reset() {
    const { closeLaunchMenu } = await import('../chela/dashboard/frontend/src/features/launcher/index.ts');
    closeLaunchMenu();
    win.chela.closeSidebar();
    await flush();
}

test('@390: the "+" is outside the sidebar and NOT inside any transformed ancestor', () => {
    mount(PHONE);
    const plus = doc.getElementById('btn-new-mobile');
    assert.ok(plus, '#btn-new-mobile is missing from index.html');
    assert.equal(plus.getAttribute('aria-label'), 'New session');
    assert.equal(plus.closest('.sidebar'), null,
        'the "+" lives inside .sidebar — on a phone the closed drawer\'s transform traps it off-screen');
    assert.equal(transformedAncestor(plus), null,
        'the "+" sits inside a transformed ancestor, which becomes its containing block — ' +
        'its position:fixed would place it (and clip it) against that box, not the screen');
    assert.equal(win.getComputedStyle(plus).position, 'fixed');
});

test('@390 negative control: the detector DOES see the drawer\'s own New session button as trapped', () => {
    // Proves transformedAncestor() is not vacuous: at phone width the closed
    // drawer is transformed, so the in-sidebar button resolves to it.
    mount(PHONE);
    const drawerBtn = doc.querySelector('.sidebar .sidebar-new-btn');
    assert.ok(drawerBtn, '.sidebar-new-btn is missing from the sidebar');
    assert.equal(transformedAncestor(drawerBtn), doc.querySelector('.sidebar'),
        'the phone sidebar no longer resolves a transform — this file\'s trap detector proves nothing');
});

test('@390: the "+" is displayed while the drawer is closed, and hides with the ☰ while it is open', async () => {
    mount(PHONE);
    await reset();
    const plus = doc.getElementById('btn-new-mobile');
    assert.notEqual(win.getComputedStyle(plus).display, 'none',
        'the "+" is not displayed on a phone — New session is back to two taps deep');
    win.chela.toggleSidebar(true);
    assert.equal(win.getComputedStyle(plus).display, 'none',
        'the "+" still shows over the open drawer');
    await reset();
});

test('@390: tapping the "+" opens the launch menu with the drawer left CLOSED, portaled to <body>', async () => {
    // No stylesheet for this one: the popper re-measures every ancestor on open, and jsdom
    // re-runs the whole cascade per getComputedStyle (minutes with style.css mounted).
    sheet.textContent = '';
    await reset();
    clickIsland(doc.getElementById('btn-new-mobile'));
    await flush();
    assert.ok(menu(), 'the "+" did not open the launch menu');
    assert.ok(menu().querySelector('#new-sandbox-item'), 'the "+" opened some other menu');
    // a direct child of <body> (Radix's popper wrapper): never inside the sliding sidebar
    assert.equal(menu().closest('[data-radix-popper-content-wrapper]')?.parentElement, doc.body,
        'the launch menu is not portaled to <body>: a transformed ancestor could trap it');
    assert.equal(doc.body.classList.contains('sidebar-open'), false,
        'the "+" opened the drawer: the menu must appear with the drawer CLOSED');
    assert.equal(doc.querySelector('.sidebar').classList.contains('open'), false);
    await reset();
});

test('@390: the drawer path still opens the SAME launch menu (accepted case)', async () => {
    sheet.textContent = '';
    await reset();
    win.chela.toggleSidebar(true);
    clickIsland(doc.querySelector('.sidebar .sidebar-new-btn'));
    await flush();
    assert.ok(menu()?.querySelector('#new-sandbox-item'), 'the drawer\'s New session no longer opens the launch menu');
    await reset();
});

test('@1440: the "+" is not displayed on desktop', () => {
    phone = false;
    mount(DESKTOP);
    assert.equal(win.getComputedStyle(doc.getElementById('btn-new-mobile')).display, 'none',
        'the phone-only "+" shows on desktop');
    phone = true;
});
