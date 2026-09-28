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
// jsdom does no layout, so the pixel check ("the + is visible and the menu is
// on-screen at 390px") belongs to the real-browser (Playwright) suite; here the
// menu placement is checked off openNewMenu's arithmetic with the fab's rect.
//
// Run: node --test tests/mobile_new_fab.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom).
import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootDashboardDom, sliceTemplate } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, PHONE, DESKTOP } from './js_helpers/css_viewport.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const CSS = fs.readFileSync(path.join(here, '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

let win, doc, sheet;
let phone = true;
before(async () => {
    // The REAL markup from the app shell through the end of #new-menu: the
    // sidebar (with its New session button), both fabs, the scrim and the menu.
    const body = sliceTemplate('<div class="app">',
        'onclick="chela.hideNewMenu(); chela.newShellWindow()">Shell window</div>\n</div>');
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

// Run an element's REAL onclick attribute with a click-shaped event (the same
// attribute -> window.chela hop a browser takes; jsdom runs no inline handlers).
function click(el) {
    const code = el.getAttribute('onclick');
    assert.ok(code, `${el.id || el.className} has no onclick attribute`);
    const ev = { stopPropagation() {}, currentTarget: el, target: el };
    return new Function('chela', 'event', code).call(el, win.chela, ev);
}

function reset() {
    win.chela.hideNewMenu();
    win.chela.closeSidebar();
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

test('@390: the "+" is displayed while the drawer is closed, and hides with the ☰ while it is open', () => {
    mount(PHONE);
    reset();
    const plus = doc.getElementById('btn-new-mobile');
    assert.notEqual(win.getComputedStyle(plus).display, 'none',
        'the "+" is not displayed on a phone — New session is back to two taps deep');
    win.chela.toggleSidebar(true);
    assert.equal(win.getComputedStyle(plus).display, 'none',
        'the "+" still shows over the open drawer');
    reset();
});

test('@390: tapping the "+" opens #new-menu with the drawer left CLOSED, on-screen', () => {
    mount(PHONE);
    reset();
    const plus = doc.getElementById('btn-new-mobile');
    const menu = doc.getElementById('new-menu');
    assert.equal(menu.closest('.sidebar'), null, '#new-menu moved into the sidebar');
    assert.equal(transformedAncestor(menu), null, '#new-menu sits inside a transformed ancestor');
    // The fab's box at 390px: top 14, left 14 + 50 (style.css), 42px square.
    plus.getBoundingClientRect = () => ({ top: 14, bottom: 56, left: 64, right: 106, width: 42, height: 42 });
    Object.defineProperty(menu, 'offsetWidth', { value: 232, configurable: true });   // .launch-menu min-width

    click(plus);

    assert.equal(menu.style.display, 'block', 'the "+" did not open #new-menu');
    assert.equal(doc.body.classList.contains('sidebar-open'), false,
        'the "+" opened the drawer — the menu must appear with the drawer CLOSED');
    assert.equal(doc.querySelector('.sidebar').classList.contains('open'), false);
    const left = parseFloat(menu.style.left), top = parseFloat(menu.style.top);
    assert.ok(left >= 0 && left + 232 <= PHONE.width, `menu x ${left}..${left + 232} is off a ${PHONE.width}px screen`);
    assert.ok(top > 56 && top < PHONE.height, `menu top ${top} is not just under the fab`);
    reset();
});

test('@390: the drawer path still opens the SAME #new-menu (accepted case)', () => {
    mount(PHONE);
    reset();
    win.chela.toggleSidebar(true);
    const drawerBtn = doc.querySelector('.sidebar .sidebar-new-btn');
    const menu = doc.getElementById('new-menu');
    drawerBtn.getBoundingClientRect = () => ({ top: 60, bottom: 92, left: 14, right: 250, width: 236, height: 32 });
    click(drawerBtn);
    assert.equal(menu.style.display, 'block', 'the drawer\'s New session no longer opens #new-menu');
    reset();
});

test('@1440: the "+" is not displayed on desktop', () => {
    phone = false;
    mount(DESKTOP);
    assert.equal(win.getComputedStyle(doc.getElementById('btn-new-mobile')).display, 'none',
        'the phone-only "+" shows on desktop');
    phone = true;
});
