// THE FEED'S AGENT LANES — the properties that decide whether this layout works.
//
// A lane view is only as good as the `wid` on a row, and a filtered view is only as
// good as what it admits to hiding. feedmodel.js is pure (no DOM, no fetch), so all
// of that is provable here without a browser:
//
//   1. lanes sort by ATTENTION — needs-you → busy → idle → gone → chela itself;
//   2. a DEAD agent's lane still renders, from the LOG (74 of one day's events
//      belonged to windows that no longer existed);
//   3. an unattributed event lands in the `chela itself` lane and is NEVER guessed
//      into someone's (CMX-48: a wrong wid is worse than no wid);
//   4. a lane is keyed on `wid`, never on a name (tmux rename is the truth);
//   5. the firehose is hidden by DEFAULT but never SILENTLY — the count is stated.
//
// Run: node --test tests/  (tests/test_js_suites.py runs every .test.mjs inside pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    CLASSES, DEFAULT_CLASSES, SYSTEM_LABEL, SYSTEM_WID,
    buildLanes, classOf, drainLog, flatRows, goneSummary, hiddenSummary, splitGone,
} from '../chela/dashboard/static/js/feedmodel.js';

let _seq = 0;
const ev = (type, wid, over = {}) =>
    ({ seq: ++_seq, ts: 1000 + _seq, type, wid: wid ?? null, summary: type, payload: {}, ...over });

// --- classification ---------------------------------------------------------

test('every class carries a glyph AND a word — hue is never the signal', () => {
    for (const [id, meta] of Object.entries(CLASSES)) {
        assert.ok(meta.glyph, `${id} has no glyph`);
        assert.ok(meta.word, `${id} has no word`);
    }
});

test('a gate is a gate, a tool call is the firehose, an unknown type is shown', () => {
    assert.equal(classOf('hook.permission_request'), 'gate');
    assert.equal(classOf('hook.pre_tool_use'), 'tool');
    assert.equal(classOf('run_review'), 'run');
    // CMX-197: the judge's own verdict on a run sitting in `awaiting_review` — without
    // these two it falls through to `other`, and the one push that says "this PR is
    // mergeable" renders as an anonymous `·` outside the Runs filter.
    assert.equal(classOf('run_judge_clean'), 'run');
    assert.equal(classOf('run_judge_cannot_verify'), 'run');
    assert.equal(classOf('daemon_start'), 'lifecycle');
    // An inbox that cannot deliver is a GATE: work is stuck until a human acts, and it
    // stayed invisible for a whole outage (CMX-77). `other` would render it as a `·`.
    assert.equal(classOf('inbox_undeliverable'), 'gate');
    // ...and its recovery (CMX-82) is a lifecycle event: the address self-healed from the
    // orchestrator's session identity, no human in the loop.
    assert.equal(classOf('inbox_self_healed'), 'lifecycle');
    assert.ok(DEFAULT_CLASSES.includes('gate'));
    // An unknown type is `other` — and `other` is ON by default. The safe default for
    // "I have never heard of this" is to show it, not to swallow it.
    assert.equal(classOf('note'), 'other');
    assert.ok(DEFAULT_CLASSES.includes('other'));
    // The firehose and the prompt traffic are behind a toggle (86% of the log).
    assert.ok(!DEFAULT_CLASSES.includes('tool'));
    assert.ok(!DEFAULT_CLASSES.includes('prompt'));
});

// --- lanes sort by ATTENTION ------------------------------------------------

test('lanes sort needs-you → busy → idle → gone → chela itself', () => {
    const agents = [
        { window_id: '@2', name: 'cmx-60', status: 'idle', claude_running: true },
        { window_id: '@3', name: 'cmx-61', status: 'busy', claude_running: true },
        { window_id: '@4', name: 'cmx-62', status: 'waiting', claude_running: true },
    ];
    const events = [
        ev('hook.session_start', '@2'), ev('hook.session_start', '@3'),
        ev('hook.permission_request', '@4'),
        ev('finished', '@9', { payload: { window_name: 'cmx-50' } }),   // window is GONE
        ev('daemon_start', null),                                       // chela's own
    ];
    const { lanes } = buildLanes(events, agents, DEFAULT_CLASSES);
    assert.deepEqual(lanes.map(l => l.wid), ['@4', '@3', '@2', '@9', SYSTEM_WID]);
    assert.equal(lanes[0].needsYou, true);
    assert.equal(lanes[0].status, 'waiting');
    // "Who wants me" is answerable without reading a single row.
    assert.deepEqual(lanes.filter(l => l.needsYou).map(l => l.name), ['cmx-62']);
});

test("a dead agent's lane still renders — the lane list comes from the LOG", () => {
    // The window is gone from tmux. Its history must not go with it.
    const events = [ev('run_review', '@9', { payload: { window_name: 'cmx-57' } })];
    const { lanes } = buildLanes(events, [], DEFAULT_CLASSES);
    assert.equal(lanes.length, 1);
    assert.equal(lanes[0].wid, '@9');
    assert.equal(lanes[0].status, 'gone');
    assert.equal(lanes[0].name, 'cmx-57');    // the last name the LOG saw it under
    assert.equal(lanes[0].events.length, 1);
});

test('an unattributed event lands in the chela lane — it is never guessed into an agents', () => {
    const agents = [{ window_id: '@2', name: 'cmx-60', status: 'idle', claude_running: true }];
    const { lanes } = buildLanes([ev('daemon_start', null)], agents, DEFAULT_CLASSES);
    const system = lanes.find(l => l.wid === SYSTEM_WID);
    assert.ok(system, 'no chela-itself lane');
    assert.equal(system.name, SYSTEM_LABEL);
    assert.equal(system.events.length, 1);
    // The only live agent gets nothing pinned on it.
    assert.equal(lanes.find(l => l.wid === '@2').events.length, 0);
});

test('a lane is keyed on the wid, and displays whatever the name is NOW', () => {
    // tmux rename is the source of truth for a name; the log's copy is a snapshot.
    const agents = [{ window_id: '@2', name: 'renamed-live', status: 'busy', claude_running: true }];
    const events = [ev('finished', '@2', { payload: { window_name: 'old-name' } })];
    const { lanes } = buildLanes(events, agents, DEFAULT_CLASSES);
    assert.equal(lanes.length, 1);
    assert.equal(lanes[0].wid, '@2');
    assert.equal(lanes[0].name, 'renamed-live');
});

test('a live agent that has said nothing yet still has a lane (the fleet is the spine)', () => {
    const agents = [{ window_id: '@2', name: 'fresh', status: 'idle', claude_running: true }];
    const { lanes } = buildLanes([], agents, DEFAULT_CLASSES);
    assert.deepEqual(lanes.map(l => l.wid), ['@2']);
    assert.equal(lanes[0].events.length, 0);
});

test('a plain shell window is not an agent lane', () => {
    const agents = [{ window_id: '@5', name: 'shell', claude_running: false }];
    assert.deepEqual(buildLanes([], agents, DEFAULT_CLASSES).lanes, []);
});

// --- nothing is hidden silently ---------------------------------------------

test('the firehose is filtered out by default — and SAYS how many rows that is', () => {
    const events = [
        ev('hook.permission_request', '@2'),
        ...Array.from({ length: 7 }, () => ev('hook.pre_tool_use', '@2')),
        ev('hook.user_prompt_submit', '@2'),
    ];
    const { lanes, hidden } = buildLanes(events, [], DEFAULT_CLASSES);
    const lane = lanes[0];
    assert.equal(lane.events.length, 1);           // just the gate
    assert.equal(lane.total, 9);                   // but it knows about all nine
    assert.equal(lane.hiddenTotal, 8);
    assert.deepEqual(lane.hidden, { tool: 7, prompt: 1 });
    assert.deepEqual(hidden, { tool: 7, prompt: 1 });
    assert.equal(hiddenSummary(lane.hidden), '1 prompt · 7 tool calls');
});

test('turning the firehose chip on shows exactly the rows it was hiding', () => {
    const events = [ev('hook.permission_request', '@2'), ev('hook.pre_tool_use', '@2')];
    const { lanes } = buildLanes(events, [], DEFAULT_CLASSES.concat('tool'));
    assert.equal(lanes[0].events.length, 2);
    assert.equal(lanes[0].hiddenTotal, 0);
});

// --- the graveyard: gone lanes COLLAPSE, they do not disappear ---------------

test('a gone lane is COLLAPSED into the graveyard, not dropped', () => {
    const agents = [{ window_id: '@2', name: 'live', status: 'busy', claude_running: true }];
    const events = [
        ev('hook.session_start', '@2'),
        ev('finished', '@7', { payload: { window_name: 'cmx-58' } }),
        ev('finished', '@8', { payload: { window_name: 'cmx-59' } }),
        ev('daemon_start', null),
    ];
    const { lanes } = buildLanes(events, agents, DEFAULT_CLASSES);
    const group = splitGone(lanes, []);          // nothing awaiting review
    // The default view is the LIVE fleet + chela itself. The corpses are still here —
    // in `buried`, one click away — not deleted.
    assert.deepEqual(group.lanes.map(l => l.wid), ['@2', SYSTEM_WID]);
    assert.deepEqual(group.buried.map(l => l.wid), ['@8', '@7']);   // newest corpse first
    assert.equal(group.agents, 2);
    assert.equal(group.events, 2);
    // Expanding restores them, in the order the sort gave them.
    assert.deepEqual(group.buried.map(l => l.name), ['cmx-59', 'cmx-58']);
    assert.equal(group.buried[0].events.length, 1);
});

test('the collapsed row STATES what it is holding — agents AND events', () => {
    assert.equal(goneSummary(5, 47), '5 finished agents · 47 events');
    assert.equal(goneSummary(1, 1), '1 finished agent · 1 event');
});

test('the graveyard counts EVERY event of a buried lane, filtered or not', () => {
    const events = [
        ev('finished', '@7'),
        ...Array.from({ length: 4 }, () => ev('hook.pre_tool_use', '@7')),
    ];
    const { lanes } = buildLanes(events, [], DEFAULT_CLASSES);
    const group = splitGone(lanes, []);
    assert.equal(group.agents, 1);
    assert.equal(group.events, 5);               // the lane's own header says 5 too
});

test('a gone lane holding an UNANSWERED review is NOT buried (CMX-62)', () => {
    // A dispatched agent kills its own window and only THEN does the run reconcile to
    // awaiting_review: "window gone" is not "finished, ignore me".
    const events = [
        ev('run_review', '@7', { payload: { task_id: 't-open', window_name: 'cmx-63' } }),
        ev('run_review', '@8', { payload: { task_id: 't-merged', window_name: 'cmx-62' } }),
    ];
    const { lanes } = buildLanes(events, [], DEFAULT_CLASSES);
    const group = splitGone(lanes, ['t-open']);
    assert.deepEqual(group.lanes.map(l => l.wid), ['@7']);
    assert.equal(group.lanes[0].openReview, true);      // and it wears the badge
    assert.deepEqual(group.buried.map(l => l.wid), ['@8']);   // merged → the graveyard
});

test('a judge verdict alone (no run_review) still keeps a gone lane open (CMX-197)', () => {
    // A daemon restarted mid-judge sees the verdict BEFORE it ever sees the plain
    // `awaiting_review` edge — that lane must not fall into the graveyard either.
    const events = [ev('run_judge_clean', '@7', { payload: { task_id: 't-open' } })];
    const { lanes } = buildLanes(events, [], DEFAULT_CLASSES);
    const group = splitGone(lanes, ['t-open']);
    assert.deepEqual(group.lanes.map(l => l.wid), ['@7']);
    assert.equal(group.lanes[0].openReview, true);
});

test('the review survives the class filter — turning `run` off must not bury it', () => {
    // The run_review row itself is filtered out of the lane, but the lane still knows it
    // is owed a review: reviewTasks is collected from EVERY event, not from the shown rows.
    const events = [ev('run_review', '@7', { payload: { task_id: 't-open' } })];
    const { lanes } = buildLanes(events, [], ['lifecycle']);
    assert.equal(lanes[0].events.length, 0);
    assert.deepEqual(lanes[0].reviewTasks, ['t-open']);
    assert.deepEqual(splitGone(lanes, ['t-open']).lanes.map(l => l.wid), ['@7']);
});

test('reviews UNKNOWN (the runs read failed) shows them — it never buries them', () => {
    const events = [ev('run_review', '@7', { payload: { task_id: 't-open' } }),
        ev('finished', '@8')];
    const { lanes } = buildLanes(events, [], DEFAULT_CLASSES);
    const group = splitGone(lanes, null);        // null = we do not know
    assert.deepEqual(group.lanes.map(l => l.wid), ['@7']);   // fail loud…
    assert.deepEqual(group.buried.map(l => l.wid), ['@8']);  // …but only for reviews
});

test('a review with no task_id cannot pin the graveyard open forever', () => {
    // Unmatchable against the runs — so it is not claimed as open.
    const events = [ev('run_review', '@7', { payload: {} })];
    const { lanes } = buildLanes(events, [], DEFAULT_CLASSES);
    assert.deepEqual(lanes[0].reviewTasks, []);
    assert.deepEqual(splitGone(lanes, ['t-open']).buried.map(l => l.wid), ['@7']);
});

test('liveness is re-derived per render — a recycled wid never resurrects a corpse', () => {
    // @7 died; tmux later hands @7 to a NEW agent. Nothing persists a "gone" flag, so the
    // lane is whatever the LIVE table says it is now — and the graveyard is empty.
    const events = [ev('finished', '@7', { payload: { window_name: 'cmx-58' } })];
    const dead = splitGone(buildLanes(events, [], DEFAULT_CLASSES).lanes, []);
    assert.deepEqual(dead.buried.map(l => l.wid), ['@7']);

    const live = [{ window_id: '@7', name: 'brand-new', status: 'busy', claude_running: true }];
    const back = splitGone(buildLanes(events, live, DEFAULT_CLASSES).lanes, []);
    assert.equal(back.buried.length, 0);
    assert.equal(back.lanes[0].status, 'busy');
    assert.equal(back.lanes[0].name, 'brand-new');
});

// --- the flat/chronological escape hatch ------------------------------------

test('the flat view is UNAFFECTED by the graveyard — it is the escape hatch', () => {
    // Collapsing the gone lanes is a LANE-view default. Flat is every row, gone or not.
    const events = [ev('finished', '@7'), ev('run_review', '@8'), ev('daemon_start', null)];
    assert.equal(flatRows(events, DEFAULT_CLASSES).length, 3);
});

test('flat mode is the same rows, ungrouped, newest first', () => {
    const events = [ev('finished', '@2'), ev('hook.pre_tool_use', '@3'), ev('run_review', '@4')];
    const rows = flatRows(events, DEFAULT_CLASSES);
    assert.deepEqual(rows.map(r => r.type), ['run_review', 'finished']);   // tool filtered
    assert.ok(rows[0].seq > rows[1].seq);                                  // newest first
    assert.equal(rows[0].cls, 'run');
});

// --- the cursor: a bounded read must RESUME, not skip -------------------------
//
// The Feed's contract with the event log, and the one that can lose data silently:
// resume from `next_seq`, NEVER from `last_seq`. This was previously "asserted" in
// tests/views.test.mjs by grepping feed.js for the string `batch.last_seq` — which
// FAILED the correct code (the drain legitimately reads last_seq to know it has hit
// the tail) and would have PASSED a reader that resumed from `last_seq` under any
// other spelling. A grep tests spelling. These test behaviour, against a fake log
// that implements event_log.read()'s actual semantics.

// event_log.read(), in 8 lines: the `limit` OLDEST events after the cursor, plus the
// cursor (`next_seq`) and the log's tail (`last_seq`) — which DIFFER once truncated.
function fakeLog(n) {
    const all = Array.from({ length: n }, (_, i) => ({ seq: i + 1, type: 'finished', wid: '@1' }));
    const calls = [];
    return {
        calls,
        serve({ after_seq, limit }) {
            calls.push({ after_seq, limit });
            const last_seq = all.length ? all[all.length - 1].seq : 0;
            let out = all.filter(e => after_seq == null || e.seq > after_seq);
            let next_seq = last_seq;
            if (limit != null && out.length > limit) {
                out = out.slice(0, limit);
                next_seq = out[out.length - 1].seq;      // ← the truncation, made resumable
            }
            return Promise.resolve({ boot_id: 'b1', events: out, gap: null, first_seq: 1, last_seq, next_seq });
        },
    };
}

test('a bounded read DRAINS to the tail — every event, exactly once, in order', async () => {
    const log = fakeLog(25);
    const r = await drainLog(log.serve, { cursor: null, boot: null, limit: 10, maxFetches: 8 });
    assert.deepEqual(r.events.map(e => e.seq), Array.from({ length: 25 }, (_, i) => i + 1));
    assert.equal(r.cursor, 25);              // parked at the tail, resumable
    assert.equal(log.calls.length, 3);       // 10 + 10 + 5 — it stops, it does not spin
});

test('a reader that resumed from last_seq would SKIP — this is what the contract buys', async () => {
    // The discriminator. Same fake log, same limit; the ONLY difference is the cursor
    // rule. If this ever stops skipping, the fake log has drifted from event_log.read()
    // and the test above has quietly stopped proving anything.
    const log = fakeLog(25);
    let cursor = null, got = [];
    for (let i = 0; i < 8; i++) {
        const b = await log.serve({ after_seq: cursor, limit: 10 });
        got = got.concat(b.events);
        cursor = b.last_seq;                 // ← the bug, spelled out
        if (!b.events.length) break;
    }
    assert.equal(got.length, 10);            // 15 events silently gone
    assert.ok(!got.some(e => e.seq === 11));

    const ok = await drainLog(log.serve, { cursor: null, boot: null, limit: 10, maxFetches: 8 });
    assert.equal(ok.events.length, 25);      // the drain loses none of them
});

test('the drain is BOUNDED — it yields with a resumable cursor rather than spinning', async () => {
    const log = fakeLog(100);
    const r = await drainLog(log.serve, { cursor: null, boot: null, limit: 10, maxFetches: 3 });
    assert.equal(r.events.length, 30);
    assert.equal(r.cursor, 30);              // the next tick picks up exactly here — no hole
});

test('a GAP is handed up and invalidates what we hold — never swallowed', async () => {
    const gap = { reason: 'the log rotated past your cursor', resume_from_seq: 400 };
    const serve = () => Promise.resolve({
        boot_id: 'b2', events: [{ seq: 401, type: 'finished', wid: '@1' }],
        gap, first_seq: 400, last_seq: 401, next_seq: 401,
    });
    const r = await drainLog(serve, { cursor: 12, boot: 'b1', limit: 10, maxFetches: 8 });
    assert.deepEqual(r.gap, gap);
    assert.equal(r.cleared, true);           // the caller must drop its stale rows
    assert.equal(r.boot, 'b2');              // and re-pin to the boot it actually got
});

test('a failed fetch ends the drain and LEAVES THE CURSOR — the next tick retries', async () => {
    const r = await drainLog(() => Promise.reject(new Error('offline')),
        { cursor: 7, boot: 'b1', limit: 10, maxFetches: 8 });
    assert.deepEqual(r.events, []);
    assert.equal(r.cursor, 7);               // not advanced past events we never saw
    assert.equal(r.gap, null);
});
