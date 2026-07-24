// THE WALL TILE MODEL — pure status-card math (wallmodel.js), the Wall
// redesign's slice 1 (docs/wall-redesign.md, "the redesigned tile"). No DOM,
// no fetch: every property here is a straight function-of-inputs check, each
// written to go RED under one specific corruption of the real logic (a guard
// that survives its own corruption is decoration, not a guard).
//
// wallmodel.js's functions take `wants` (= util.js's wantsHuman(agent)) as an
// explicit parameter rather than computing it themselves (see wallmodel.js's
// header comment for why) — these tests compute it the same way terminals.js
// does at the real call site: `needs_human === true || session_status ===
// 'waiting'`, i.e. util.js's wantsHuman verbatim, kept local to this file only
// because util.js cannot load under plain Node (no DOM).
//
// Run: node --test tests/wall_tile.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    actionBarKind, actionVerb, costView, ctxLevel, isFinished, prChip, rankOrder, recapView, tileState,
} from '../chela/dashboard/static/js/wallmodel.js';

const agent = (over = {}) => ({
    window_id: '@1', name: 'w1', session_status: null, needs_human: false,
    dispatched: false, pr: null, recap: null, recap_ts: null, ...over,
});

// Mirrors util.js's wantsHuman(a) exactly — see the header comment above.
const wantsHuman = a => !!a && (a.needs_human === true || a.session_status === 'waiting');

// --- tileState / isFinished --------------------------------------------------

test('tileState: wantsHuman OUTRANKS session_status — a busy AND blocked pane is needs-you, not working', () => {
    const a = agent({ session_status: 'busy', needs_human: true });
    // 🔴 GUARD: this is exactly the dispatched-permission-gate case (app.py's
    // _needs_human docstring) — claude's own view stays "busy" while a Bash/
    // Edit gate blocks the pane. Checking session_status === 'busy' BEFORE
    // `wants` would misclassify this as "working" and hide the gate.
    assert.equal(tileState(a, wantsHuman(a)).cls, 'needs-you');
    assert.equal(tileState(a, wantsHuman(a)).word, 'needs you');
    assert.equal(tileState(a, wantsHuman(a)).glyph, '◆');
});

test('tileState: waiting (session_status) also reads as needs-you', () => {
    const a = agent({ session_status: 'waiting' });
    assert.equal(tileState(a, wantsHuman(a)).cls, 'needs-you');
});

test('tileState: busy and not blocked reads as working', () => {
    const a = agent({ session_status: 'busy' });
    assert.deepEqual(tileState(a, wantsHuman(a)), { glyph: '●', word: 'working', cls: 'working' });
});

test('tileState: idle with an open PR and not busy/blocked reads as done', () => {
    const a = agent({ session_status: 'idle', pr: { url: 'https://x/1', number: 14 } });
    assert.deepEqual(tileState(a, wantsHuman(a)), { glyph: '✓', word: 'done', cls: 'done' });
});

test('tileState: idle with no PR is plain idle, not done', () => {
    const a = agent({ session_status: 'idle' });
    assert.equal(tileState(a, wantsHuman(a)).cls, 'idle');
});

test('isFinished: a busy pane with a PR is NOT finished — still working, PR is stale/in-flight', () => {
    // 🔴 GUARD: dropping the session_status !== 'busy' check would flip an
    // actively-working pane (that already opened a draft PR) to "done".
    const a = agent({ session_status: 'busy', pr: { url: 'https://x/1' } });
    assert.equal(isFinished(a, wantsHuman(a)), false);
});

test('isFinished: a blocked pane with a PR is NOT finished — it wants a human first', () => {
    const a = agent({ needs_human: true, pr: { url: 'https://x/1' } });
    assert.equal(isFinished(a, wantsHuman(a)), false);
});

test('isFinished: null/undefined agent never throws, reads as not finished', () => {
    assert.equal(isFinished(null, false), false);
    assert.equal(isFinished(undefined, false), false);
});

// --- action-bar verb / kind --------------------------------------------------

test('actionVerb: session_status "waiting" is a QUESTION -> Answer', () => {
    const a = agent({ session_status: 'waiting' });
    assert.equal(actionVerb(a, wantsHuman(a)), 'Answer');
});

test('actionVerb: needs_human true while session_status stays busy is a PERMISSION GATE -> Approve', () => {
    // 🔴 GUARD: swapping the branches (Answer/Approve) here silently relabels
    // every gate on the Wall with the wrong verb — the assert pins the exact
    // string per case, so a swap fails both this test and the one above.
    const a = agent({ session_status: 'busy', needs_human: true });
    assert.equal(actionVerb(a, wantsHuman(a)), 'Approve');
});

test('actionVerb: a calm pane (no wantsHuman) gets no verb at all', () => {
    const idle = agent({ session_status: 'idle' });
    const busy = agent({ session_status: 'busy' });
    assert.equal(actionVerb(idle, wantsHuman(idle)), null);
    assert.equal(actionVerb(busy, wantsHuman(busy)), null);
});

test('actionBarKind: a done pane (no wantsHuman, finished) is a Review bar', () => {
    const a = agent({ session_status: 'idle', pr: { url: 'https://x/1', number: 9 } });
    assert.deepEqual(actionBarKind(a, wantsHuman(a)), { kind: 'review', label: 'Review' });
});

test('actionBarKind: a working pane with no PR gets no action bar', () => {
    const a = agent({ session_status: 'busy' });
    assert.equal(actionBarKind(a, wantsHuman(a)), null);
});

// --- context level -----------------------------------------------------------

test('ctxLevel: thresholds are STRICTLY greater-than, at exactly 60 and 80', () => {
    assert.equal(ctxLevel(60), 'ok', '60 itself is still ok');
    // 🔴 GUARD: flipping `>` to `>=` on the 60 threshold makes this fail.
    assert.equal(ctxLevel(61), 'warn');
    assert.equal(ctxLevel(80), 'warn', '80 itself is still warn, not bad');
    // 🔴 GUARD: flipping `>` to `>=` on the 80 threshold makes this fail.
    assert.equal(ctxLevel(81), 'bad');
});

test('ctxLevel: a null/undefined pct (no context data) reads as ok, never bad', () => {
    assert.equal(ctxLevel(null), 'ok');
    assert.equal(ctxLevel(undefined), 'ok');
});

// --- null-field hidden-sentinel helpers ---------------------------------

test('recapView: no recap -> exactly null, never a stringified "null"', () => {
    assert.strictEqual(recapView(agent()), null);
    assert.strictEqual(recapView(agent({ recap: '' })), null);
    assert.strictEqual(recapView(null), null);
});

test('recapView: a real recap carries the text and the raw ts for the caller to format', () => {
    const v = recapView(agent({ recap: 'shipped the thing', recap_ts: '2026-07-24T00:00:00Z' }));
    assert.deepEqual(v, { text: 'shipped the thing', tsTitle: '2026-07-24T00:00:00Z' });
});

test('prChip: no pr, or a pr with no url -> exactly null', () => {
    assert.strictEqual(prChip(null), null);
    assert.strictEqual(prChip({}), null);
    // 🔴 GUARD: a pr object present but missing `url` must still hide — a
    // chip with no link target is worse than no chip.
    assert.strictEqual(prChip({ number: 14 }), null);
});

test('prChip: a real pr renders "#<number>", not a fabricated state word', () => {
    // chela/transcripts.py's PRLink.to_dict has no state/status field — the
    // chip must not invent one.
    const v = prChip({ url: 'https://x/1', number: 14, repository: 'chela' });
    assert.equal(v.label, '#14');
    assert.equal(v.url, 'https://x/1');
});

test('prChip: a pr with a url but no number falls back to bare "PR"', () => {
    assert.equal(prChip({ url: 'https://x/1' }).label, 'PR');
});

test('costView: null/undefined cost -> exactly null (hidden)', () => {
    assert.strictEqual(costView(null), null);
    assert.strictEqual(costView(undefined), null);
});

test('costView: a real cost of exactly $0.00 must NOT hide — 0 is a value, not "missing"', () => {
    // 🔴 GUARD: a naive `if (!costUsd) return null` (falsy check instead of
    // `== null`) would wrongly hide a genuine free session.
    assert.equal(costView(0), '$0.00');
});

test('costView: formats to two decimals like cost.js\'s _fmtCost', () => {
    assert.equal(costView(1.5), '$1.50');
    assert.equal(costView(0.004), '$0.00');
});

// --- rankOrder (Wall redesign slice 2: auto-arrange) -------------------------

test('rankOrder: needs-you (0) -> busy/working (1) -> idle (2) -> done (3), across all four tiers', () => {
    // Deliberately shuffled input (done, idle, busy, needs-you) — the assert
    // pins the CANONICAL order, so it only passes if every tier is read in the
    // right place.
    const done = agent({ window_id: '@done', session_status: 'idle', pr: { url: 'https://x/1' } });
    const idle = agent({ window_id: '@idle', session_status: 'idle' });
    const busy = agent({ window_id: '@busy', session_status: 'busy' });
    const needsYou = agent({ window_id: '@needs', needs_human: true });
    const list = [done, idle, busy, needsYou];
    const wantsByWid = { '@done': false, '@idle': false, '@busy': false, '@needs': true };
    // 🔴 GUARD: swapping any two rank tiers (e.g. idle sorting before busy, or
    // done sorting before idle) flips this exact order.
    assert.deepEqual(rankOrder(list, wantsByWid), ['@needs', '@busy', '@idle', '@done']);
});

test('rankOrder: wantsHuman OUTRANKS busy — a gated-but-busy pane ranks with needs-you, not working', () => {
    // Mirrors tileState's load-bearing precedence (wallmodel.js's header
    // comment): a dispatched pane sitting at a permission gate still reports
    // session_status 'busy'. Checking 'busy' before `wants` would misrank a
    // blocked pane as merely "working" and bury it behind calmer panes.
    const gated = agent({ window_id: '@gated', session_status: 'busy', needs_human: true });
    const busy = agent({ window_id: '@busy', session_status: 'busy' });
    const list = [busy, gated];
    const wantsByWid = { '@busy': false, '@gated': true };
    // 🔴 GUARD: checking session_status === 'busy' before `wants` puts @busy
    // first instead of @gated.
    assert.deepEqual(rankOrder(list, wantsByWid), ['@gated', '@busy']);
});

test('rankOrder: stable within a rank — same-rank panes keep their input order, never re-sorted by wid', () => {
    const b1 = agent({ window_id: '@b1', session_status: 'busy' });
    const b2 = agent({ window_id: '@b2', session_status: 'busy' });
    const b3 = agent({ window_id: '@b3', session_status: 'busy' });
    const list = [b3, b1, b2];   // deliberately not alphabetical
    const wantsByWid = { '@b1': false, '@b2': false, '@b3': false };
    // 🔴 GUARD: a non-stable sort (e.g. re-sorting ties by wid, or dropping the
    // `i` tiebreak in favour of relying on engine sort stability) would reorder
    // this to ['@b1','@b2','@b3'] instead of preserving the input sequence.
    assert.deepEqual(rankOrder(list, wantsByWid), ['@b3', '@b1', '@b2']);
});

test('rankOrder: all-same-rank input (idle tier) comes back in the exact input order', () => {
    const a1 = agent({ window_id: '@a1', session_status: 'idle' });
    const a2 = agent({ window_id: '@a2', session_status: 'idle' });
    const a3 = agent({ window_id: '@a3', session_status: 'idle' });
    const list = [a2, a3, a1];
    const wantsByWid = { '@a1': false, '@a2': false, '@a3': false };
    assert.deepEqual(rankOrder(list, wantsByWid), ['@a2', '@a3', '@a1']);
});

test('rankOrder: empty input -> []', () => {
    assert.deepEqual(rankOrder([], {}), []);
    assert.deepEqual(rankOrder(null, {}), []);
});
