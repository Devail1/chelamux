// CMX-386 → CMX-313 — the phone's New session. CMX-386 added a phone-only "+"
// beside the floating ☰; CMX-313 (Liav, 10-23: the Claude app's look) removed it
// again: New session is the drawer's own button, and with a pane open the ☰
// moved into the pane's title bar (tests/browser/phone_pane_titlebar.test.mjs).
//
// What stays load-bearing is WHERE the floating ☰ lives: the phone sidebar is an
// off-canvas drawer slid away with `transform`, and a transformed ancestor
// becomes the containing block for every position:fixed descendant — so a fab
// inside the sidebar would be trapped off-screen with it (the trap #529
// documented for .safety-float). These guards boot the REAL index.html shell
// and the REAL module graph, and resolve styles through cssForViewport at a
// 390px phone (jsdom alone ignores every @media rule).
//
// Run: pnpm exec vitest run tests/mobile_new_fab.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { test, beforeAll } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { APP_BODY, bootDashboardDom, clickIsland, flush } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, PHONE } from './js_helpers/css_viewport.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const CSS = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

let win, doc, sheet;
const phone = true;
beforeAll(async () => {
    // The REAL page (the shell): the sidebar (with its New session button), both fabs, the
    // scrim and the launch menu.
    const { dom } = await bootDashboardDom({ body: APP_BODY, phone: () => phone });
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
    if (doc.querySelector('.sidebar').classList.contains('open')) clickIsland(doc.getElementById('sidebar-scrim'));
    await flush();
}

test('@390 CMX-313: there is no phone "+": New session is the drawer\'s own button', () => {
    mount(PHONE);
    assert.equal(doc.getElementById('btn-new-mobile'), null, 'the phone "+" is back on the page');
    assert.equal(doc.querySelector('.mobile-new-fab'), null, 'the phone "+" is back on the page');
    assert.ok(doc.querySelector('.sidebar .sidebar-new-btn'), 'the drawer lost its New session button');
});

test('@390: the floating ☰ is outside the sidebar and NOT inside any transformed ancestor', () => {
    mount(PHONE);
    const fab = doc.getElementById('btn-menu-mobile');
    assert.ok(fab, '#btn-menu-mobile is missing from the page');
    assert.equal(fab.closest('.sidebar'), null,
        'the ☰ lives inside .sidebar — on a phone the closed drawer\'s transform traps it off-screen');
    assert.equal(transformedAncestor(fab), null,
        'the ☰ sits inside a transformed ancestor, which becomes its containing block — ' +
        'its position:fixed would place it (and clip it) against that box, not the screen');
    assert.equal(win.getComputedStyle(fab).position, 'fixed');
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

test('@390: the drawer path still opens the SAME launch menu (accepted case)', async () => {
    sheet.textContent = '';
    await reset();
    clickIsland(doc.getElementById('btn-menu-mobile'));   // the ☰ opens the closed drawer
    clickIsland(doc.querySelector('.sidebar .sidebar-new-btn'));
    await flush();
    assert.ok(menu()?.querySelector('#new-sandbox-item'), 'the drawer\'s New session no longer opens the launch menu');
    await reset();
});
