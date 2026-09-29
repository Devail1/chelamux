// CMX-397 — the Wall's layout toolbar COLLAPSES to one control, in a REAL DOM.
//
// Liav keeps Focus ON as his default view, so the "Grid:" row of six presets +
// lock/auto/focus was mostly noise. It is not removed: it folds into ONE button
// showing the CURRENT layout/mode glyph and a chevron, and expands inline on a
// click. This runs the REAL terminals.js (like tests/walldock.test.mjs) against
// the terminals panel cut from the REAL templates/index.html, so the ids and the
// inline onclick wiring asserted on are the ones a browser gets.
//
// Run: node --test tests/wall_grid_toolbar.test.mjs (pytest runs it via
// tests/test_js_suites.py; it needs `pnpm install` for jsdom).
import { before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { renderShell } from './browser/fixture.mjs';
import { gridRowCollapsed } from '../chela/dashboard/static/js/wallmodel.js';

describe('gridRowCollapsed: the toolbar\'s resting state', () => {
    test('no stored choice: collapsed exactly when Focus is on', () => {
        assert.equal(gridRowCollapsed(null, true), true);
        assert.equal(gridRowCollapsed(null, false), false);
    });
    test('an explicit stored choice wins over the Focus default', () => {
        assert.equal(gridRowCollapsed('0', true), false);
        assert.equal(gridRowCollapsed('1', false), true);
    });
});

// The terminals panel exactly as the real template ships it.
const PANEL = new JSDOM(renderShell()).window.document.getElementById('panel-terminals').outerHTML;

const AGENTS = ['@1', '@2', '@3'].map((wid, i) => ({
    name: `a${i}`, window_id: wid, online: true, session_status: 'idle', claude_running: true,
}));

function fakeFetch(url) {
    const path = String(url);
    const body = path.endsWith('/api/agents') ? AGENTS
        : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
            : path.startsWith('/api/term/ready') ? { ready: true } : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {},
        removeWidget(el, removeDOM) { if (removeDOM !== false) el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let dom;
const $ = s => document.querySelector(s);
const toggle = () => $('#term-grid-toggle');
const row = () => $('#term-grid-row');
// Shown = not inside a [hidden] subtree and not display:none inline — what the
// user can actually reach. The toolbar itself (#term-wall-grid) is the scope.
const shown = el => !el.closest('[hidden]') && !el.closest('[style*="display: none"], [style*="display:none"]');
const shownControls = () => [...$('#term-wall-grid').querySelectorAll('button')].filter(shown);
const storedPreset = () => JSON.parse(localStorage.getItem('pc_wall_preset'));

before(async () => {
    dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        // runScripts so the template's INLINE onclick attributes really fire — a
        // preset click must reach chela.applyGridLayout the way a browser's does.
        // The panel carries no <script>, so nothing else is evaluated.
        { url: 'http://localhost:5005/', pretendToBeVisual: true, runScripts: 'dangerously' });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    // Liav's default: Focus ON, and NO stored expand/collapse choice.
    localStorage.setItem('pc_term_mode', 'wall');
    localStorage.setItem('pc_wall_focus', '1');
    localStorage.removeItem('pc_wall_grid_collapsed');
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    const terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

describe('the collapsible Wall layout toolbar', () => {
    test('Focus ON + no stored choice: it starts COLLAPSED to one control + a chevron', () => {
        assert.equal(toggle().tagName, 'BUTTON', 'the collapsed control is a real <button>');
        assert.equal(toggle().getAttribute('aria-expanded'), 'false');
        assert.ok(row().hidden, 'the preset row is hidden while collapsed');
        const controls = shownControls();
        assert.deepEqual(controls.map(b => b.id), ['term-grid-toggle'],
            'collapsed, exactly ONE layout control is reachable');
        // It shows the CURRENT mode (Focus) — the same glyph the Focus button draws.
        assert.ok(toggle().querySelector('svg'), 'the control carries the current layout glyph');
        assert.equal(toggle().querySelector('svg').outerHTML,
            $('#term-focus-btn').querySelector('svg').outerHTML,
            'Focus is on, so the collapsed control shows the Focus glyph');
        assert.ok(toggle().querySelector('.grid-chevron svg'), 'plus a chevron');
    });

    test('expanding shows every preset, lock, auto and Focus', () => {
        window.chela.toggleGridRow();
        assert.equal(toggle().getAttribute('aria-expanded'), 'true');
        assert.ok(!row().hidden);
        const presets = [...document.querySelectorAll('#term-grid-presets .gl-btn')].filter(shown);
        assert.equal(presets.length, 6, 'all six presets are reachable once expanded');
        for (const id of ['term-lock-btn', 'term-auto-btn', 'term-focus-btn']) {
            assert.ok(shown($('#' + id)), `${id} is reachable once expanded`);
        }
        assert.equal(localStorage.getItem('pc_wall_grid_collapsed'), '0',
            'an explicit expand is remembered');
    });

    test('choosing a preset APPLIES it (its real handler) and collapses the row', () => {
        const btn = document.querySelector('#term-grid-presets .gl-btn[data-preset="5"]');
        btn.click();   // the inline onclick → chela.applyGridLayout(3, 2, this)
        assert.deepEqual(storedPreset(), { cols: 3, rows: 2 }, 'the preset handler ran');
        assert.ok(btn.classList.contains('active'), 'and marked that preset active');
        assert.ok(row().hidden, 'choosing a preset collapses the row');
        assert.equal(toggle().getAttribute('aria-expanded'), 'false');
        assert.equal(localStorage.getItem('pc_wall_grid_collapsed'), '0',
            'a preset choice is a transient collapse — the stored choice is untouched');
    });

    test('every preset still works from the expanded row', () => {
        for (const btn of document.querySelectorAll('#term-grid-presets .gl-btn')) {
            window.chela.toggleGridRow();
            assert.ok(!row().hidden);
            btn.click();
            const [, cols, rows] = btn.getAttribute('onclick').match(/applyGridLayout\((\d+), (\d+)/);
            assert.deepEqual(storedPreset(), { cols: +cols, rows: +rows }, `${btn.title} applied`);
            assert.ok(row().hidden, `${btn.title} collapsed the row`);
        }
    });

    test('Focus off: the collapsed control shows the active PRESET\'s glyph', () => {
        window.chela.toggleGridRow();
        window.chela.toggleWallFocus($('#term-focus-btn'));   // off
        assert.ok(row().hidden, 'choosing a mode collapses the row too');
        const active = document.querySelector('#term-grid-presets .gl-btn.active svg');
        assert.equal(toggle().querySelector('svg').outerHTML, active.outerHTML);
    });

    test('Esc collapses an expanded row', () => {
        window.chela.toggleGridRow();
        assert.ok(!row().hidden);
        document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        assert.ok(row().hidden, 'Esc collapses');
        assert.equal(toggle().getAttribute('aria-expanded'), 'false');
    });

    test('a click outside collapses it; a click inside the toolbar does not', () => {
        window.chela.toggleGridRow();
        $('#term-grid-presets').dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
        assert.ok(!row().hidden, 'a pointerdown inside the toolbar keeps it open');
        $('#term-stage').dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
        assert.ok(row().hidden, 'a pointerdown outside collapses it');
    });

    test('an explicit collapse is remembered', () => {
        window.chela.toggleGridRow();   // open
        window.chela.toggleGridRow();   // explicit close
        assert.equal(localStorage.getItem('pc_wall_grid_collapsed'), '1');
    });
});
