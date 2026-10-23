// Helper for the CMX-265 END-TO-END fixture (tests/test_dispatcher_worktree_gc.py's
// test_closed_run_lands_in_archived_lane_end_to_end): NOT itself a *.test.mjs (so
// tests/test_js_suites.py's glob-discovery does not try to run it standalone — it has
// no fixture of its own to render), just the JS half of a single fixture that starts
// as a real `dispatcher.tick()`-reconciled `closed` row in Python and ends here, fed
// straight into the REAL Work board the browser runs (features/work, CMX-233), through the
// one poll that feeds it.
//
// CMX-217: run by Vitest (the legacy graph imports TypeScript now, which plain `node` cannot
// load) through its own config, closed_run_lane.config.mjs, with the inputs in the env:
//   CLOSED_RUN_PAYLOAD=<path-to-/api/dispatcher-JSON> CLOSED_RUN_TASK=<closed-task-id> \
//     node node_modules/vitest/vitest.mjs run --config tests/js_helpers/closed_run_lane.config.mjs
// Exit 0 when every assertion holds; nonzero, with the failed assertion in the report, otherwise.
//
// The shared jsdom bootstrap (dashboard_dom.mjs, CMX-290), booting the page's real entry into the
// real page (the shell, CMX-248), so the Work view mounts in its #panel-work.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'vitest';
import { APP_BODY, bootDashboardDom, clickIsland, flush } from './dashboard_dom.mjs';

const { CLOSED_RUN_PAYLOAD: payloadPath, CLOSED_RUN_TASK: closedTaskId } = process.env;
if (!payloadPath || !closedTaskId) throw new Error('CLOSED_RUN_PAYLOAD and CLOSED_RUN_TASK must be set');
const payload = JSON.parse(readFileSync(payloadPath, 'utf8'));


test('the closed run renders in the Archived lane, labelled, never as done', async () => {
    const fetchImpl = url => Promise.resolve({
        ok: true, status: 200, json: () => Promise.resolve(String(url).endsWith('/api/dispatcher') ? payload : {}),
    });
    await bootDashboardDom({ body: APP_BODY, fetchImpl, terminalsEnabled: false });
    const { pollWork } = await import('../../chela/dashboard/frontend/src/features/work/index.ts');
    await pollWork();
    for (let i = 0; i < 4; i++) await flush();

    // CMX-316: Done and Archived are rails at the board's right edge; open both.
    for (const key of ['archived', 'done']) {
        const rail = document.querySelector(`.kanban-rail[data-col="${key}"]`);
        assert.ok(rail, `the board has no ${key} rail`);
        clickIsland(rail);
        for (let i = 0; i < 2; i++) await flush();
    }
    const archivedCol = document.querySelector('.kanban-col.kanban-col-archived');
    const doneCol = document.querySelector('.kanban-col.kanban-col-done');
    assert.ok(archivedCol, 'the board has no .kanban-col-archived column');
    assert.ok(doneCol, 'the board has no .kanban-col-done column');

    // Scoped by BOTH data-task-id and .wcard-closed: the same task_id can
    // legitimately render a second, unrelated card elsewhere on the board (an
    // unstruck tracker line for a closed-not-merged PR still shows as an open task —
    // a separate, out-of-scope surface, not what this fixture is proving). What must
    // be unambiguous is the CLOSED run's own card specifically.
    const closedCard = archivedCol.querySelector(
        `.wcard-closed[data-task-id="${closedTaskId}"]`);
    assert.ok(closedCard,
        `no .wcard-closed[data-task-id=${closedTaskId}] inside .kanban-col-archived`);
    const doneCard = doneCol.querySelector(`[data-task-id="${closedTaskId}"].wcard-done`);
    assert.ok(!doneCard,
        `task_id=${closedTaskId}'s run was ALSO coerced into a done card in the done column`);

    // Round 7 (PR #334): readable WITHOUT colour — assert the state's rendered word,
    // not just the CSS hook a red-weak reader can't see.
    const state = closedCard.querySelector('.wcard-state');
    assert.ok(state, 'the closed card has no .wcard-state word at all');
    assert.match(state.textContent, /closed, not merged/,
        `closed card's state has no readable label text — got: "${state.textContent}"`);
});
