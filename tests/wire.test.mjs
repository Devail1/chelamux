// THE WIRE — the Wall's room gesture, proved without a browser.
//
// The properties that decide whether this feature is safe to ship:
//
//   1. A DROP THAT MEANS NOTHING CREATES NOTHING — on the source tile itself, on
//      empty stage, or on a tile with no wid. A "room of one" is a relationship
//      with nobody in it.
//   2. 🔴 A ROOM CHANGE DOES NOT RELOAD THE TERMINALS. This is THE regression test.
//      The wall's render loop early-returns on `_termSig`; if room state ever
//      reached that signature, `buildWall` would `innerHTML =` the stage and every
//      live terminal in the fleet would reload because two agents started talking.
//      So: the iframe NODES must be identity-preserved across a room update (same
//      object, same `src`), and the rooms path in terminals.js must not touch
//      `_termSig` / `buildWall` / `innerHTML` at all.
//   3. THE ACCENT IS NEVER HUE-ONLY (the primary user is red-weak): the badge
//      carries the room's own NAME and a glyph.
//   4. The API's shape is `rooms.status()`'s — keyed by wid, which is what the
//      Wall has (tests/test_api_rooms.py holds the other end of that contract).
//
// Run: node --test tests/  (tests/test_js_suites.py runs every .test.mjs in pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { applyRoomAccents, bezierPath, resolveDrop, roomsByWid } from '../chela/dashboard/static/js/wire.js';

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static', 'js');
const src = f => readFileSync(join(JS_DIR, f), 'utf8');

// --- a DOM small enough to read, big enough for a wall of tiles ---------------
// applyRoomAccents does four things and no more: toggle a class, set a CSS var,
// write a badge, and leave everything else — including the iframes — alone. This
// shim implements exactly the surface it uses, so "it left the iframe alone" is
// an assertion about object identity, not about a mock.
class El {
    constructor(cls = '', attrs = {}) {
        this.classes = new Set(cls.split(' ').filter(Boolean));
        this.attrs = { ...attrs };
        this.children = [];
        this.styles = {};
        this.textContent = '';
        this.hidden = false;
        this.classList = {
            toggle: (c, on) => (on ? this.classes.add(c) : this.classes.delete(c)),
            contains: c => this.classes.has(c),
        };
        this.style = {
            setProperty: (k, v) => { this.styles[k] = v; },
            removeProperty: k => { delete this.styles[k]; },
        };
    }
    add(child) { this.children.push(child); return child; }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    setAttribute(k, v) { this.attrs[k] = v; }
    removeAttribute(k) { delete this.attrs[k]; }
    // Only the two selector shapes wire.js uses: '.cls' and '.cls[attr]'.
    matches(sel) {
        const m = /^\.([\w-]+)(?:\[([\w-]+)\])?$/.exec(sel);
        if (!m) throw new Error(`shim: unsupported selector ${sel}`);
        return this.classes.has(m[1]) && (!m[2] || this.getAttribute(m[2]) !== null);
    }
    descendants() { return this.children.flatMap(c => [c, ...c.descendants()]); }
    querySelectorAll(sel) { return this.descendants().filter(e => e.matches(sel)); }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

// A wall: two tiles, each with a header badge and a LIVE iframe we must not touch.
function fakeWall(wids) {
    const stage = new El('term-stage');
    const tiles = {};
    wids.forEach(wid => {
        const item = stage.add(new El('grid-stack-item', { 'gs-id': wid }));
        const content = item.add(new El('grid-stack-item-content'));
        const badge = content.add(new El('gs-room'));
        badge.hidden = true;
        const frame = content.add(new El('term-frame'));
        frame.setAttribute('src', `/term/${wid}/`);
        tiles[wid] = { item, content, badge, frame };
    });
    return { stage, tiles };
}

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

test('a member whose window is GONE is simply not painted — no tile, no crash', () => {
    const { stage, tiles } = fakeWall(['@1']);
    applyRoomAccents(stage, STATUS(['wire-a-b', ['@1', '@ghost']]));
    assert.ok(tiles['@1'].content.classList.contains('in-room'));
});

test('the accent is never hue-only: the badge carries the room name and a glyph', () => {
    const { stage, tiles } = fakeWall(['@1', '@2']);
    applyRoomAccents(stage, STATUS(['wire-a-b', ['@1', '@2']]));
    for (const wid of ['@1', '@2']) {
        const { content, badge } = tiles[wid];
        assert.ok(content.classList.contains('in-room'));
        assert.match(badge.textContent, /🔌 wire-a-b/);        // the NON-HUE cue
        assert.equal(badge.getAttribute('data-room'), 'wire-a-b');
        assert.equal(badge.hidden, false);
        assert.ok(content.styles['--room-accent'], 'and a colour, as the second signal');
    }
    // Both tiles wear the SAME accent — that is what "shared border" means.
    assert.equal(tiles['@1'].content.styles['--room-accent'], tiles['@2'].content.styles['--room-accent']);
});

test('leaving a room clears the tile — accent, badge and all', () => {
    const { stage, tiles } = fakeWall(['@1', '@2']);
    applyRoomAccents(stage, STATUS(['wire-a-b', ['@1', '@2']]));
    applyRoomAccents(stage, STATUS(['wire-a-b', ['@2']]));     // @1 left
    assert.equal(tiles['@1'].content.classList.contains('in-room'), false);
    assert.equal(tiles['@1'].badge.hidden, true);
    assert.equal(tiles['@1'].content.styles['--room-accent'], undefined);
    assert.ok(tiles['@2'].content.classList.contains('in-room'));
});

// --- 4. 🔴 THE REGRESSION TEST: a room change must not reload the terminals ----

test('a room change is identity-preserving — the live iframes are NOT recreated', () => {
    const { stage, tiles } = fakeWall(['@1', '@2', '@3']);
    const before = ['@1', '@2', '@3'].map(w => tiles[w].frame);
    const srcs = before.map(f => f.getAttribute('src'));

    applyRoomAccents(stage, STATUS(['wire-a-b', ['@1', '@2']]));   // wired
    applyRoomAccents(stage, STATUS(['wire-a-b', ['@1', '@2', '@3']]));   // a third joins
    applyRoomAccents(stage, { rooms: {} });                        // and everyone leaves

    const after = ['@1', '@2', '@3'].map(w => stage.querySelectorAll('.grid-stack-item[gs-id]')
        .find(it => it.getAttribute('gs-id') === w).querySelector('.term-frame'));
    after.forEach((frame, i) => {
        assert.equal(frame, before[i], 'the iframe is the SAME NODE — it was never rebuilt');
        assert.equal(frame.getAttribute('src'), srcs[i], 'and its src was never rewritten');
    });
});

test('the rooms path in terminals.js never touches _termSig, buildWall or innerHTML', () => {
    // Structural, because the bug is structural: room state that reaches the render
    // signature reloads the whole fleet. Read the rooms functions, and ONLY those.
    const text = src('terminals.js');
    const fns = ['_refreshRooms', '_replayRoomAccents', 'wireRoomClick', '_wireDrop'];
    for (const fn of fns) {
        const start = text.indexOf(`function ${fn}(`);
        assert.ok(start > 0, `${fn} not found in terminals.js`);
        const body = text.slice(start, text.indexOf('\n}\n', start));
        for (const forbidden of ['_termSig', 'buildWall', 'innerHTML', 'renderTerminals']) {
            assert.ok(!body.includes(forbidden),
                `${fn} references ${forbidden} — a room change must not rebuild the wall`);
        }
    }
});

test('the wire drag reuses .gs-dragging (mouse-through) and NOT .term-dragging (hides the terminals)', () => {
    const text = src('terminals.js');
    const start = text.indexOf('function wireDragStart(');
    const body = text.slice(start, text.indexOf('\n}\n', start));
    assert.ok(body.includes("classList.add('gs-dragging')"), 'the iframes must stop eating the mouse');
    assert.ok(!body.includes('term-dragging'), 'the wire must not hide the terminals you are wiring');
    assert.ok(body.includes('stopPropagation'), 'or gridstack steals the gesture as a tile drag');
});
