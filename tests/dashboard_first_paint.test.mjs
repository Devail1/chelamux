// CMX-89 — what paints BEFORE any module runs must match what the modules then
// decide, or the page flashes one state and switches to another (worst on a
// phone, where the module graph takes longest to load).
//
//   1. the Wall toolbar row (CMX-135): no "+ New shell" in it any more — the
//      New menu is the one place a shell starts — and its in-row pill copies
//      ship empty: #term-approvals and #term-shares as empty island slots
//      (CMX-240), so neither paints before the modules have counted anything.
//   2. the collapsed desktop sidebar — nav.js restores it, but as a module it
//      runs late; index.html's inline <body> script must restore it first.
//
// The markup is the REAL index.html (sliced + rendered for terminals on), the
// CSS the REAL style.css resolved per viewport by cssForViewport (jsdom alone
// ignores every @media rule). No module is loaded: this IS the pre-JS paint.
//
// Run: pnpm exec vitest run tests/dashboard_first_paint.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs jsdom).
import { test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { sliceTemplate } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, PHONE, DESKTOP } from './js_helpers/css_viewport.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const DASH = path.join(here, '..', 'chela', 'dashboard');
const CSS = fs.readFileSync(path.join(DASH, 'static', 'style.css'), 'utf8');
// CMX-278: the sidebar's state left nav.js for the sidebar feature's controller.
const NAV_JS = fs.readFileSync(path.join(DASH, 'frontend', 'src', 'features', 'sidebar', 'controller.ts'), 'utf8');

const TOOLBAR = sliceTemplate('<div class="term-toolbar">', '<div id="term-stage"',
    { terminals_enabled: true, wall_tile_dispatched: false });

function toolbarDom(vp) {
    return new JSDOM(`<!DOCTYPE html><html><head><style>${cssForViewport(CSS, vp)}</style></head>
        <body><main class="canvas"><div class="panel active" id="panel-terminals">${TOOLBAR}</div></div></main></body></html>`);
}

test('the Wall toolbar renders no "+ New shell" button (CMX-135)', () => {
    const doc = toolbarDom(DESKTOP).window.document;
    // CMX-242: the layout toggle is the walltoolbar island's; its slot marks the real row.
    assert.ok(doc.querySelector('.term-toolbar #wall-toolbar-island'), 'the toolbar slice is not the real row');
    assert.equal(doc.getElementById('term-new-shell'), null, '#term-new-shell is back in index.html');
    assert.ok(![...doc.querySelectorAll('.term-toolbar button')].some(b => /new shell/i.test(b.textContent)),
        'a "New shell" button is in the Wall toolbar again');
});

for (const [name, vp] of [['@390', PHONE], ['@1440', DESKTOP]]) {
    test(`${name} before any JS: neither in-row pill paints`, () => {
        const dom = toolbarDom(vp);
        // CMX-219: #term-approvals is the approvals island's: before JS its slot draws nothing
        const approvals = dom.window.document.getElementById('term-approvals-island');
        assert.ok(approvals, '#term-approvals-island is missing from index.html');
        assert.equal(approvals.childNodes.length, 0, `#term-approvals-island has content before JS at ${name}`);
        // CMX-240: #term-shares is the share feature's pill island; before JS its slot is
        // empty and draws no box of its own.
        const slot = dom.window.document.getElementById('shares-pill-row');
        assert.ok(slot, "#term-shares's island slot is missing from index.html");
        assert.equal(slot.children.length, 0, `the shares pill slot ships content at ${name}`);
        assert.equal(dom.window.getComputedStyle(slot).display, 'contents', `the shares pill slot draws a box at ${name}`);
    });
}

// The inline restore, run for real: the template from <body> up to the app
// shell, executed as a browser would (inline scripts only — no modules).
const BODY_HEAD = sliceTemplate('<body>', '<div class="app">');

function bodyClassesAfterInlineScripts(stored) {
    const dom = new JSDOM(`<!DOCTYPE html><html><head></head>${BODY_HEAD}</div></body></html>`, {
        runScripts: 'dangerously', url: 'http://localhost/',
        beforeParse(win) { if (stored !== null) win.localStorage.setItem(KEY, stored); },
    });
    return [...dom.window.document.body.classList];
}

const KEY = (NAV_JS.match(/const SIDEBAR_COLLAPSED_KEY = '([^']+)'/) || [])[1];

test('a collapsed sidebar is restored before first paint, with nav.js\'s own key', () => {
    assert.ok(KEY, "the controller's SIDEBAR_COLLAPSED_KEY not found");
    assert.ok(bodyClassesAfterInlineScripts('1').includes('sidebar-collapsed'),
        'the persisted collapsed sidebar is only restored by the controller — it paints expanded, then folds');
    assert.ok(!bodyClassesAfterInlineScripts(null).includes('sidebar-collapsed'));
    assert.ok(!bodyClassesAfterInlineScripts('0').includes('sidebar-collapsed'));
});
