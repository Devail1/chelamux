// Helper for the CMX-265 END-TO-END fixture (tests/test_dispatcher_worktree_gc.py's
// test_closed_run_lands_in_archived_lane_end_to_end): NOT itself a *.test.mjs (so
// tests/test_js_suites.py's glob-discovery does not try to run it standalone — it has
// no fixture of its own to render), just the JS half of a single fixture that starts
// as a real `dispatcher.tick()`-reconciled `closed` row in Python and ends here, fed
// straight into the REAL renderKanban() the browser runs.
//
// Usage: node assert_closed_run_lane.mjs <path-to-/api/dispatcher-JSON> <closed-task-id>
// Exit 0 + nothing on stdout when every assertion holds; exit 1 + a message otherwise.
//
// Same jsdom bootstrap as tests/kanban_flatten.test.mjs (module-graph import of
// main.js so kanban.js's window.chela wiring evaluates against a real window).
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const [, , payloadPath, closedTaskId] = process.argv;
if (!payloadPath || !closedTaskId) {
    console.error('usage: node assert_closed_run_lane.mjs <payload.json> <closed-task-id>');
    process.exit(2);
}
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

const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
    { url: 'http://localhost:5005/', pretendToBeVisual: true });
for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
    'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
    'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
    Object.defineProperty(globalThis, k, {
        value: dom.window[k], writable: true, configurable: true,
    });
}
dom.window.matchMedia = q => ({
    media: q, matches: false,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
});
globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
globalThis.window.chela = globalThis.window.chela || {};
globalThis.setInterval = () => 0;
globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;

await import('../../chela/dashboard/static/js/main.js');
const { renderKanban } = await import('../../chela/dashboard/static/js/kanban.js');

try {
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
} catch (err) {
    console.error(err.message || String(err));
    process.exit(1);
}
process.exit(0);
