// THE WIRE, THE PURE HALF — the parts of the Wall's room gesture that are decided by
// arithmetic, not by a DOM: what a drop MEANS, where the cable is DRAWN, and how
// `/api/rooms` is indexed for the tiles.
//
//   1. A DROP THAT MEANS NOTHING CREATES NOTHING — on the source tile itself, on
//      empty stage, or on a tile with no wid. A "room of one" is a relationship
//      with nobody in it.
//   2. The API's shape is `rooms.status()`'s — keyed by wid, which is what the Wall
//      has (tests/test_api_rooms.py holds the other end of that contract).
//
// EVERYTHING THAT TOUCHES THE DOM IS IN tests/wall.test.mjs, in a real one (jsdom),
// running the real `buildWall`. It used to be here, against a hand-written `El` shim,
// and that was theatre: the shim implemented neither `innerHTML` nor a live `src` —
// the only two mechanisms that reload a terminal — so the regression test written to
// catch a reload could not have failed if the code had started reloading. The same
// went for its companion, a grep over the source text asserting the rooms path never
// says `buildWall`: non-transitive (one `_repaint()` helper in between defeats it),
// and its body-slice ran to the first `\n}\n`, so a reformat would have truncated the
// body to '' — and ''.includes(x) is false, i.e. a grep matching NO TEXT read GREEN.
//
// Run: node --test tests/  (tests/test_js_suites.py runs every .test.mjs in pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bezierPath, resolveDrop, roomsByWid } from '../chela/dashboard/static/js/wire.js';

const STATUS = (...pairs) => ({
    rooms: Object.fromEntries(pairs.map(([room, wids]) => [room, {
        created: 1, members: Object.fromEntries(wids.map(w => [w, { name: w, live: true }])),
    }])),
    pending: [],
});

// --- 1. the drop rules --------------------------------------------------------

test('a drop on a peer is the only drop that makes a room', () => {
    assert.deepEqual(resolveDrop('@1', '@2'), { ok: true, wids: ['@1', '@2'] });
});

test('a drop on the SOURCE tile creates nothing — a room of one is not a room', () => {
    assert.equal(resolveDrop('@1', '@1').ok, false);
    assert.equal(resolveDrop('@1', '@1').reason, 'self');
});

test('a drop on empty stage, or on a tile with no wid, creates nothing', () => {
    assert.equal(resolveDrop('@1', null).ok, false);        // elementFromPoint found no tile
    assert.equal(resolveDrop('@1', '').ok, false);          // a tile without a gs-id
    assert.equal(resolveDrop(null, '@2').ok, false);        // no source (defensive)
});

// --- 2. the geometry ----------------------------------------------------------

test('the bezier starts at the port and ends at the cursor', () => {
    const d = bezierPath(10, 20, 300, 240);
    assert.match(d, /^M 10 20 C /);
    assert.ok(d.endsWith('300 240'), d);
});

test('the bezier leaves the port horizontally, whichever way the wire runs', () => {
    // Control points share the endpoints' y — a patch cable, not a diagonal.
    const right = /^M 0 0 C ([\d.-]+) 0, ([\d.-]+) 100, 200 100$/.exec(bezierPath(0, 0, 200, 100));
    assert.ok(right, 'rightward wire');
    assert.ok(Number(right[1]) > 0 && Number(right[2]) < 200, 'control points bow outward');
    const left = /^M 200 0 C ([\d.-]+) 0, ([\d.-]+) 100, 0 100$/.exec(bezierPath(200, 0, 0, 100));
    assert.ok(left, 'leftward wire');
    assert.ok(Number(left[1]) < 200 && Number(left[2]) > 0, 'and mirrors when it runs left');
});

test('the wire has slack even when the two ports are on top of each other', () => {
    const d = bezierPath(50, 50, 50, 400);
    assert.ok(!d.includes('NaN'), d);
    assert.match(d, /C 74 50, 26 400, 50 400/);   // clamped slack, never a zero-length curve
});

// --- 3. the read: /api/rooms -> per-tile state --------------------------------

test('rooms are indexed by wid — which is what the Wall has', () => {
    const by = roomsByWid(STATUS(['wire-a-b', ['@1', '@2']], ['ops', ['@2', '@3']]));
    assert.deepEqual(by, { '@1': ['wire-a-b'], '@2': ['ops', 'wire-a-b'], '@3': ['ops'] });
});

test('an empty payload leaves every tile roomless (and does not throw)', () => {
    assert.deepEqual(roomsByWid({ rooms: {} }), {});
    assert.deepEqual(roomsByWid(null), {});
});
