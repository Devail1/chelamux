// 🗂️📐 CMX-23 — the Work board's Linear columns (chela/dashboard/static/js/
// kanbanlinearmodel.js). Pure model, no DOM: each test goes RED under one specific
// corruption of the real logic, and each carries its negative control.
//
// Run: node --test tests/kanban_linear_model.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    UNMAPPED_KEY, columnOf, runStateBadges, trackerBoard, trackerColumns,
} from '../chela/dashboard/static/js/kanbanlinearmodel.js';


// A fake team's states, already in Linear's board order (the server sorts them).
const LINEAR = [
    { name: 'Backlog', type: 'backlog', hidden: false },
    { name: 'Todo', type: 'unstarted', hidden: false },
    { name: 'In Progress', type: 'started', hidden: false },
    { name: 'In Review', type: 'started', hidden: false },
    { name: 'Done', type: 'completed', hidden: false },
    { name: 'Canceled', type: 'canceled', hidden: true },
    { name: 'Duplicate', type: 'canceled', hidden: true },
];

const labels = board => board.columns.map(c => c.label);

test('columns are the tracker states, in its order, with Canceled/Duplicate hidden', () => {
    const cols = trackerColumns([{ path: 'a', tracker_columns: LINEAR }]);
    const board = trackerBoard(cols, []);
    assert.deepEqual(labels(board), ['Backlog', 'Todo', 'In Progress', 'In Review', 'Done']);
    // Negative control: chela's run statuses are NOT columns.
    for (const s of ['failed', 'Review', 'running', 'needs_human', 'To Do']) {
        assert.ok(!labels(board).includes(s), `${s} must not be a column`);
    }
});

test('a workflow without tracker states falls back to chela lanes (null)', () => {
    assert.equal(trackerColumns([{ path: 'md', tracker_columns: null }]), null);
    assert.equal(trackerColumns([{ path: 'a', tracker_columns: LINEAR },
                                 { path: 'md', tracker_columns: null }]), null);
    // Control: the Linear-only view does get columns.
    assert.ok(trackerColumns([{ path: 'a', tracker_columns: LINEAR }]));
});

test('a failed run in In Review renders inside In Review with its failed badge — no failed column', () => {
    const card = { status: 'failed', task_id: 'CMX-19', tracker_state: 'In Review', pr_url: 'https://x/pull/1' };
    const board = trackerBoard(LINEAR, [card]);
    const review = board.columns.find(c => c.label === 'In Review');
    assert.deepEqual(review.cards, [card]);
    for (const c of board.columns) if (c.label !== 'In Review') assert.equal(c.cards.length, 0, c.label);
    assert.ok(!labels(board).some(l => /fail/i.test(l)));
    // The card's own red "failed" pill is checked on the RENDERED card:
    // tests/kanban_tracker_render.test.mjs.
});

test('the tracker state wins over the run status; the status is only the fallback', () => {
    // A running run whose issue a human moved to In Review stays where Linear says.
    assert.equal(columnOf({ status: 'running', tracker_state: 'In Review' }, LINEAR).name, 'In Review');
    // Control: with no tracker state, a running run is In Progress, an awaiting one In Review,
    // a done one Done, an open one Todo.
    assert.equal(columnOf({ status: 'running' }, LINEAR).name, 'In Progress');
    assert.equal(columnOf({ status: 'awaiting_review' }, LINEAR).name, 'In Review');
    assert.equal(columnOf({ status: 'done' }, LINEAR).name, 'Done');
    assert.equal(columnOf({ status: 'open' }, LINEAR).name, 'Todo');
    assert.equal(columnOf({ status: 'failed' }, LINEAR).name, 'In Progress');
    assert.equal(columnOf({ status: 'failed', pr_url: 'https://x/pull/2' }, LINEAR).name, 'In Review');
});

test('a closed run lands in the hidden Canceled column — counted, not shown', () => {
    const board = trackerBoard(LINEAR, [{ status: 'closed' }, { status: 'done' }]);
    assert.equal(board.hiddenCount, 1);
    assert.equal(board.columns.find(c => c.label === 'Done').cards.length, 1);
});

test('a card no column fits is never dropped — it lands in Unmapped', () => {
    const board = trackerBoard(LINEAR, [{ status: 'mystery', tracker_state: 'Triage' }]);
    const un = board.columns.find(c => c.key === UNMAPPED_KEY);
    assert.ok(un && un.cards.length === 1);
    // Control: a mapped card makes no Unmapped column.
    assert.ok(!trackerBoard(LINEAR, [{ status: 'open' }]).columns.some(c => c.key === UNMAPPED_KEY));
});

test('run state rides as badges: judging, rework N, blocked race', () => {
    const got = runStateBadges({ status: 'running', judge_state: 'running', rework_count: 2 }).map(b => b.label);
    assert.deepEqual(got, ['⚖️ judging', '🔁 rework 2']);
    assert.deepEqual(runStateBadges({ status: 'awaiting_review', judge_state: 'blocked_race' }).map(b => b.label),
                     ['🧊 blocked race']);
    // Control: a quiet run carries none.
    assert.deepEqual(runStateBadges({ status: 'running', judge_state: 'clean', rework_count: 0 }), []);
});

// kanban.js's call sites are guarded by a RENDER, not a source regex:
// tests/kanban_tracker_render.test.mjs.
