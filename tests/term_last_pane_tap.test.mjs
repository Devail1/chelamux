// CMX-169 - A PANE PICKED BEFORE THE SIDEBAR DRAWS IS NOT MOVED WHEN IT DOES.
//
// The sidebar's layout read is held open, so the boot renders its fallback pane (the first
// row, @201) with no sidebar yet. With no sidebar to tap (CMX-202 removed the pill strip),
// the header swipe is how the user picks in that gap: a swipe to @202 is the user's choice,
// and when the sidebar then draws, adoptSidebarDefault() must leave it, not jump to the top
// sidebar row.
//
// Run: pnpm exec vitest run tests/term_last_pane_tap.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootPhone, pane, settle } from './js_helpers/term_pane_fixture.mjs';

let release, chela, terminals;
beforeAll(async () => {
    const layoutGate = new Promise(r => { release = r; });
    ({ chela, terminals } = await bootPhone({ order: ['/srv/code/gamma', '/srv/code/beta', '/srv/code/alpha'], layoutGate }));
    await settle();
});

test('a swipe before the sidebar draws survives the sidebar drawing', async () => {
    assert.deepEqual(chela.sidebarOrder(), [], 'the sidebar has not drawn');
    assert.deepEqual(pane(), { value: '@201', shown: '@201' }, 'the boot fell back to the first row');
    terminals.stepAgentMobile(1);   // options order with no sidebar yet: @201 → @202
    await settle();
    release();
    await settle();
    assert.deepEqual(chela.sidebarOrder(), ['@203', '@202', '@201'], 'the sidebar has drawn');
    assert.deepEqual(pane(), { value: '@202', shown: '@202' });
});
