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
    actionBarKind, actionVerb, costView, ctxLevel, focusLayout, isFinished, prChip, rankOrder, recapView, tileState,
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

// docs/AGENT_IDENTITY.md slice 1: a pane chela cannot resolve a status for is
// a distinct fact from one it confirmed is idle. `idle` is a real value
// `claude agents --json` reports (see the test above); `session_status` being
// absent while `claude_running` is true means chela's own join failed — that
// must render as "unknown", never silently degrade to the "idle" glyph/word.
test('tileState: claude running but session_status unresolved reads as unknown, not idle', () => {
    const a = agent({ session_status: null, claude_running: true });
    // 🔴 GUARD: falling through to the idle branch here is exactly the bug —
    // a confidently wrong "idle" reads identically to a real idle claude.
    assert.deepEqual(tileState(a, wantsHuman(a)), { glyph: '?', word: 'unknown', cls: 'unknown' });
});

test('tileState: no claude running at all (never resolved) is plain idle, not unknown', () => {
    const a = agent({ session_status: null, claude_running: false });
    assert.equal(tileState(a, wantsHuman(a)).cls, 'idle');
});

test('tileState: unknown is outranked by wantsHuman, same as every other state', () => {
    const a = agent({ session_status: null, claude_running: true, needs_human: true });
    assert.equal(tileState(a, wantsHuman(a)).cls, 'needs-you');
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

test('rankOrder: an unknown pane (claude running, status unresolved) ranks with idle — auto-arrange is unchanged by slice 1', () => {
    // docs/AGENT_IDENTITY.md: slice 1 only changes what the tile PILL says, not
    // where the pane sits — _rankTier mirrors tileState's precedence but has no
    // unknown-specific tier, so it must fall into the same bucket idle does.
    const busy = agent({ window_id: '@busy', session_status: 'busy' });
    const unknown = agent({ window_id: '@unknown', session_status: null, claude_running: true });
    const done = agent({ window_id: '@done', session_status: 'idle', pr: { url: 'https://x/1' } });
    const list = [done, unknown, busy];
    const wantsByWid = { '@done': false, '@unknown': false, '@busy': false };
    assert.deepEqual(rankOrder(list, wantsByWid), ['@busy', '@unknown', '@done']);
});

// --- focusLayout (Wall redesign: Focus layout toggle) ------------------------

test('focusLayout: the focus pane gets {x:0,y:0,w:8} and the full height', () => {
    const layout = focusLayout(['@a', '@b', '@c'], '@a', 12);
    // 🔴 GUARD: widening the focus pane to w:12 (or any value but 8) while a
    // strip exists breaks this — the strip needs the right 4 columns.
    assert.deepEqual(layout['@a'], { x: 0, y: 0, w: 8, h: 12 });
});

test('focusLayout: strip panes sit at x:8,w:4, stacked top-to-bottom in the given order', () => {
    const layout = focusLayout(['@a', '@b', '@c'], '@a', 12);
    // 🔴 GUARD: moving the strip's x off 8 (e.g. back to 0) would overlap the
    // focus pane instead of sitting beside it.
    assert.equal(layout['@b'].x, 8);
    assert.equal(layout['@b'].w, 4);
    assert.equal(layout['@c'].x, 8);
    assert.equal(layout['@c'].w, 4);
    // Two strip panes, 12 rows total -> 6/6, y increasing, non-overlapping.
    assert.deepEqual(layout['@b'], { x: 8, y: 0, w: 4, h: 6 });
    assert.deepEqual(layout['@c'], { x: 8, y: 6, w: 4, h: 6 });
});

test('focusLayout: strip stacking is non-overlapping and heights sum to the total, last pane eats the remainder', () => {
    // 13 rows / 3 strip panes doesn't divide evenly — pins the exact split
    // _fillNodesByOrder's own math would produce (floor, floor, remainder).
    const layout = focusLayout(['@f', '@1', '@2', '@3'], '@f', 13);
    // 🔴 GUARD: breaking the y-accumulation (e.g. always starting each strip
    // pane at y:0, or using a fixed height instead of accumulating) would
    // make these ranges overlap instead of stacking.
    assert.deepEqual(layout['@1'], { x: 8, y: 0, w: 4, h: 4 });
    assert.deepEqual(layout['@2'], { x: 8, y: 4, w: 4, h: 4 });
    assert.deepEqual(layout['@3'], { x: 8, y: 8, w: 4, h: 5 });   // last eats the remainder
    const total = layout['@1'].h + layout['@2'].h + layout['@3'].h;
    assert.equal(total, 13, 'strip heights must sum to the total row count');
    // Non-overlapping: each pane's y range ends exactly where the next begins.
    assert.equal(layout['@1'].y + layout['@1'].h, layout['@2'].y);
    assert.equal(layout['@2'].y + layout['@2'].h, layout['@3'].y);
});

test('focusLayout: a single pane (focus only, no strip) fills all 12 columns', () => {
    const layout = focusLayout(['@only'], '@only', 10);
    // 🔴 GUARD: leaving w at 8 here (the strip-present width) would waste the
    // right 4 columns when there is nothing to put in a strip.
    assert.deepEqual(layout, { '@only': { x: 0, y: 0, w: 12, h: 10 } });
});

test('focusLayout: focusWid absent from orderedWids falls back to the first wid as focus', () => {
    const layout = focusLayout(['@x', '@y'], '@nonexistent', 10);
    // 🔴 GUARD: dropping the fallback (or falling back to some other wid than
    // the first) would either throw on a stale focus target or focus the
    // wrong pane.
    assert.deepEqual(layout['@x'], { x: 0, y: 0, w: 8, h: 10 });
    assert.deepEqual(layout['@y'], { x: 8, y: 0, w: 4, h: 10 });
});

test('focusLayout: empty orderedWids -> {}', () => {
    assert.deepEqual(focusLayout([], '@a', 10), {});
    assert.deepEqual(focusLayout(null, '@a', 10), {});
});
