// CMX-164: Settings > Concurrency steppers (chela/dashboard/static/js/concurrencymodel.js).
// Run: node --test tests/concurrency_model.test.mjs (tests/test_js_suites.py runs it).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { memNote, stepPayload, workflowNote } from '../chela/dashboard/static/js/concurrencymodel.js';

const GiB = 1024 ** 3;
const state = {
    min: 1, max: 8, judge_peak_bytes: GiB, available_bytes: 4 * GiB, available_source: 'x.slice',
    judges: { effective: 2, source: 'env' },
    workflows: [{ path: '/a/WORKFLOW.md', effective: 3, override: null, file_value: 3 },
                { path: '/b/WORKFLOW.md', effective: 8, override: 8, file_value: 1 }],
};

test('stepPayload: + and - step the right workflow, by path', () => {
    assert.deepEqual(stepPayload(state, 'agents', 0, 1), { workflow: '/a/WORKFLOW.md', value: 4 });
    assert.deepEqual(stepPayload(state, 'agents', 0, -1), { workflow: '/a/WORKFLOW.md', value: 2 });
});

test('stepPayload: clamps to the bounds', () => {
    assert.deepEqual(stepPayload(state, 'agents', 1, 1), { workflow: '/b/WORKFLOW.md', value: 8 });
    const low = { ...state, judges: { effective: 1, source: 'default' } };
    assert.deepEqual(stepPayload(low, 'judges', 0, -1), { judges: 1 });
});

test('stepPayload: judges step the judge count; reset clears', () => {
    assert.deepEqual(stepPayload(state, 'judges', 0, 1), { judges: 3 });
    assert.deepEqual(stepPayload(state, 'judges', 0, 0), { judges: '' });
    assert.deepEqual(stepPayload(state, 'agents', 1, 0), { workflow: '/b/WORKFLOW.md', value: '' });
});

test('memNote: N x peak, and available memory with its source', () => {
    assert.equal(memNote(state, 3),
        'Memory: up to 3 × 1.0 GB judge peak = 3.0 GB. Available now: 4.0 GB (x.slice).');
    assert.match(memNote({ ...state, available_bytes: null }, 1), /Available now: unknown\.$/);
});

test('workflowNote: says whether the value is the file or an override', () => {
    assert.match(workflowNote(state, state.workflows[0]), /^From WORKFLOW\.md\. Memory: up to 3 /);
    assert.match(workflowNote(state, state.workflows[1]), /^Override \(WORKFLOW\.md says 1\)\. Memory: up to 8 /);
});
