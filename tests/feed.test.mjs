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
// Run: node --test tests/  (tests/test_feed_js.py runs this inside pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    CLASSES, DEFAULT_CLASSES, SYSTEM_LABEL, SYSTEM_WID,
    buildLanes, classOf, flatRows, hiddenSummary,
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
    assert.equal(classOf('daemon_start'), 'lifecycle');
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

// --- the flat/chronological escape hatch ------------------------------------

test('flat mode is the same rows, ungrouped, newest first', () => {
    const events = [ev('finished', '@2'), ev('hook.pre_tool_use', '@3'), ev('run_review', '@4')];
    const rows = flatRows(events, DEFAULT_CLASSES);
    assert.deepEqual(rows.map(r => r.type), ['run_review', 'finished']);   // tool filtered
    assert.ok(rows[0].seq > rows[1].seq);                                  // newest first
    assert.equal(rows[0].cls, 'run');
});
