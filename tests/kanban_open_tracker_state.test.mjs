// 🗂️ CMX-168 — an OPEN task's card sits in the tracker column Linear has it in. The API
// sends each open task's `tracker_state`; _kanbanFlatten used to build the open card
// without it, so columnOf() fell back to the first `unstarted` column and every Backlog
// issue rendered in Todo (19 there vs Linear's 7). Runs the REAL renderKanban against a
// real DOM and reads the column each card landed in.
//
// Run: pnpm exec vitest run tests/kanban_open_tracker_state.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery).
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { bootDashboardDom } from './js_helpers/dashboard_dom.mjs';

const KANBAN_BODY = `
<div class="work-pane active" id="work-board" data-seg="board">
  <div class="kanban-filters" id="kanban-filters"></div>
  <div class="kanban-nav-strip" id="kanban-nav-strip"></div>
  <div class="kanban-board-wrap"><div class="kanban-board" id="kanban-board"></div></div>
  <div id="kanban-empty" class="work-empty" style="display:none;"></div>
</div>`;

const STATES = [
    { name: 'Backlog', type: 'backlog', hidden: false },
    { name: 'Todo', type: 'unstarted', hidden: false },
    { name: 'In Progress', type: 'started', hidden: false },
    { name: 'Done', type: 'completed', hidden: false },
];

let renderKanban;

beforeAll(async () => {
    ({ modules: { kanban: { renderKanban } } } = await bootDashboardDom({
        body: KANBAN_BODY, extraModules: ['kanban.js'],
        fetchImpl: () => new Promise(() => {}),
    }));
});

beforeEach(() => { localStorage.clear(); });

const task = (id, state) => ({ id, title: `title ${id}`, file: 'Linear', line_number: 0,
                               raw: '', body: null, tracker_url: null, tracker_state: state });
const payload = (open, columns = STATES) => ({ configured: true, workflows: [{
    path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: open, backlog_items: [],
    parked_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [],
    tracker_columns: columns }] });

// The titles of the cards drawn in board column `key` (null when that column is not drawn).
function titlesIn(key) {
    const col = document.querySelector(`#kanban-board .kanban-col[data-col="${key}"]`);
    if (!col) return null;
    return [...col.querySelectorAll('.kanban-card .kanban-card-title')].map(e => e.textContent.trim());
}
const boardCols = () => [...document.querySelectorAll('#kanban-board .kanban-col-label')].map(e => e.textContent);
const hiddenRows = () => [...document.querySelectorAll('#kanban-hidden .kanban-hidden-label')].map(e => e.textContent);

test('an open Backlog task renders in Backlog, an open Todo task in Todo', () => {
    renderKanban(payload([task('CMX-1', 'Backlog'), task('CMX-2', 'Todo')]));
    assert.deepEqual(titlesIn('tr-backlog'), ['title CMX-1']);
    assert.deepEqual(titlesIn('tr-todo'), ['title CMX-2']);
});

test('negative control: an open task with no tracker_state falls back to Todo', () => {
    // The pre-fix shape: the card without its state lands in the first `unstarted` column.
    renderKanban(payload([task('CMX-1', null), task('CMX-2', 'Todo')]));
    assert.deepEqual(titlesIn('tr-todo'), ['title CMX-1', 'title CMX-2']);
    assert.equal(titlesIn('tr-backlog'), null);
});

test('state matching is case-insensitive on the tracker state name', () => {
    renderKanban(payload([task('CMX-1', 'backlog')]));
    assert.deepEqual(titlesIn('tr-backlog'), ['title CMX-1']);
});

test('an open task whose state maps to no column is shown in Unmapped, never dropped', () => {
    // No `unstarted` column, so neither the state nor the status fallback places it.
    const cols = STATES.filter(s => s.type !== 'unstarted');
    renderKanban(payload([task('CMX-9', 'Triage'), task('CMX-1', 'Backlog')], cols));
    assert.deepEqual(titlesIn('tr-unmapped'), ['title CMX-9']);
    assert.deepEqual(titlesIn('tr-backlog'), ['title CMX-1']);
    assert.ok(boardCols().includes('Unmapped'));
});

test('CMX-136: an empty Backlog is in the hidden panel; a carded one is on the board', () => {
    renderKanban(payload([task('CMX-2', 'Todo')]));
    assert.ok(hiddenRows().includes('Backlog'), 'empty Backlog should be hidden');
    assert.ok(!boardCols().includes('Backlog'));
    renderKanban(payload([task('CMX-1', 'Backlog'), task('CMX-2', 'Todo')]));
    assert.ok(boardCols().includes('Backlog'), 'a carded Backlog should be drawn');
    assert.ok(!hiddenRows().includes('Backlog'));
});
