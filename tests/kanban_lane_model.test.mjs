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
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { KANBAN_LANES, KANBAN_LANE_LABELS, laneOf } from '../chela/dashboard/static/js/kanbanlanemodel.js';

// kanban.js itself can't be imported here — its import chain (util.js) reads
// `window` at module scope, same reason views.test.mjs and dispatcher.js's own
// comment give for treating it as source, not a module. So the archived card's
// actual pill TEXT — the thing this file was called out for never checking —
// is pinned by parsing the real STATUS_CHIPS object literal out of the source,
// not by a loose substring grep that would pass on a comment or a stale class.
const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static', 'js');
const KANBAN_JS_SRC = readFileSync(join(JS_DIR, 'kanban.js'), 'utf8');

function _extractStatusChipLabels(src) {
    const block = src.match(/const STATUS_CHIPS = \{([\s\S]*?)\n\};/);
    if (!block) throw new Error('STATUS_CHIPS object literal not found in kanban.js — did it move or get renamed?');
    const labels = {};
    const entryRe = /(\w+):\s*\{\s*label:\s*'([^']*)'/g;
    let m;
    while ((m = entryRe.exec(block[1]))) labels[m[1]] = m[2];
    return labels;
}

const STATUS_CHIP_LABELS = _extractStatusChipLabels(KANBAN_JS_SRC);

// The whole status set the Work board currently renders — every status
// _kanbanFlatten (kanban.js) can hand a card, across all 6 lanes.
const ALL_STATUSES = [
    'backlog', 'open', 'claimed', 'running',
    'awaiting_review', 'changes_requested', 'needs_human', 'failed',
    'done', 'closed',
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

test('laneOf: closed -> archived, never done or review (CMX-265)', () => {
    // 🔴 GUARD: a `closed` run (a PR a human closed WITHOUT merging) landing in `done`
    // would mix rejected/superseded work back into the "everything in Done shipped"
    // lane — the exact bug Liav asked to have fixed. Landing in `review` would just
    // reopen the original ghost-in-Review bug this whole ticket started from.
    const lane = laneOf('closed');
    assert.equal(lane, 'archived');
    assert.notEqual(lane, 'done');
    assert.notEqual(lane, 'review');
});

// --- archived/closed must be distinguishable by more than colour (round 3) ---
//
// Liav is red-weak — CMX-230 spent ~30 rounds getting this contract right elsewhere on the
// board, and round 2 of THIS ticket landed a lane + a card pill for `closed` without a single
// assertion on either one's actual text. A CSS class assertion (`st-closed-unmerged`) would
// pass even if the palette were swapped to a second hue with no readable cue at all — these
// check the WORDS a reader (or a screen reader) actually sees.

test('kanban.js: the closed/archived card pill spells the state out in words, not just a glyph', () => {
    const label = STATUS_CHIP_LABELS.closed;
    assert.ok(label, 'STATUS_CHIPS.closed is missing from kanban.js entirely');
    // 🔴 GUARD: a glyph-only pill (e.g. just "⊘") reads as nothing to someone who can't rely
    // on colour to disambiguate it from every other glyph pill on the board.
    assert.match(label, /[a-zA-Z]{4,}/, `"${label}" has no readable word, only a glyph/colour`);
    assert.match(label.toLowerCase(), /closed|archiv/, `"${label}" does not name the archived state`);
});

test('kanban.js: that pill text is rendered as TEXT content, not just used to pick a CSS class', () => {
    // 🔴 GUARD: this is the part a class-only assertion can't see. chipMeta.label must reach
    // the DOM as escaped text (kanban.js's _kCard), not merely select chipMeta.cls — otherwise
    // the guard above could pass on a label string that the renderer never actually shows.
    assert.match(KANBAN_JS_SRC, /\$\{escHtml\(chipMeta\.label\)\}/,
        'the status pill must render chipMeta.label as text content, not only via its cls');
});

test('kanban.js: no OTHER status pill carries the archived wording (negative control)', () => {
    // ⭐ Without this, "put the word 'closed' on every pill" would also satisfy the guard
    // above — this is the one that proves the cue is SPECIFIC to the archived state, i.e.
    // an open or merged row does not carry it.
    const others = Object.entries(STATUS_CHIP_LABELS).filter(([status]) => status !== 'closed');
    assert.ok(others.length >= 6, 'sanity: expected several other status pills to compare against');
    for (const [status, label] of others) {
        assert.doesNotMatch(label.toLowerCase(), /closed|archiv/,
            `"${status}" pill unexpectedly carries the archived wording: "${label}"`);
    }
});

test('lane label: archived lane says "Archived" in words, distinct from "Done"', () => {
    assert.equal(KANBAN_LANE_LABELS.archived, 'Archived');
    // 🔴 GUARD: a hue-only "Archived" lane rendered with Done's exact label text would make
    // the two lanes indistinguishable to a reader going by the words alone.
    assert.notEqual(KANBAN_LANE_LABELS.archived, KANBAN_LANE_LABELS.done);
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

test('KANBAN_LANES has exactly the 6 lanes, in order', () => {
    assert.deepEqual(KANBAN_LANES, ['backlog', 'todo', 'in_progress', 'review', 'done', 'archived']);
});

test('every lane key has a label', () => {
    for (const key of KANBAN_LANES) {
        assert.equal(typeof KANBAN_LANE_LABELS[key], 'string');
        assert.ok(KANBAN_LANE_LABELS[key].length > 0, `lane "${key}" has no label`);
    }
});

test('completeness: every known status resolves to one of the 6 lane keys, none undefined', () => {
    // 🔴 GUARD: deleting a status's entry from STATUS_LANE inside
    // kanbanlanemodel.js does NOT throw — laneOf's `|| 'review'` fallback
    // would quietly swallow it into Review. That is an ACCEPTABLE fallback
    // for a genuinely unknown status (see the test above), but this test
    // pins the full set of 10 KNOWN statuses landing on their real, specific
    // lanes — it would only catch a delete if paired with the per-status
    // assertions above going RED too, which is why both exist.
    for (const status of ALL_STATUSES) {
        const lane = laneOf(status);
        assert.ok(KANBAN_LANES.includes(lane), `laneOf(${status}) returned "${lane}", not a real lane key`);
    }
});

test('completeness: every one of the 6 lane keys is actually reachable from the known status set', () => {
    // 🔴 GUARD: deleting a LANE (e.g. dropping 'review' from KANBAN_LANES
    // while STATUS_LANE still points statuses at it) would leave a lane no
    // status can ever land in, or a lane KANBAN_LANES doesn't know about —
    // either way the board silently loses a lane. This checks the mapping is
    // onto, not just well-typed.
    const reached = new Set(ALL_STATUSES.map(laneOf));
    for (const key of KANBAN_LANES) {
        assert.ok(reached.has(key), `lane "${key}" is unreachable — no known status maps to it`);
    }
    assert.equal(reached.size, KANBAN_LANES.length, 'every lane key is reachable, and nothing besides the 6 lane keys is produced');
});
