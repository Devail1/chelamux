// CMX-89: what paints first must match what the modules then decide, or the page flashes one
// state and switches to another (worst on a phone, where the bundle takes longest to load).
//
//   1. the Wall toolbar row (CMX-135): no "+ New shell" in it any more (the New menu is the
//      one place a shell starts), and on the first paint, before anything is counted, neither
//      in-row pill copy (#term-approvals, #term-shares) shows. Since CMX-248 the first paint
//      IS the shell's (<App>, mounted synchronously), so it is booted here with every read held.
//   2. the collapsed desktop sidebar: the sidebar controller restores it, but the bundle runs
//      late; index.html's inline <body> script must restore it first. No module is loaded for
//      this one: it IS the pre-JS paint.
//
// The CSS is the REAL style.css resolved per viewport by cssForViewport (jsdom alone ignores
// every @media rule).
//
// Run: pnpm exec vitest run tests/dashboard_first_paint.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs jsdom).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { APP_BODY, bootDashboardDom, sliceTemplate } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, PHONE, DESKTOP } from './js_helpers/css_viewport.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const DASH = path.join(here, '..', 'chela', 'dashboard');
const CSS = fs.readFileSync(path.join(DASH, 'static', 'style.css'), 'utf8');
// CMX-278: the sidebar's state is the sidebar feature's controller.
const NAV_JS = fs.readFileSync(path.join(DASH, 'frontend', 'src', 'features', 'sidebar', 'controller.ts'), 'utf8');

let doc, sheet;
beforeAll(async () => {
    // Every read held: what paints is what the shell draws before it has counted anything.
    ({ dom: { window: { document: doc } } } = await bootDashboardDom({ body: APP_BODY, fetchImpl: () => new Promise(() => {}) }));
    sheet = doc.head.appendChild(doc.createElement('style'));
});

test('the Wall toolbar renders no "+ New shell" button (CMX-135)', () => {
    // CMX-242: the layout toggle is the walltoolbar island's; its slot marks the real row.
    assert.ok(doc.querySelector('#panel-terminals > .term-toolbar #wall-toolbar-island'), 'the toolbar is not the real row');
    assert.equal(doc.getElementById('term-new-shell'), null, '#term-new-shell is back');
    assert.ok(![...doc.querySelectorAll('.term-toolbar button')].some(b => /new shell/i.test(b.textContent)),
        'a "New shell" button is in the Wall toolbar again');
});

const shows = el => !!el && !el.hidden && doc.defaultView.getComputedStyle(el).display !== 'none';

for (const [name, vp] of [['@390', PHONE], ['@1440', DESKTOP]]) {
    test(`${name} on the first paint: neither in-row pill shows`, () => {
        sheet.textContent = cssForViewport(CSS, vp);
        // CMX-219: #term-approvals is the approvals pill's, drawn only while one waits
        assert.ok(doc.getElementById('term-approvals-island'), '#term-approvals-island is missing');
        assert.equal(shows(doc.getElementById('term-approvals')), false, `#term-approvals shows before anything was counted at ${name}`);
        // CMX-240: #term-shares is the share feature's pill; its slot draws no box of its own
        const slot = doc.getElementById('shares-pill-row');
        assert.ok(slot, "#term-shares's slot is missing");
        assert.equal(shows(doc.getElementById('term-shares')), false, `#term-shares shows before anything was counted at ${name}`);
        assert.equal(doc.defaultView.getComputedStyle(slot).display, 'contents', `the shares pill slot draws a box at ${name}`);
    });
}

// The inline restore, run for real: the template from <body> up to the root, executed as a
// browser would (inline scripts only, no modules).
const BODY_HEAD = sliceTemplate('<body>', '<div id="root"></div>');

function bodyClassesAfterInlineScripts(stored) {
    const dom = new JSDOM(`<!DOCTYPE html><html><head></head>${BODY_HEAD}</body></html>`, {
        runScripts: 'dangerously', url: 'http://localhost/',
        beforeParse(win) { if (stored !== null) win.localStorage.setItem(KEY, stored); },
    });
    return [...dom.window.document.body.classList];
}

const KEY = (NAV_JS.match(/const SIDEBAR_COLLAPSED_KEY = '([^']+)'/) || [])[1];

test('a collapsed sidebar is restored before first paint, with the controller\'s own key', () => {
    assert.ok(KEY, "the controller's SIDEBAR_COLLAPSED_KEY not found");
    assert.ok(bodyClassesAfterInlineScripts('1').includes('sidebar-collapsed'),
        'the persisted collapsed sidebar is only restored by the controller — it paints expanded, then folds');
    assert.ok(!bodyClassesAfterInlineScripts(null).includes('sidebar-collapsed'));
    assert.ok(!bodyClassesAfterInlineScripts('0').includes('sidebar-collapsed'));
});
