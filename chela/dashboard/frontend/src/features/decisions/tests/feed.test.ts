// A log row's class and the cursor drain (static/js/feedmodel.js until CMX-228; ported from
// tests/feed.test.mjs). The Feed's lane grouping left with the Feed view (CMX-279).
import { expect, test } from 'vitest';
import { CLASSES, classOf, drainLog, type LogEvent } from '../feed.ts';

test('every class carries a glyph AND a word: hue is never the signal', () => {
  for (const meta of Object.values(CLASSES)) {
    expect(meta.glyph).toBeTruthy();
    expect(meta.word).toBeTruthy();
  }
});

test.each([
  ['hook.permission_request', 'gate'],
  ['hook.elicitation', 'gate'],
  ['blocked', 'gate'],
  // CMX-77: an inbox that cannot deliver is a GATE: work is stuck until a human acts.
  ['inbox_undeliverable', 'gate'],
  ['hook.permission_denied', 'denied'],
  ['hook.pre_tool_use', 'tool'],
  ['hook.user_prompt_submit', 'prompt'],
  ['run_review', 'run'],
  ['run_failed', 'run'],
  ['run_changes_requested', 'run'],
  ['run_needs_human', 'run'],
  // CMX-197 / CMX-239 / CMX-358 / CMX-86: unmapped, each would render as an anonymous `·`.
  ['run_judge_clean', 'run'],
  ['run_judge_cannot_verify', 'run'],
  ['run_judge_blocked_race', 'run'],
  ['run_unjudged_merged', 'run'],
  ['run_judge_not_started', 'run'],
  ['run_reopened', 'run'],
  ['run_was_closed', 'run'],
  ['run_ci_red', 'run'],
  ['run_ci_flake', 'run'],
  ['run_pr_conflicting', 'run'],
  ['run_stuck', 'run'],
  ['run_marker_unread', 'run'],
  ['run_selfcheck_long', 'run'],
  ['run_park_overdue', 'run'],
  ['dispatch_hold_expired', 'lifecycle'],
  ['dispatch_hold_released', 'lifecycle'],
  ['daemon_start', 'lifecycle'],
  ['inbox_self_healed', 'lifecycle'],
  ['orchestrator.moved', 'lifecycle'],
  ['watch_epoch_lost', 'lifecycle'],
  ['finished', 'lifecycle'],
  ['died', 'lifecycle'],
  ['gone_unknown', 'lifecycle'],
  ['completed_gone', 'lifecycle'],
  // An unknown type is `other`: the safe default for "never heard of it" is to SHOW it.
  ['note', 'other'],
  ['constructor', 'other'],
])('classOf(%s) is %s', (type, cls) => {
  expect(classOf(type)).toBe(cls);
});

// --- the cursor: a bounded read must RESUME, not skip. event_log.read(), faked: the `limit`
// OLDEST events after the cursor, plus the cursor (`next_seq`) and the log's tail
// (`last_seq`), which DIFFER once truncated.
function fakeLog(n: number) {
  const all: LogEvent[] = Array.from({ length: n }, (_, i) => ({ seq: i + 1, type: 'finished', wid: '@1' }));
  const calls: { after_seq: number | null; limit: number }[] = [];
  return {
    calls,
    serve({ after_seq, limit }: { after_seq: number | null; limit: number }) {
      calls.push({ after_seq, limit });
      const last_seq = all.length ? (all[all.length - 1] as LogEvent).seq : 0;
      let out = all.filter((e) => after_seq == null || e.seq > after_seq);
      let next_seq = last_seq;
      if (out.length > limit) {
        out = out.slice(0, limit);
        next_seq = (out[out.length - 1] as LogEvent).seq;
      }
      return Promise.resolve({ boot_id: 'b1', events: out, gap: null, first_seq: 1, last_seq, next_seq });
    },
  };
}

test('a bounded read DRAINS to the tail: every event, exactly once, in order', async () => {
  const log = fakeLog(25);
  const r = await drainLog(log.serve, { limit: 10, maxFetches: 8 });
  expect(r.events.map((e) => e.seq)).toEqual(Array.from({ length: 25 }, (_, i) => i + 1));
  expect(r.cursor).toBe(25);
  expect(log.calls).toEqual([
    { after_seq: null, limit: 10 },
    { after_seq: 10, limit: 10 },
    { after_seq: 20, limit: 10 },
  ]);
  expect([r.boot, r.gap, r.cleared]).toEqual(['b1', null, false]);
});

test('a reader that resumed from last_seq would SKIP: this is what the contract buys', async () => {
  const log = fakeLog(25);
  let cursor: number | null = null;
  let got: LogEvent[] = [];
  for (let i = 0; i < 8; i++) {
    const b = await log.serve({ after_seq: cursor, limit: 10 });
    got = got.concat(b.events);
    cursor = b.last_seq; // the bug, spelled out
    if (!b.events.length) break;
  }
  expect(got).toHaveLength(10);
  expect((await drainLog(log.serve, { limit: 10, maxFetches: 8 })).events).toHaveLength(25);
});

test('the drain is BOUNDED: it yields with a resumable cursor rather than spinning', async () => {
  const r = await drainLog(fakeLog(100).serve, { limit: 10, maxFetches: 3 });
  expect(r.events).toHaveLength(30);
  expect(r.cursor).toBe(30);
});

test('an empty batch is the tail: the drain stops there', async () => {
  const log = fakeLog(0);
  const r = await drainLog(log.serve, { cursor: 5, boot: 'b1', limit: 10, maxFetches: 8 });
  expect(log.calls).toHaveLength(1);
  expect(r.events).toEqual([]);
});

test('a GAP is handed up and invalidates what we hold, never swallowed', async () => {
  const gap = { reason: 'the log rotated past your cursor' };
  let n = 0;
  const serve = () =>
    Promise.resolve(
      n++ === 0
        ? { boot_id: 'b1', events: [{ seq: 13, type: 'finished' }], gap: null, last_seq: 500, next_seq: 13 }
        : { boot_id: 'b2', events: [{ seq: 401, type: 'finished' }], gap, last_seq: 401, next_seq: 401 },
    );
  const r = await drainLog(serve, { cursor: 12, boot: 'b1', limit: 1, maxFetches: 8 });
  expect(r.gap).toEqual(gap);
  expect(r.cleared).toBe(true);
  expect(r.events.map((e) => e.seq)).toEqual([401]); // the pre-gap row is dropped
  expect(r.boot).toBe('b2');
});

test('a failed fetch or a malformed batch ends the drain and LEAVES THE CURSOR', async () => {
  for (const serve of [() => Promise.reject(new Error('offline')), () => Promise.resolve(null), () => Promise.resolve({ events: 'x' })]) {
    const r = await drainLog(serve, { cursor: 7, boot: 'b1', limit: 10, maxFetches: 8 });
    expect(r).toEqual({ events: [], cursor: 7, boot: 'b1', gap: null, cleared: false });
  }
});
