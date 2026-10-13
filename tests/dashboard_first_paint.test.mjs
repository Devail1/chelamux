// CMX-89 — what paints BEFORE any module runs must match what the modules then
// decide, or the page flashes one state and switches to another (worst on a
// phone, where the module graph takes longest to load).
//
//   1. #term-new-shell — renderTerminals() hides it on a phone (forced single
//      mode; the "+" FAB starts sessions there), but only once terminals.js has
//      run. style.css must hide it at phone width on its own; on desktop it is
//      the Wall's own control and must still paint.
//   2. the collapsed desktop sidebar — nav.js restores it, but as a module it
//      runs late; index.html's inline <body> script must restore it first.
//
// The markup is the REAL index.html (sliced + rendered for terminals on), the
// CSS the REAL style.css resolved per viewport by cssForViewport (jsdom alone
// ignores every @media rule). No module is loaded: this IS the pre-JS paint.
//
// Run: node --test tests/dashboard_first_paint.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs jsdom).
import { test } from 'node:test';
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
const NAV_JS = fs.readFileSync(path.join(DASH, 'static', 'js', 'nav.js'), 'utf8');

const TOOLBAR = sliceTemplate('<div class="term-toolbar">', '+ New shell</button>',
    { terminals_enabled: true, wall_tile_dispatched: false });

function newShellDisplay(vp) {
    const dom = new JSDOM(`<!DOCTYPE html><html><head><style>${cssForViewport(CSS, vp)}</style></head>
        <body><main class="canvas"><div class="panel active" id="panel-terminals">${TOOLBAR}</div></div></main></body></html>`);
    const btn = dom.window.document.getElementById('term-new-shell');
    assert.ok(btn, '#term-new-shell is missing from index.html');
    return dom.window.getComputedStyle(btn).display;
}

test('@390 before any JS: "+ New shell" does not paint', () => {
    assert.equal(newShellDisplay(PHONE), 'none',
        '#term-new-shell paints on a phone until terminals.js hides it — the first-load flash');
});

test('@1440 before any JS: "+ New shell" paints (the Wall is the default)', () => {
    assert.notEqual(newShellDisplay(DESKTOP), 'none');
});

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
    assert.ok(KEY, "nav.js's SIDEBAR_COLLAPSED_KEY not found");
    assert.ok(bodyClassesAfterInlineScripts('1').includes('sidebar-collapsed'),
        'the persisted collapsed sidebar is only restored by nav.js — it paints expanded, then folds');
    assert.ok(!bodyClassesAfterInlineScripts(null).includes('sidebar-collapsed'));
    assert.ok(!bodyClassesAfterInlineScripts('0').includes('sidebar-collapsed'));
});
