// CMX-169 - WITH NO USABLE STORED PANE, A PHONE'S BOOT OPENS THE TOP SIDEBAR ROW.
//
// The boot's first terminals render runs as soon as /api/agents lands, BEFORE the sidebar
// has drawn (main.js), so it has no sidebar order to follow and opens the first row. Once
// the sidebar draws, nav.js calls terminals.adoptSidebarDefault(), which moves that default
// to the first session in the sidebar's order. One boot per file: term_last_pane.test.mjs
// boots WITH a stored pane, term_last_pane_tap.test.mjs swipes before the sidebar draws.
//
// Run: pnpm exec vitest run tests/term_last_pane_boot.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { clickIsland } from './js_helpers/dashboard_dom.mjs';
import { KEY, bootPhone, pane, settle, stored } from './js_helpers/term_pane_fixture.mjs';

let nav, sidebar;
beforeAll(async () => {
    // A stored pane that is offline: the boot falls through to the sidebar's first.
    ({ nav, sidebar } = await bootPhone({ order: ['/srv/code/gamma', '/srv/code/beta', '/srv/code/alpha'], seed: { [KEY]: stored('@299', 's-z', 'z') } }));
    await settle();
});

test('the boot opens the first session in the sidebar order, not the first /api/agents row', () => {
    assert.deepEqual(sidebar.sidebarOrder(), ['@203', '@202', '@201']);
    assert.deepEqual(pane(), { value: '@203', shown: '@203' });
    assert.equal(JSON.parse(localStorage.getItem(KEY)).wid, '@203');
});

test('after the boot, a sidebar re-render never moves a pane the user picked', async () => {
    clickIsland(document.querySelector('#sidebar-agents .agent-row[data-item="w:@201"]'));
    await settle();
    await sidebar.refreshSidebar();
    await settle();
    assert.deepEqual(pane(), { value: '@201', shown: '@201' });
});
