// THE DECISIONS UNREAD BADGE — pure seq/urgency arithmetic (decisionsmodel.js).
// No DOM, no fetch: every property here is a straight function-of-inputs check,
// each written to go RED under one specific corruption of the real logic (a
// guard that survives its own corruption is decoration, not a guard).
//
// Run: node --test tests/decisions_badge.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    formatUnreadCount, seedLastSeen, unreadCount, unreadUrgency,
} from '../chela/dashboard/static/js/decisionsmodel.js';

const ev = (seq, type = 'finished') => ({ seq, type, ts: seq, wid: '@1', summary: '' });

// --- unread count -----------------------------------------------------------

test('unread count is events with seq STRICTLY greater than lastSeenSeq', () => {
    const events = [ev(1), ev(2), ev(3), ev(4)];
    assert.equal(unreadCount(events, 2), 2, 'seq 3 and 4 are unseen');
    // 🔴 GUARD: the event AT lastSeenSeq must not count as unread — the boundary
    // is exclusive. Flipping the model's `>` to `>=` makes this assert fail.
    assert.equal(unreadCount(events, 4), 0, 'the just-seen event must not re-count as unread');
});

test('unread count is 0 (hidden) when nothing exceeds lastSeenSeq', () => {
    assert.equal(unreadCount([ev(1), ev(2)], 5), 0);
    assert.equal(formatUnreadCount(0), '', 'a zero count renders no label — the badge hides');
});

test('the rendered label caps at 99+, never an uncapped number', () => {
    assert.equal(formatUnreadCount(1), '1');
    assert.equal(formatUnreadCount(99), '99');
    assert.equal(formatUnreadCount(100), '99+');
    assert.equal(formatUnreadCount(4321), '99+');
});

// --- first-load seeding -------------------------------------------------------

test('first-ever load seeds lastSeen to the CURRENT max held seq, not 0', () => {
    const events = [ev(1), ev(2), ev(3)];
    const seeded = seedLastSeen(events);
    assert.equal(seeded, 3, 'must seed to the max seq in the held backlog');
    // 🔴 GUARD: seeding to 0 (the naive "nothing seen yet" reading) badges the
    // entire historical backlog as unread on a browser that has never opened
    // the popover — exactly the bug the review called out.
    assert.equal(unreadCount(events, seeded), 0, 'a freshly seeded cursor must show zero unread');
    assert.notEqual(seeded, 0, 'seeding to 0 would badge the whole backlog as unread');
});

test('seeding an empty backlog yields 0, and 0 unread stays 0 (no false badge)', () => {
    assert.equal(seedLastSeen([]), 0);
    assert.equal(unreadCount([], 0), 0);
});

// --- on-open advances the cursor (persistence is decisions.js's job; this is
// the arithmetic that persistence sits on top of) ----------------------------

test('advancing lastSeen to the max held seq clears unread, and a later event re-badges', () => {
    const held = [ev(1), ev(2), ev(3)];
    const openedAt = Math.max(...held.map(e => e.seq));   // what "open the popover" computes
    assert.equal(unreadCount(held, openedAt), 0, 'opening must clear every currently-held event');

    // A new decision lands after the popover was opened — it must re-badge.
    const withNew = held.concat([ev(4, 'run_failed')]);
    assert.equal(unreadCount(withNew, openedAt), 1, 'an event after the last-seen cursor must count as unread');
});

// --- urgency colour -----------------------------------------------------------

test('any unseen bad/warn-class event drives the badge to that urgency, not neutral', () => {
    const bad = [ev(1, 'finished'), ev(2, 'run_failed')];
    assert.equal(unreadUrgency(bad, 0), 'bad', 'an unseen run_failed must mark the badge bad');

    const warn = [ev(1, 'finished'), ev(2, 'run_review')];
    assert.equal(unreadUrgency(warn, 0), 'warn', 'an unseen run_review must mark the badge warn');

    const neutral = [ev(1, 'finished'), ev(2, 'completed_gone')];
    assert.equal(unreadUrgency(neutral, 0), 'neutral', 'no bad/warn unseen events must stay neutral');
});

test('bad outranks warn when both are present among the unseen set', () => {
    const mixed = [ev(1, 'run_review'), ev(2, 'run_failed')];
    assert.equal(unreadUrgency(mixed, 0), 'bad');
});

test('urgency ignores events the cursor has already passed', () => {
    const events = [ev(1, 'run_failed'), ev(2, 'finished')];
    // 🔴 GUARD: urgency must be computed over the UNSEEN set only — inverting
    // this (scanning all held events regardless of lastSeenSeq) would keep the
    // badge red forever, even after the bad event was already seen.
    assert.equal(unreadUrgency(events, 1), 'neutral', 'a seen run_failed must not still colour the badge bad');
});
