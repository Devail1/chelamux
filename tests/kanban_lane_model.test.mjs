// KANBAN LANE MODEL — pure status→lane guards (chela/dashboard/static/js/
// kanbanlanemodel.js). No DOM, no jsdom: laneOf() takes a plain status string
// and returns a plain lane-key string, so these are straight function-of-
// input checks, each written to go RED under one specific corruption of the
// real logic (a guard that survives its own corruption is decoration, not a
// guard).
//
// Run: node --test tests/kanban_lane_model.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { KANBAN_LANES, KANBAN_LANE_LABELS, laneOf } from '../chela/dashboard/static/js/kanbanlanemodel.js';

// The whole status set the Work board currently renders — every status
// _kanbanFlatten (kanban.js) can hand a card, across all 5 lanes.
const ALL_STATUSES = [
    'backlog', 'open', 'claimed', 'running',
    'awaiting_review', 'changes_requested', 'needs_human', 'failed',
    'done',
];

// --- per-status mapping ------------------------------------------------------

test('laneOf: backlog -> backlog', () => {
    assert.equal(laneOf('backlog'), 'backlog');
});

test('laneOf: open -> todo', () => {
    assert.equal(laneOf('open'), 'todo');
});

test('laneOf: claimed -> in_progress', () => {
    // 🔴 GUARD: re-mapping claimed to its own lane (or to review) breaks the
    // In Progress lane's whole premise — claimed and running must share it.
    assert.equal(laneOf('claimed'), 'in_progress');
});

test('laneOf: running -> in_progress', () => {
    assert.equal(laneOf('running'), 'in_progress');
});

test('laneOf: awaiting_review -> review', () => {
    assert.equal(laneOf('awaiting_review'), 'review');
});

test('laneOf: changes_requested -> review', () => {
    // 🔴 GUARD: the rework loop's states must not get a lane of their own —
    // they ride in Review alongside awaiting_review, same as the old
    // single-column board's Awaiting Review bucket.
    assert.equal(laneOf('changes_requested'), 'review');
});

test('laneOf: needs_human -> review', () => {
    assert.equal(laneOf('needs_human'), 'review');
});

test('laneOf: failed -> review (Liav\'s call, 2026-07-25)', () => {
    // 🔴 GUARD: THE decision this whole slice exists to encode. Re-mapping
    // failed to 'done' (the tempting "it's finished" read) would bury the one
    // state that most needs the orchestrator's attention among cards nobody
    // is actively watching — this is exactly the corruption to check by hand.
    assert.equal(laneOf('failed'), 'review');
});

test('laneOf: done -> done', () => {
    assert.equal(laneOf('done'), 'done');
});

// --- unknown-status fallback --------------------------------------------------

test('laneOf: an unknown status falls back to review, not hidden in backlog/done', () => {
    // 🔴 GUARD: a future dispatcher status (or a typo) must stay VISIBLE — the
    // fallback exists so an anomaly surfaces where the orchestrator already
    // looks for "needs action", never silently vanishing into a lane nobody
    // re-opens. Changing the fallback to return null/undefined, or to
    // 'backlog'/'done', is precisely the corruption this guards against.
    assert.equal(laneOf('some_unknown_status'), 'review');
    assert.equal(laneOf(''), 'review');
    assert.equal(laneOf(undefined), 'review');
    assert.equal(laneOf(null), 'review');
});

// --- completeness: every status maps onto exactly the 5 lane keys ------------

test('KANBAN_LANES has exactly the 5 lanes, in order', () => {
    assert.deepEqual(KANBAN_LANES, ['backlog', 'todo', 'in_progress', 'review', 'done']);
});

test('every lane key has a label', () => {
    for (const key of KANBAN_LANES) {
        assert.equal(typeof KANBAN_LANE_LABELS[key], 'string');
        assert.ok(KANBAN_LANE_LABELS[key].length > 0, `lane "${key}" has no label`);
    }
});

test('completeness: every known status resolves to one of the 5 lane keys, none undefined', () => {
    // 🔴 GUARD: deleting a status's entry from STATUS_LANE inside
    // kanbanlanemodel.js does NOT throw — laneOf's `|| 'review'` fallback
    // would quietly swallow it into Review. That is an ACCEPTABLE fallback
    // for a genuinely unknown status (see the test above), but this test
    // pins the full set of 9 KNOWN statuses landing on their real, specific
    // lanes — it would only catch a delete if paired with the per-status
    // assertions above going RED too, which is why both exist.
    for (const status of ALL_STATUSES) {
        const lane = laneOf(status);
        assert.ok(KANBAN_LANES.includes(lane), `laneOf(${status}) returned "${lane}", not a real lane key`);
    }
});

test('completeness: every one of the 5 lane keys is actually reachable from the known status set', () => {
    // 🔴 GUARD: deleting a LANE (e.g. dropping 'review' from KANBAN_LANES
    // while STATUS_LANE still points statuses at it) would leave a lane no
    // status can ever land in, or a lane KANBAN_LANES doesn't know about —
    // either way the board silently loses a lane. This checks the mapping is
    // onto, not just well-typed.
    const reached = new Set(ALL_STATUSES.map(laneOf));
    for (const key of KANBAN_LANES) {
        assert.ok(reached.has(key), `lane "${key}" is unreachable — no known status maps to it`);
    }
    assert.equal(reached.size, KANBAN_LANES.length, 'every lane key is reachable, and nothing besides the 5 lane keys is produced');
});
