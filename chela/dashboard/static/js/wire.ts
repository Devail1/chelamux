// ---------------------------------------------------------------------------
// THE WIRE — the pure half of the Wall's room gesture. No fetch, no globals.
//
// Drag a line from one tile's port onto another tile and the two agents are in a
// room (chela/rooms.py — the relationship already exists; this is only its UI).
// Everything here is either pure geometry or a surgical, per-tile DOM patch, and
// that split is load-bearing:
//
//   1. THE GEOMETRY IS PURE, so the bezier and the drop rules are provable in
//      node (`tests/wire.test.mjs`) without a browser — feedmodel.js's precedent.
//   2. THE ACCENT IS A PATCH, NEVER A RENDER. Room membership is presentational
//      per-tile state. It must NEVER reach `_termSig` (terminals.js): a changed
//      signature makes `buildWall` re-`innerHTML` the stage, which destroys and
//      recreates EVERY iframe — i.e. every live terminal in the fleet reloads
//      because two agents started talking. So `applyRoomAccents` only ever
//      toggles a class, sets a CSS var and writes a badge's text; it never
//      touches an iframe. `tests/wall.test.mjs` holds that line in a REAL DOM:
//      it runs the real `buildWall`, does a real room update, and asserts the
//      iframe NODES are the same objects, with no write to `src` — put an
//      `innerHTML =` or a `frame.src =` in here and it goes red. (It is a real
//      DOM precisely because the fake one it replaced implemented neither, and
//      so could not have failed.)
//
// The accent is deliberately NOT hue-only (the primary user is red-weak): the
// colour rides along with a badge carrying the room's own name beside a plug icon (drawn
// once by the tile's markup, terminals.js), so "these two tiles are wired together" is
// readable with no colour vision at all.
//
// CMX-236 (CMX-212 P22): TypeScript, converted in place.
// ---------------------------------------------------------------------------

import { colorForId } from '../collab/presence-core';

// `/api/rooms`' shape (chela/rooms.py `status()`); only the members' keys are read here.
export interface RoomsStatus {
    rooms?: Record<string, { members?: Record<string, unknown> | null } | null> | null;
}

export type Drop = { ok: false; reason: 'no-target' | 'self' } | { ok: true; wids: [string, string] };

// The port a wire leaves from / the socket it lands on.
export const PORT_GLYPH = '○';
export const SOCKET_GLYPH = '◉';

// A cubic bezier from the source port to the cursor, with horizontal control
// points (patch-cable feel: it leaves the port sideways, lands sideways). The
// slack scales with the horizontal run so a short wire doesn't loop absurdly.
export function bezierPath(x1: number, y1: number, x2: number, y2: number): string {
    const slack = Math.max(24, Math.min(160, Math.abs(x2 - x1) * 0.5));
    const dir = x2 >= x1 ? 1 : -1;
    const c1x = x1 + slack * dir, c2x = x2 - slack * dir;
    return `M ${r(x1)} ${r(y1)} C ${r(c1x)} ${r(y1)}, ${r(c2x)} ${r(y2)}, ${r(x2)} ${r(y2)}`;
}
const r = (n: number) => Math.round(n * 100) / 100;

// What a drop MEANS. A drop on the source itself, on empty stage, or on a tile
// with no wid is a CANCEL — never a room of one. (The whole point of the gesture
// is a relationship; a self-room is a loop with nobody in it.)
export function resolveDrop(fromWid: string | null | undefined, toWid: string | null | undefined): Drop {
    if (!fromWid || !toWid) return { ok: false, reason: 'no-target' };
    if (fromWid === toWid) return { ok: false, reason: 'self' };
    return { ok: true, wids: [fromWid, toWid] };
}

// `/api/rooms` (= `rooms.status()`, = `chela room status`) -> wid -> [room, ...].
// A member whose window is gone still appears in the payload; the wall simply has
// no tile for it, and applyRoomAccents only paints tiles it finds.
export function roomsByWid(status: RoomsStatus | null | undefined): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    const rooms = (status && status.rooms) || {};
    Object.keys(rooms).sort().forEach(room => {
        const members = (rooms[room] && rooms[room].members) || {};
        Object.keys(members).forEach(wid => {
            (out[wid] = out[wid] || []).push(room);
        });
    });
    return out;
}

// Stable per-room colour — the same Okabe-Ito, red-weak-safe palette the presence
// cursors use, so the dashboard has ONE colour language. Never the only signal.
export const accentFor = (room: string): string => colorForId(room) as string;

// The SURGICAL patch (see the header): class + CSS var + the badge's name text, per tile.
// The badge's plug icon is markup (terminals.js `paneHead`), so this never rewrites it.
// `root` is #term-stage. Returns the number of tiles found, for the tests.
export function applyRoomAccents(root: ParentNode | null | undefined, status: RoomsStatus | null | undefined): number {
    if (!root) return 0;
    const by = roomsByWid(status);
    const tiles = root.querySelectorAll<HTMLElement>('.grid-stack-item[gs-id]');
    tiles.forEach(item => {
        const wid = item.getAttribute('gs-id') as string;
        const mine = by[wid] || [];
        const content = item.querySelector<HTMLElement>('.grid-stack-item-content') || item;
        const badge = item.querySelector<HTMLElement>('.gs-room');
        content.classList.toggle('in-room', mine.length > 0);
        if (mine.length) content.style.setProperty('--room-accent', accentFor(mine[0] as string));
        else content.style.removeProperty('--room-accent');
        const name = badge && badge.querySelector('.gs-room-name');
        if (!badge || !name) return;
        if (mine.length) {
            const extra = mine.length > 1 ? ` +${mine.length - 1}` : '';
            name.textContent = `${mine[0]}${extra}`;   // the non-hue cue: the room's NAME, by its icon
            badge.setAttribute('data-room', mine[0] as string);
            badge.setAttribute('title', `In room ${mine.join(', ')}, click to leave ${mine[0]}`);
            badge.hidden = false;
        } else {
            name.textContent = '';
            badge.removeAttribute('data-room');
            badge.hidden = true;
        }
    });
    return tiles.length;
}
