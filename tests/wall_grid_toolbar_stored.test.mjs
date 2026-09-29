// CMX-397 — the stored expand/collapse choice is honoured when the page LOADS.
//
// terminals.js reads pc_wall_grid_collapsed once, the first time it builds the
// toolbar. tests/wall_grid_toolbar.test.mjs boots with NO stored choice, where
// ignoring the stored value and using the Focus default give the same answer —
// so this suite is its own process, booted with a choice that CONTRADICTS the
// default: Focus ON (default: collapsed) but the user last expanded it ('0').
//
// Run: node --test tests/wall_grid_toolbar_stored.test.mjs (needs `pnpm install`).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootWall } from './wall_grid_toolbar_env.mjs';

before(async () => {
    await bootWall({ pc_wall_focus: '1', pc_wall_grid_collapsed: '0' });
});

test('Focus ON but a stored "expanded" choice: the toolbar loads EXPANDED', () => {
    const toggle = document.querySelector('#term-grid-toggle');
    assert.equal(toggle.getAttribute('aria-expanded'), 'true',
        'the remembered expand beats the Focus-on collapsed default');
    assert.ok(!document.querySelector('#term-grid-row').hidden, 'the preset row is visible');
    assert.equal(localStorage.getItem('pc_wall_grid_collapsed'), '0', 'loading did not rewrite it');
});
