// CMX-178: decisions inbox SEARCH + click-through model (decisionsmodel.js).
// Pure functions, no DOM — every property here is a straight function-of-inputs
// check, each written to go RED under one specific corruption of the real logic
// (a guard that survives its own corruption is decoration, not a guard).
//
// Run: node --test tests/decisions_search.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    filterDecisionEvents, itemFromDecisionPayload, matchesDecisionQuery,
} from '../chela/dashboard/static/js/decisionsmodel.js';

// --- itemFromDecisionPayload -------------------------------------------------

test('a run_events payload (title/run_status keys) normalises to a task-modal item', () => {
    const event = {
        seq: 5, type: 'run_review', payload: {
            task_id: 'a1b2c3', title: '**Do the thing.** Some brief.', run_status: 'awaiting_review',
            branch_name: 'cmx-9', pr_url: 'https://example.test/pr/9', pr_state: 'open',
            attempt: 1, started_at: 1000, ended_at: 2000,
        },
    };
    const item = itemFromDecisionPayload(event);
    assert.ok(item, 'a payload naming a task_id must produce an item');
    assert.equal(item.task_id, 'a1b2c3');
    assert.equal(item.title, '**Do the thing.** Some brief.');
    assert.equal(item.status, 'awaiting_review', 'run_status must map to the modal\'s `status` field');
    assert.equal(item.branch_name, 'cmx-9');
    assert.equal(item.pr_url, 'https://example.test/pr/9');
    assert.equal(item.pr_state, 'open');
    assert.equal(item.attempt, 1);
});

test('a _window_payload event (task_title key, no branch/PR) still normalises', () => {
    const event = {
        seq: 6, type: 'died', payload: {
            task_id: 'z9y8', task_title: 'A window that died', run_status: 'running',
            wid: '@3', window_name: 'cmx-11',
        },
    };
    const item = itemFromDecisionPayload(event);
    assert.ok(item, 'a window-shaped payload with a task_id must still produce an item');
    assert.equal(item.title, 'A window that died', 'task_title must be read when `title` is absent');
    assert.equal(item.status, 'running');
    // 🔴 GUARD: no branch_name/pr_url on this payload shape — the item must not
    // fabricate one. Flip `item.branch_name = p.branch_name` to a hardcoded
    // string and this goes red.
    assert.equal(item.branch_name, undefined);
});

test('a payload with NO task_id has nothing to click through to', () => {
    const event = { seq: 7, type: 'inbox_undeliverable', payload: { wid: '@0', why: 'address dead' } };
    // 🔴 GUARD: this is the whole point of the null return — a bare
    // window/inbox-plumbing event must render with no click affordance.
    // Removing the `if (!p.task_id) return null` guard makes this assert fail.
    assert.equal(itemFromDecisionPayload(event), null);
});

test('an event with no payload at all does not throw', () => {
    assert.equal(itemFromDecisionPayload({ seq: 1, type: 'finished' }), null);
    assert.equal(itemFromDecisionPayload(null), null);
});

test('reviews (a parsed list) round-trip into review_history as JSON text', () => {
    const reviews = [{ round: 1, at: 111, verdict: 'changes_requested', body: 'nope' }];
    const event = { seq: 8, type: 'run_changes_requested', payload: { task_id: 't1', reviews } };
    const item = itemFromDecisionPayload(event);
    assert.equal(typeof item.review_history, 'string', 'review_history must be a JSON string, not the raw array');
    assert.deepEqual(JSON.parse(item.review_history), reviews);
});

test('an empty reviews list leaves review_history unset (no empty timeline artefact)', () => {
    const event = { seq: 9, type: 'run_review', payload: { task_id: 't1', reviews: [] } };
    assert.equal(itemFromDecisionPayload(event).review_history, undefined);
});

// --- search matching ---------------------------------------------------------

test('a blank/whitespace query matches every event', () => {
    const event = { seq: 1, type: 'finished', summary: 'x', payload: {} };
    assert.equal(matchesDecisionQuery(event, ''), true);
    assert.equal(matchesDecisionQuery(event, '   '), true);
    assert.equal(matchesDecisionQuery(event, undefined), true);
});

test('a query matches by task_id, branch_name, or pr_url — case-insensitively', () => {
    const event = {
        seq: 2, type: 'run_review', summary: 'cmx-42 awaiting review',
        payload: { task_id: 'abc123XYZ', branch_name: 'cmx-42', pr_url: 'https://x.test/pull/42' },
    };
    assert.equal(matchesDecisionQuery(event, 'CMX-42'), true, 'branch_name match must be case-insensitive');
    assert.equal(matchesDecisionQuery(event, 'abc123xyz'), true, 'task_id match must be case-insensitive');
    assert.equal(matchesDecisionQuery(event, 'pull/42'), true, 'pr_url is a searchable field');
    assert.equal(matchesDecisionQuery(event, 'nope-not-here'), false);
});

test('a query also matches the rendered summary and event type, not payload fields alone', () => {
    const event = { seq: 3, type: 'run_needs_human', summary: 'cmx-7 NEEDS A HUMAN', payload: { task_id: 't7' } };
    assert.equal(matchesDecisionQuery(event, 'needs a human'), true);
    assert.equal(matchesDecisionQuery(event, 'run_needs_human'), true);
});

test('🔴 GUARD: filterDecisionEvents actually narrows the list, not a no-op', () => {
    const events = [
        { seq: 1, type: 'run_review', summary: 'cmx-1 awaiting review', payload: { branch_name: 'cmx-1' } },
        { seq: 2, type: 'run_review', summary: 'cmx-2 awaiting review', payload: { branch_name: 'cmx-2' } },
    ];
    const filtered = filterDecisionEvents(events, 'cmx-1');
    assert.equal(filtered.length, 1, 'the query must exclude the non-matching event');
    assert.equal(filtered[0].seq, 1);
});

test('filterDecisionEvents with a blank query returns every held event, unfiltered', () => {
    const events = [{ seq: 1, type: 'finished', payload: {} }, { seq: 2, type: 'finished', payload: {} }];
    assert.equal(filterDecisionEvents(events, '').length, 2);
    assert.equal(filterDecisionEvents(events, undefined).length, 2);
});

test('filterDecisionEvents on an empty/undefined list never throws', () => {
    assert.deepEqual(filterDecisionEvents([], 'x'), []);
    assert.deepEqual(filterDecisionEvents(undefined, 'x'), []);
});
