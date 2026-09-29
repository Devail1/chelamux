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
import { bootWall } from './wall_grid_toolbar_env.mjs';   // needs `pnpm install` for jsdom
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

const $ = s => document.querySelector(s);
const toggle = () => $('#term-grid-toggle');
const row = () => $('#term-grid-row');
// Shown = not inside a [hidden] subtree and not display:none inline — what the
// user can actually reach. The toolbar itself (#term-wall-grid) is the scope.
const shown = el => !el.closest('[hidden]') && !el.closest('[style*="display: none"], [style*="display:none"]');
const shownControls = () => [...$('#term-wall-grid').querySelectorAll('button')].filter(shown);
const storedPreset = () => JSON.parse(localStorage.getItem('pc_wall_preset'));

before(async () => {
    // Liav's default: Focus ON, and NO stored expand/collapse choice.
    await bootWall({ pc_wall_focus: '1', pc_wall_grid_collapsed: null });
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

    test('choosing Auto-arrange collapses the row, and the control shows the AUTO glyph', () => {
        window.chela.toggleGridRow();
        assert.ok(!row().hidden);
        window.chela.toggleWallAuto($('#term-auto-btn'));   // on
        assert.ok(row().hidden, 'choosing Auto-arrange collapses the row');
        assert.equal(toggle().getAttribute('aria-expanded'), 'false');
        const auto = $('#term-auto-btn').querySelector('svg').outerHTML;
        assert.equal(toggle().querySelector('svg').outerHTML, auto,
            'Auto-arrange owns the layout, so the collapsed control shows its glyph');
        // Not vacuous: the preset glyph it would otherwise fall back to differs.
        const preset = document.querySelector('#term-grid-presets .gl-btn.active svg');
        assert.notEqual(preset.outerHTML, auto);
        window.chela.toggleWallAuto($('#term-auto-btn'));   // off again
        assert.equal(toggle().querySelector('svg').outerHTML, preset.outerHTML);
    });

    test('Esc hands keyboard focus back to the toggle, not a now-hidden preset', () => {
        window.chela.toggleGridRow();
        const preset = document.querySelector('#term-grid-presets .gl-btn');
        preset.focus();
        assert.equal(document.activeElement, preset, 'focus starts inside the expanded row');
        document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        assert.ok(row().hidden);
        assert.equal(document.activeElement, toggle(), 'Esc returns focus to the toggle');
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
