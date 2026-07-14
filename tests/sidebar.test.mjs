// THE SIDEBAR: two sections, zero filter chips, collapsible.
//
// Structural guards over the sources — each one locks a property that is easy to
// re-break by accident:
//
//   1. THE TYPE IS A CUE, NOT A FILTER. The 4-chip All/Claude/Shell/Server row is
//      gone: a live fleet is a handful of windows that always fit the viewport, so
//      the chips filtered a list you could already see.
//   2. THE CUE IS NEVER HUE-ALONE. Three coloured dots would encode the window type
//      in colour only — unreadable for a red-weak viewer and invisible in greyscale.
//      A glyph (C / $ / ⚙) carries it; the Okabe-Ito tint only reinforces.
//   3. COLLAPSING MUST NOT RELOAD THE FLEET. buildWall does `innerHTML =` whenever
//      its cache key (_termSig) changes, so if sidebar state ever leaked into that
//      key, collapsing the sidebar would reload EVERY live terminal (the CMX-67
//      trap). The rail is a body class + CSS; nothing in it re-renders a pane.
//   4. ONE CONTROL, TWO BEHAVIOURS. #btn-menu / toggleSidebar drives both the phone
//      drawer and the desktop rail — there is no second toggle — and the collapsed
//      state survives a reload (localStorage).
//
// Run: node --test tests/  (or `uv run pytest -q` — tests/test_js_suites.py runs every .test.mjs)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const src = p => readFileSync(join(ROOT, p), 'utf8');

const NAV = src('static/js/nav.js');
const LAUNCHER = src('static/js/launcher.js');
const HTML = src('templates/index.html');
const CSS = src('static/style.css');

test('the type filter is gone — markup, handler and state', () => {
    assert.ok(!HTML.includes('agent-filter'), 'the filter chip row is still in index.html');
    assert.ok(!NAV.includes('setAgentFilter'), 'setAgentFilter still exists');
    assert.ok(!NAV.includes('_agentFilter'), 'the filter state still exists');
    assert.ok(!CSS.includes('.agent-filter'), 'the filter chips still have styling');
});

test('the window type survives as a per-row cue — a glyph first, colour only reinforcing', () => {
    // The glyph is the primary channel: readable in greyscale.
    assert.match(NAV, /_TYPE_GLYPH\s*=\s*{[^}]*claude:\s*'C'[^}]*shell:\s*'\$'/);
    assert.ok(NAV.includes('ar-type'), 'the row no longer carries a type cue');
    // Colour is the secondary channel, and it is colourblind-safe (Okabe-Ito), so
    // the three types never come down to a red/green pairing.
    ['#56B4E9', '#009E73', '#E69F00'].forEach(c =>
        assert.ok(CSS.includes(c), `the type cue dropped the Okabe-Ito colour ${c}`));
});

test('the sidebar is two sections — Launch folded into the launch menu', () => {
    assert.ok(!HTML.includes('launcher-section'), 'the Launch sidebar section is still there');
    assert.ok(HTML.includes('new-menu-launch'), 'the launch menu has no Favorites/Recent host');
    // Every launch behaviour moved WITH the rows: click-to-launch, pin, unpin,
    // forget-a-recent, add-a-favourite.
    ['launchProject', 'pinFav', 'unpinFav', 'forgetRecent', 'openFavAdd'].forEach(fn =>
        assert.ok(LAUNCHER.includes(fn), `${fn} was lost in the move`));
    assert.ok(LAUNCHER.includes("getElementById('new-menu-launch')"),
        'the launcher does not render into the launch menu');
});

test('one sidebar control drives both breakpoints, and the desktop state persists', () => {
    assert.ok(HTML.includes('chela.toggleSidebar()'), '#btn-menu no longer calls toggleSidebar');
    assert.equal(HTML.match(/toggleSidebar\(\)/g).length, 1, 'a second sidebar toggle appeared');
    assert.ok(NAV.includes("localStorage.setItem(SIDEBAR_COLLAPSED_KEY"),
        'the collapsed state is not persisted — it would forget itself on reload');
    assert.ok(CSS.includes('body.sidebar-collapsed .app'), 'the collapsed rail has no layout rule');
    // Navigating closes the phone drawer; it must NEVER collapse the desktop rail.
    assert.match(NAV, /function closeSidebar\(\)\s*{\s*if \(_isPhoneWidth\(\)\)/);
});

test('collapsing cannot reload the fleet — sidebar state never reaches the wall cache key', () => {
    const TERMINALS = src('static/js/terminals.js');
    // _termSig is built from the live wid set, not from any sidebar/layout state.
    assert.ok(!TERMINALS.includes('sidebar-collapsed'), 'terminals.js reads sidebar state');
    // Calls, not mentions — the comments here talk ABOUT the cache on purpose.
    const code = NAV.replace(/\/\/.*$/gm, '');
    assert.ok(!/_termSig\s*=/.test(code), 'nav.js pokes the wall render cache');
    assert.ok(!/\b(buildWall|renderTerminals)\s*\(/.test(code), 'nav.js re-renders the wall on a sidebar toggle');
    // It re-fits (a resize event the wall already debounces), it does not re-render.
    assert.ok(NAV.includes("new Event('resize')"), 'the wall is never told the canvas resized');
});
