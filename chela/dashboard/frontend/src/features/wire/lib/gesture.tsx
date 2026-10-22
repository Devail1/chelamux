// CMX-242 (CMX-212 P32): THE WIRE: drag a line from one tile onto another to put their agents
// in a room. The relationship is chela/rooms.py's (typed ledger, active dispatch, loop guards);
// this is the gesture that CREATES one, and nothing more. From terminals.js.
//
// Three things make it work on a wall of live iframes:
//   1. THE IFRAMES MUST NOT EAT THE MOUSE: `.gs-dragging` on the grid drops `pointer-events`
//      on every `.term-frame` (style.css), so mousemove and elementFromPoint see the TILES.
//      Not `.term-dragging`, which HIDES the terminals: watching them is the point.
//   2. THE PRESS IS CONSUMED (preventDefault + stopPropagation), or Gridstack starts a tile drag.
//   3. EVERY OTHER TILE SPROUTS A SOCKET while a wire is in flight (`.wire-live` on the stage),
//      or nobody discovers the gesture exists.
//
// The line is a React SVG over #term-stage, painted synchronously on every move. Each wire
// has its own root in its own container: terminals.js may rewrite the stage (a new pane, a
// rebuild) mid-gesture, and a root whose container left the page still unmounts cleanly,
// where a portal would fail to remove a node that is no longer there.
// The tiles are still legacy markup, so the source/target/socket classes are set on them here.
// Room state never reaches the wall's render signature: the accents are patched per tile
// (applyRoomAccents), so wiring two agents together reloads no terminal.
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { applyRoomAccents, bezierPath, resolveDrop, type RoomsStatus } from '../../../../../static/js/wire.ts';
import { api, BOOT } from '../../../shared/api/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { termMode } from '../../../shared/store/wall.ts';
import { displayLabel, type HeadAgent } from '../../panehead/index.ts';
import { paneToast } from '../../share/index.ts';

interface Wire {
  fromWid: string;
  stage: HTMLElement;
  grid: HTMLElement;
  /** This wire's overlay: its container, in the stage, and the root drawing into it. */
  box: HTMLElement;
  root: Root;
  hoverWid: string | null;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

let wire: Wire | null = null;
let rooms: RoomsStatus | null = null;

const wallOn = () => BOOT.terminalsEnabled && termMode.get() === 'wall';
const stageEl = () => document.getElementById('term-stage');

function tileEl(wid: string): HTMLElement | null {
  return [...document.querySelectorAll<HTMLElement>('#term-stage .grid-stack-item[gs-id]')].find((t) => t.getAttribute('gs-id') === wid) ?? null;
}

function paint(w: Wire): void {
  flushSync(() =>
    w.root.render(
      <svg className="wire-overlay">
        <path d={bezierPath(w.x1, w.y1, w.x2, w.y2)} />
      </svg>,
    ),
  );
}

/** Every way a held gesture can end where `document` cannot see it (released over devtools,
 * a second monitor, the desktop) is wired here too: a `.gs-dragging` that outlived the drag
 * would leave every terminal on the wall dead to clicks. */
export function cancelWire(): void {
  const w = wire;
  if (!w) return;
  document.removeEventListener('mousemove', onMove);
  document.removeEventListener('mouseup', onDrop);
  document.removeEventListener('keydown', onKey);
  document.removeEventListener('pointercancel', cancelWire);
  window.removeEventListener('blur', cancelWire);
  w.stage.classList.remove('wire-live');
  w.grid.classList.remove('gs-dragging');
  if (w.hoverWid) tileEl(w.hoverWid)?.classList.remove('wire-target');
  tileEl(w.fromWid)?.classList.remove('wire-source');
  wire = null;
  w.root.unmount();
  w.box.remove();
}

function point(w: Wire, e: MouseEvent) {
  const r = w.stage.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

/** The tile under the pointer, or null (empty stage, or a tile with no id: both CANCEL). */
function hit(e: MouseEvent): string | null {
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const item = el?.closest('#term-stage .grid-stack-item');
  return item?.getAttribute('gs-id') || null;
}

function onMove(e: MouseEvent): void {
  const w = wire;
  if (!w) return;
  // No button held, and we never saw it come up: it was released off-window. The gesture
  // ended on nothing we saw, so it is a CANCEL: a room the user did not aim at is worse than none.
  if (e.buttons !== undefined && !(e.buttons & 1)) {
    cancelWire();
    return;
  }
  const p = point(w, e);
  w.x2 = p.x;
  w.y2 = p.y;
  const over = hit(e);
  const next = resolveDrop(w.fromWid, over).ok ? over : null;
  if (next !== w.hoverWid) {
    if (w.hoverWid) tileEl(w.hoverWid)?.classList.remove('wire-target');
    w.hoverWid = next;
    if (next) tileEl(next)?.classList.add('wire-target');
  }
  paint(w);
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') cancelWire();
}

async function onDrop(e: MouseEvent): Promise<void> {
  const w = wire;
  if (!w) return;
  const drop = resolveDrop(w.fromWid, hit(e));
  const head = tileEl(w.fromWid)?.querySelector<HTMLElement>('.gs-head') ?? null;
  cancelWire();
  if (!drop.ok) return; // self, empty stage, no id: nothing is created
  try {
    const res = (await api('/api/rooms/join', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wids: drop.wids }),
    })) as { room?: string; error?: string } | null;
    if (!res || res.error) {
      paneToast(head, res?.error || 'wire failed');
      return;
    }
    const peer = drop.wids[1];
    const agent = (agents.get() as HeadAgent[]).find((a) => a.window_id === peer);
    paneToast(head, `${displayLabel(agent, peer)} joined ${res.room ?? 'the room'}`);
  } catch {
    paneToast(head, 'wire failed, could not reach the dashboard');
    return;
  }
  await refreshRooms();
}

/** Start a wire from `wid`'s tile (the pane header's Wire row, on its press). */
export function startWire(e: MouseEvent, from: HTMLElement, wid: string): void {
  e.preventDefault();
  e.stopPropagation(); // or Gridstack takes the press as a tile drag
  if (!wallOn()) return;
  const stage = stageEl();
  const grid = stage?.querySelector<HTMLElement>('.grid-stack');
  if (!stage || !grid) return;
  cancelWire(); // never two at once
  grid.classList.add('gs-dragging');
  stage.classList.add('wire-live');
  const src = tileEl(wid);
  src?.classList.add('wire-source');
  // CMX-123: from the SOURCE PANE's top-center, never the menu row (a floating portal).
  const b = (src ?? from).getBoundingClientRect();
  const s = stage.getBoundingClientRect();
  const x1 = b.left + b.width / 2 - s.left;
  const y1 = b.top - s.top;
  const box = document.createElement('div');
  box.className = 'wire-overlay-box';
  stage.append(box);
  wire = { fromWid: wid, stage, grid, box, root: createRoot(box), hoverWid: null, x1, y1, x2: x1, y2: y1 };
  onMove(e);
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onDrop);
  document.addEventListener('keydown', onKey);
  document.addEventListener('pointercancel', cancelWire);
  window.addEventListener('blur', cancelWire);
}

/** The room badge on a tile's header: leave the room it names. Leaving edits membership; the
 * ledger itself is untouched. */
export async function leaveRoom(badge: HTMLElement, wid: string): Promise<void> {
  const room = badge.getAttribute('data-room');
  if (!room) return;
  try {
    await api('/api/rooms/leave', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wid, room }),
    });
  } catch {
    /* transient: the next poll reads the truth */
  }
  await refreshRooms();
}

/** Read the rooms (the dict `chela room status` prints) and PATCH the tiles' accents. */
export async function refreshRooms(): Promise<void> {
  if (!wallOn()) return;
  let status: unknown;
  try {
    status = await api('/api/rooms');
  } catch {
    return; // transient: the next tick
  }
  rooms = status as RoomsStatus;
  applyRoomAccents(stageEl(), rooms);
}

/** Repaint the last-known accents onto tiles that were just (re)built. */
export function replayRoomAccents(): void {
  if (rooms) applyRoomAccents(stageEl(), rooms);
}
