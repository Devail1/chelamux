// The inbox's unread / search / click-through math (static/js/decisionsmodel.js until
// CMX-228; ported from tests/decisions_badge.test.mjs and decisions_search.test.mjs).
import { expect, test } from 'vitest';
import type { LogEvent } from '../feed.ts';
import {
  DECISION_URGENCY,
  DISPATCHER_AGED_OUT_NOTE,
  filterDecisionEvents,
  findDispatcherRun,
  formatUnreadCount,
  itemFromDecisionPayload,
  matchesDecisionQuery,
  maxSeq,
  partialItemFromDecisionPayload,
  seedLastSeen,
  unreadCount,
  unreadUrgency,
  unseenEvents,
} from '../model.ts';

const ev = (seq: number, type = 'finished'): LogEvent => ({ seq, type, ts: seq, wid: '@1', summary: '' });

test('unread is events with seq STRICTLY greater than lastSeen', () => {
  const events = [ev(1), ev(2), ev(3), ev(4)];
  expect(unreadCount(events, 2)).toBe(2);
  expect(unreadCount(events, 4)).toBe(0); // the just-seen event never re-counts
  expect(unreadCount([ev(1), ev(2)], 5)).toBe(0);
  expect(unreadCount(events, null)).toBe(4);
  expect(unseenEvents([null, ev(3)], 0)).toEqual([ev(3)]);
  expect(unreadCount(undefined, 0)).toBe(0);
});

test('the label: empty at zero (the badge hides), capped at 99+', () => {
  expect(formatUnreadCount(0)).toBe('');
  expect(formatUnreadCount(1)).toBe('1');
  expect(formatUnreadCount(99)).toBe('99');
  expect(formatUnreadCount(100)).toBe('99+');
});

test('first-ever load seeds lastSeen to the CURRENT max held seq, never 0', () => {
  const events = [ev(1), ev(3), ev(2)];
  expect(seedLastSeen(events)).toBe(3);
  expect(unreadCount(events, seedLastSeen(events))).toBe(0);
  expect(seedLastSeen([])).toBe(0);
  expect(maxSeq([null, ev(7)])).toBe(7);
});

test('urgency: bad outranks warn outranks neutral, over the UNSEEN events only', () => {
  expect(unreadUrgency([ev(1), ev(2, 'run_failed')], 0)).toBe('bad');
  expect(unreadUrgency([ev(1), ev(2, 'run_review')], 0)).toBe('warn');
  expect(unreadUrgency([ev(1), ev(2, 'completed_gone')], 0)).toBe('neutral');
  expect(unreadUrgency([ev(1, 'run_review'), ev(2, 'run_failed')], 0)).toBe('bad');
  expect(unreadUrgency([ev(1, 'run_failed'), ev(2)], 1)).toBe('neutral');
});

test('the urgency table, by literal value', () => {
  expect(DECISION_URGENCY).toEqual({
    run_failed: 'bad',
    died: 'bad',
    gone_unknown: 'bad',
    inbox_undeliverable: 'bad',
    watch_epoch_lost: 'bad',
    run_review: 'warn',
    run_needs_human: 'warn',
    run_changes_requested: 'warn',
    blocked: 'warn',
  });
});

// --- itemFromDecisionPayload

test('a run_events payload (title/run_status keys) normalises to a task-modal item', () => {
  const item = itemFromDecisionPayload({
    seq: 5,
    type: 'run_review',
    payload: { task_id: 'a1b2c3', title: '**Do the thing.**', run_status: 'awaiting_review', branch_name: 'cmx-9', pr_url: 'https://example.test/pr/9', pr_state: 'open', attempt: 1, rework_count: 2, last_error: 'boom', started_at: 1000, ended_at: '2026-10-01T00:00:00Z' },
  });
  expect(item).toEqual({ task_id: 'a1b2c3', title: '**Do the thing.**', status: 'awaiting_review', branch_name: 'cmx-9', pr_url: 'https://example.test/pr/9', pr_state: 'open', attempt: 1, rework_count: 2, last_error: 'boom', started_at: 1000, ended_at: '2026-10-01T00:00:00Z' });
});

test('a _window_payload event (task_title, no branch/PR) still normalises, fabricating nothing', () => {
  const item = itemFromDecisionPayload({ seq: 6, type: 'died', payload: { task_id: 'z9y8', task_title: 'A window that died', run_status: 'running', wid: '@3' } });
  expect(item?.title).toBe('A window that died');
  expect(item?.status).toBe('running');
  expect(item?.branch_name).toBeUndefined();
  expect(itemFromDecisionPayload({ seq: 1, type: 'x', payload: { task_id: 't' } })?.title).toBe('');
});

test('no task_id, no payload or no event: nothing to click through to', () => {
  expect(itemFromDecisionPayload({ seq: 7, type: 'inbox_undeliverable', payload: { wid: '@0' } })).toBeNull();
  expect(itemFromDecisionPayload({ seq: 1, type: 'finished' })).toBeNull();
  expect(itemFromDecisionPayload(null)).toBeNull();
});

test('reviews round-trip into review_history as JSON text; an empty list leaves it unset', () => {
  const reviews = [{ round: 1, at: 111, verdict: 'changes_requested', body: 'nope' }];
  const item = itemFromDecisionPayload({ seq: 8, type: 'run_changes_requested', payload: { task_id: 't1', reviews } });
  expect(JSON.parse(item?.review_history ?? 'null')).toEqual(reviews);
  expect(itemFromDecisionPayload({ seq: 9, type: 'run_review', payload: { task_id: 't1', reviews: [] } })?.review_history).toBeUndefined();
});

// --- search

test('a blank query matches every event', () => {
  const e = { seq: 1, type: 'finished', summary: 'x', payload: {} };
  expect(matchesDecisionQuery(e, '')).toBe(true);
  expect(matchesDecisionQuery(e, '   ')).toBe(true);
});

test.each([
  ['task_id', { task_id: 'abc123XYZ' }, 'abc123xyz'],
  ['title', { title: 'Port the Inbox' }, 'the inbox'],
  ['task_title', { task_title: 'A Window' }, 'a window'],
  ['branch_name', { branch_name: 'cmx-42' }, 'CMX-42'],
  ['pr_url', { pr_url: 'https://x.test/pull/42' }, 'pull/42'],
  ['window_name', { window_name: 'agent-z' }, 'AGENT-Z'],
])('a query matches by %s, case-insensitively', (_f, payload, query) => {
  expect(matchesDecisionQuery({ seq: 2, type: 'run_review', payload }, query)).toBe(true);
  expect(matchesDecisionQuery({ seq: 2, type: 'run_review', payload }, 'nope-not-here')).toBe(false);
});

test('a query also matches the summary, the event type and the window', () => {
  const e = { seq: 3, type: 'run_needs_human', summary: 'cmx-7 NEEDS A HUMAN', wid: '@77', payload: { task_id: 't7' } };
  expect(matchesDecisionQuery(e, 'needs a human')).toBe(true);
  expect(matchesDecisionQuery(e, 'run_needs_human')).toBe(true);
  expect(matchesDecisionQuery(e, '@77')).toBe(true);
});

test('filterDecisionEvents narrows; a blank query returns every event; nothing never throws', () => {
  const events = [
    { seq: 1, type: 'run_review', summary: 'cmx-1', payload: { branch_name: 'cmx-1' } },
    { seq: 2, type: 'run_review', summary: 'cmx-2', payload: { branch_name: 'cmx-2' } },
  ];
  expect(filterDecisionEvents(events, 'cmx-1').map((e) => e.seq)).toEqual([1]);
  expect(filterDecisionEvents(events, '')).toEqual(events);
  expect(filterDecisionEvents(events, '')).not.toBe(events); // a copy: the caller may sort it
  expect(filterDecisionEvents(undefined, 'x')).toEqual([]);
});

// --- findDispatcherRun (CMX-178 rework)

test('finds a run in any bucket of any workflow, and an open task by `id`', () => {
  const run = { task_id: 't9', brief: 'the real brief' };
  const data = {
    workflows: [
      { open_tasks: [], active_runs: [{ task_id: 'a1' }], awaiting_review_runs: [], recent_runs: [] },
      { open_tasks: [{ id: 'cmx-9', body: 'the brief' }], active_runs: [], awaiting_review_runs: [run], recent_runs: [{ task_id: 'r1' }] },
    ],
  };
  expect(findDispatcherRun(data, 't9')).toBe(run); // the SAME object, never a copy
  expect(findDispatcherRun(data, 'cmx-9')?.body).toBe('the brief');
  expect(findDispatcherRun(data, 'a1')?.task_id).toBe('a1');
  expect(findDispatcherRun(data, 'r1')?.task_id).toBe('r1');
  expect(findDispatcherRun(data, 'not-present')).toBeNull();
});

test('findDispatcherRun never throws on a missing/malformed response, nor matches no task id', () => {
  for (const bad of [null, {}, { workflows: [] }, { workflows: [{}] }, { workflows: [null, { active_runs: [null, {}] }] }]) expect(findDispatcherRun(bad, 't1')).toBeNull();
  const data = { workflows: [{ active_runs: [{ task_id: 'a1' }] }] };
  expect(findDispatcherRun(data, null)).toBeNull();
  expect(findDispatcherRun(data, '')).toBeNull();
});

test('a partial ticket carries the payload AND a visible aged-out note, as its body', () => {
  const item = partialItemFromDecisionPayload({ seq: 5, type: 'run_review', payload: { task_id: 't9', branch_name: 'cmx-9' } });
  expect(item?.task_id).toBe('t9');
  expect(item?.branch_name).toBe('cmx-9');
  expect(item?.body).toBe(DISPATCHER_AGED_OUT_NOTE);
  expect(DISPATCHER_AGED_OUT_NOTE).toMatch(/aged out/);
  expect(partialItemFromDecisionPayload({ seq: 6, type: 'inbox_undeliverable', payload: { wid: '@0' } })).toBeNull();
});
