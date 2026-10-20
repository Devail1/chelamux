// Helper for the CMX-265 END-TO-END fixture (tests/test_dispatcher_worktree_gc.py's
// test_closed_run_lands_in_archived_lane_end_to_end): NOT itself a *.test.mjs (so
// tests/test_js_suites.py's glob-discovery does not try to run it standalone — it has
// no fixture of its own to render), just the JS half of a single fixture that starts
// as a real `dispatcher.tick()`-reconciled `closed` row in Python and ends here, fed
// straight into the REAL renderKanban() the browser runs.
//
// CMX-217: run by Vitest (the legacy graph imports TypeScript now, which plain `node` cannot
// load) through its own config, closed_run_lane.config.mjs, with the inputs in the env:
//   CLOSED_RUN_PAYLOAD=<path-to-/api/dispatcher-JSON> CLOSED_RUN_TASK=<closed-task-id> \
//     node node_modules/vitest/vitest.mjs run --config tests/js_helpers/closed_run_lane.config.mjs
// Exit 0 when every assertion holds; nonzero, with the failed assertion in the report, otherwise.
//
// Same jsdom bootstrap as tests/kanban_flatten.test.mjs — shared via
// dashboard_dom.mjs (CMX-290), booting the page's real entry so kanban.js's
// window.chela wiring evaluates against a real window.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'vitest';
import { bootDashboardDom } from './dashboard_dom.mjs';

const { CLOSED_RUN_PAYLOAD: payloadPath, CLOSED_RUN_TASK: closedTaskId } = process.env;
if (!payloadPath || !closedTaskId) throw new Error('CLOSED_RUN_PAYLOAD and CLOSED_RUN_TASK must be set');
const payload = JSON.parse(readFileSync(payloadPath, 'utf8'));

const BODY = `
<div class="work-pane active" id="work-board" data-seg="board">
  <div class="kanban-filters" id="kanban-filters"></div>
  <div class="kanban-mobile-controls" id="kanban-mobile-controls">
    <div class="kanban-nav-strip" id="kanban-nav-strip" aria-label="Jump to column"></div>
  </div>
  <div class="kanban-board" id="kanban-board"></div>
  <div id="kanban-empty" class="work-empty" style="display:none;"></div>
</div>`;

test('the closed run renders in the Archived lane, labelled, never as done', async () => {
    const { modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: BODY, extraModules: ['kanban.js'],
    });
    renderKanban(payload);

    const archivedCol = document.querySelector('.kanban-col-archived');
    const doneCol = document.querySelector('.kanban-col-done');
    assert.ok(archivedCol, '#kanban-board has no .kanban-col-archived column');
    assert.ok(doneCol, '#kanban-board has no .kanban-col-done column');

    // Scoped by BOTH data-task-id and .kanban-card-closed: the same task_id can
    // legitimately render a second, unrelated card elsewhere on the board (an
    // unstruck tracker line for a closed-not-merged PR still shows as an open task —
    // a separate, out-of-scope surface, not what this fixture is proving). What must
    // be unambiguous is the CLOSED run's own card specifically.
    const closedCard = archivedCol.querySelector(
        `.kanban-card-closed[data-task-id="${closedTaskId}"]`);
    assert.ok(closedCard,
        `no .kanban-card-closed[data-task-id=${closedTaskId}] inside .kanban-col-archived`);
    const doneCard = doneCol.querySelector(`[data-task-id="${closedTaskId}"].kanban-card-done`);
    assert.ok(!doneCard,
        `task_id=${closedTaskId}'s run was ALSO coerced into a done card in the done column`);

    // Round 7 (PR #334): readable WITHOUT colour — assert the pill's rendered word,
    // not just the CSS hook a red-weak reader can't see.
    const stateChip = closedCard.querySelector('.kanban-state-chip');
    assert.ok(stateChip, 'the closed card has no .kanban-state-chip pill at all');
    assert.match(stateChip.textContent, /closed, not merged/,
        `closed card's status pill has no readable label text — got: "${stateChip.textContent}"`);
});
