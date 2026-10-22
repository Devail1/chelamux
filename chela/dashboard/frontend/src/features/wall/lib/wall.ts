// CMX-247 (CMX-212 P33): the Wall (was terminals.js). Every pane is keyed by its tmux WINDOW ID
// (`@N`), never its name: a rename leaves the tile, its iframe and its ttyd untouched. Two modes:
// the wall (a Gridstack grid, grid.ts) and single (one pane; phones always). A tile's parts are
// React (panehead, tilefoot, paneframe), portaled into the slots it carries; the tile elements
// stay plain DOM because Gridstack owns them (it adopts, moves and removes them).
import { api, BOOT } from '../../../shared/api/index.ts';
import { onEvent } from '../../../shared/events/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { context, loadContext } from '../../../shared/store/context.ts';
import { view } from '../../../shared/store/view.ts';
import { minimizedPanes, pinnedPanes, setMinimized, termMode, wallLayout } from '../../../shared/store/wall.ts';
import { pinKeybar } from '../../keybar/index.ts';
import { configurePaneFrames, focusFrame, forgetReady, framesOf, isSuspended, mountPaneFrames, resolveReady } from '../../paneframe/index.ts';
import { configurePaneHeads, displayLabel, mountPaneHeads, refreshPaneHeads, type HeadAgent } from '../../panehead/index.ts';
import { configureShareHost, followAgents, stopOwnerPresence } from '../../share/index.ts';
import { renderSidebarAgents, sidebarOrder, updateCtxCache } from '../../sidebar/controller.ts';
import { orderByList } from '../../sidebar/model.ts';
import { mountTileFoots, refreshTileFoots } from '../../tilefoot/index.ts';
import { configureDock, forgetAutoDock, mountDock, refreshDock, runLazyDock } from '../../walldock/index.ts';
import { mountWallToolbar } from '../../walltoolbar/index.ts';
import { leaveRoom, refreshRooms, replayRoomAccents, startWire } from '../../wire/index.ts';
import * as G from './grid.ts';

/** What the Wall needs from the legacy page, handed over by nav.js at import. */
export interface WallHost {
  selectView(id: string): void;
  /** The tab title and favicon badge (util.js updateTabSignal). */
  tabSignal(rows: unknown[]): void;
}
const host: WallHost = { selectView() {}, tabSignal() {} };
export const configureWall = (h: WallHost): void => void Object.assign(host, h);

let mode = localStorage.getItem('pc_term_mode') || 'wall';
let termSig = '';
/** The panes on the page, in render order. */
export let renderedWids: string[] = [];
let active = ''; // the single-mode pane
let paneDefaulted = ''; // a pane picked before the sidebar had an order (adoptSidebarDefault)

const $ = (id: string) => document.getElementById(id);
const rows = () => agents.get() as HeadAgent[];
const liveWids = (): string[] => rows().filter((a) => (a as { online?: boolean }).online !== false && a.window_id).map((a) => a.window_id as string);
const setRows = (v: unknown) => agents.set(Array.isArray(v) ? v : []); // an error body is not a pane list
export const label = (wid: string): string => displayLabel(rows().find((a) => a.window_id === wid), wid);
/** The single-mode pane ('' when none). */
export const activePane = (): string => active;

// No local label store: a rename goes to tmux and comes back through /api/agents. Drop the old
// per-browser one (pc_pane_titles) so the two can never disagree.
try { localStorage.removeItem('pc_pane_titles'); } catch { /* private mode */ }

configureShareHost({ paneTitle: label, paneFrames: (wid) => framesOf(wid), activeWid: activePane });
configurePaneFrames({ activeWid: activePane, tick: () => void termTick(), frameLoaded: () => pinKeybar() });
configurePaneHeads({
  minimize: (wid) => minimizePane(wid),
  dropPane: (wid) => dropTerminalPane(wid),
  wireDragStart: (e, from, wid) => startWire(e, from, wid),
  leaveRoom: (badge, wid) => leaveRoom(badge, wid),
  labelsChanged: () => refreshPaneLabels(),
});
configureDock({ restore: (wid) => restoreFromDock(wid) });
pinnedPanes.subscribe(() => {
  for (const it of document.querySelectorAll('#term-stage .grid-stack-item[gs-id]')) it.classList.toggle('pane-pinned', pinnedPanes.get().has(it.getAttribute('gs-id') ?? ''));
});
// The fill is viewport-relative: a resize re-applies the preset (debounced).
let resizeTimer: ReturnType<typeof setTimeout> | undefined;
window.addEventListener('resize', () => {
  if (!BOOT.terminalsEnabled || mode !== 'wall' || !G.hasGrid()) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(G.applyGridLayout, 200);
});

// ---- A tile: its slots, then the React parts in them --------------------------------------------
function div(cls: string, attrs: Record<string, string> = {}, ...kids: HTMLElement[]): HTMLElement {
  const d = document.createElement('div');
  d.className = cls;
  for (const [k, v] of Object.entries(attrs)) d.setAttribute(k, v);
  d.append(...kids);
  return d;
}

/** A pane's slots: header, (wall) recap, terminal, footer. */
function paneParts(wid: string, wall: boolean): HTMLElement[] {
  const w = (k: string): Record<string, string> => (wall ? { [k]: '1' } : {});
  return [
    div('island-slot pane-head-slot', { 'data-head-for': wid, ...w('data-head-wall') }),
    ...(wall ? [div('island-slot', { 'data-recap-slot': wid })] : []),
    div('island-slot', { 'data-frame-for': wid }),
    div('island-slot', { 'data-foot-for': wid, ...w('data-foot-wall') }),
  ];
}

const tileEl = (wid: string, c: G.Cell): HTMLElement =>
  div(pinnedPanes.get().has(wid) ? 'grid-stack-item pane-pinned' : 'grid-stack-item',
    { 'gs-id': wid, 'gs-x': String(c.x), 'gs-y': String(c.y), 'gs-w': String(c.w), 'gs-h': String(c.h) },
    div('grid-stack-item-content', {}, ...paneParts(wid, true)));

/** Draw the React parts under `scope`: BEFORE Gridstack adopts the tiles (it finds `.gs-grip`). */
function mountTileParts(scope: ParentNode): void {
  mountPaneHeads(scope);
  mountTileFoots(scope);
  mountPaneFrames(scope);
}

/** A tile's spot: its saved one, else `fallback`. */
function spot(wid: string, fallback: { x: number; y: number }): G.Cell {
  const s = G.savedLayout()[wid] ?? {};
  return { x: Number.isInteger(s.x) ? (s.x as number) : fallback.x, y: Number.isInteger(s.y) ? (s.y as number) : fallback.y, w: s.w || 6, h: s.h || 5 };
}

// ---- Minimize to the dock --------------------------------------------------------------------
// A minimized tile leaves the grid but stays in the page, hidden, so its terminal never reloads.
function minimizeItem(item: G.GridItem): void {
  if (item.dataset.minimized === '1') return;
  const n = item.gridstackNode;
  if (n) item.dataset.gsCoords = JSON.stringify({ x: n.x, y: n.y, w: n.w, h: n.h });
  G.unadopt(item, false);
  item.dataset.minimized = '1';
  item.style.display = 'none';
}

export function minimizePane(wid: string): void {
  const item = G.gridItem(wid);
  if (!item) return;
  minimizeItem(item);
  setMinimized(wid, true); // the dock repaints synchronously, at its final height
  refit();
}

export function restoreFromDock(wid: string): void {
  const item = G.gridItem(wid);
  if (item && G.hasGrid()) {
    let c: Partial<G.Cell> = {};
    try { c = JSON.parse(item.dataset.gsCoords || '{}') as Partial<G.Cell>; } catch { /* no saved spot */ }
    item.removeAttribute('style'); // display:none and Gridstack's stale inline position
    for (const k of ['x', 'y'] as const) if (Number.isInteger(c[k])) item.setAttribute(`gs-${k}`, String(c[k]));
    for (const k of ['w', 'h'] as const) if (c[k]) item.setAttribute(`gs-${k}`, String(c[k]));
    delete item.dataset.minimized;
    G.adopt(item);
  }
  setMinimized(wid, false);
  refit();
}

/** The dock changes the height the wall may fill: refit above it. */
function refit(): void { if (mode === 'wall') G.applyGridLayout(); }

// Status: each row's share flag, the headers, footers and dock, then the lazy dock (CMX-76: a
// dispatched worker opens minimized and pops out when it blocks) and the attention layouts.
function applyTermStatus(): void {
  const list = rows();
  followAgents(list);
  refreshPaneHeads();
  refreshTileFoots();
  refreshDock();
  if (!BOOT.wallTileDispatched && mode === 'wall' && G.hasGrid()) runLazyDock(list, { onWall: (w) => renderedWids.includes(w), minimize: minimizePane, restore: restoreFromDock });
  G.applyAutoArrange();
  G.applyWallFocus();
  host.tabSignal(list);
}

// ---- The last viewed pane (CMX-169) ------------------------------------------------------
// Single mode opens the last pane viewed on THIS device if it is still online, else the top
// sidebar row. The stored wid is paired with its session id and name: a wid a tmux restart
// handed to another session matches neither, so it is not trusted.
const LAST_PANE_KEY = 'pc_term_last_wid';
type Who = { wid?: string; session_id?: string; name?: string };
const who = (wid: string) => (rows().find((x) => x.window_id === wid) ?? {}) as Who;

function rememberPane(wid: string): void {
  const a = who(wid);
  try { localStorage.setItem(LAST_PANE_KEY, JSON.stringify({ wid, session_id: a.session_id || '', name: a.name || '' })); } catch { /* private mode */ }
}

function storedPane(wids: string[]): string {
  let s: Who | null;
  try { s = JSON.parse(localStorage.getItem(LAST_PANE_KEY) || 'null') as Who | null; } catch { return ''; }
  if (!s?.wid || !wids.includes(s.wid)) return '';
  const a = who(s.wid);
  const same = (k: 'session_id' | 'name') => Boolean(s[k] && a[k] && s[k] === a[k]);
  return !((s.session_id && a.session_id) || (s.name && a.name)) || same('session_id') || same('name') ? s.wid : '';
}

function initialPane(wids: string[]): string {
  const stored = storedPane(wids);
  if (stored) return stored;
  const order = sidebarOrder();
  paneDefaulted = order.length ? '' : wids[0] || '';
  return orderByList(wids, order)[0] || '';
}

/** The boot's first render runs before the sidebar draws, so its default had no order to follow.
 * Once it draws (nav.js), move that default to the top row, unless something picked another. */
export function adoptSidebarDefault(): void {
  const was = paneDefaulted;
  paneDefaulted = '';
  const first = mobileWids()[0];
  if (was && mode === 'single' && active === was && first && first !== was) void showPane(first);
}

// ---- Render ----------------------------------------------------------------------------------
export function setTermMode(m: string): void {
  mode = m;
  localStorage.setItem('pc_term_mode', m);
  void renderTerminals();
}

/** The Wall view, in wall mode: a sidebar click then minimizes an open pane (CMX-139). */
export const isWallVisible = (): boolean => view.get() === 'terminals' && mode === 'wall';
// By wid (a rename is no change); the open pane counts in single mode only, where it is drawn.
const sigOf = (wids: string[]) => `${mode}|${mode === 'single' ? active : ''}|${wids.slice().sort().join(',')}`;

/** Render the Wall (the view's tick). Keeps every live iframe unless the mode, the single pane or
 * the pane set changed. `ctxRead`: the tick's shared context read (CMX-166). */
export async function renderTerminals(ctxRead?: Promise<unknown> | null): Promise<void> {
  if (!BOOT.terminalsEnabled) return;
  if (!rows().length) setRows(await api('/api/agents'));
  const wids = liveWids();
  if (G.isPhone()) mode = 'single'; // a 12-column wall is useless at 375 px
  termMode.set(mode);
  mountWallToolbar($('wall-toolbar-island'));
  mountDock($('term-min-dock'));
  if (!wids.includes(active)) active = initialPane(wids);
  if (sigOf(wids) === termSig) return;
  termSig = sigOf(wids);
  await resolveReady(wids); // before building: a ready pane draws its iframe with no placeholder
  document.body.classList.remove('pane-is-maximized', 'term-mobile-full'); // their pane is going
  const stage = $('term-stage');
  if (!stage) return;
  G.destroyGrid();
  stage.className = mode === 'wall' || !wids.length ? '' : 'term-single';
  if (!wids.length) {
    const empty = div('');
    empty.style.cssText = 'padding:20px; text-align:center; color:var(--text-dim);';
    empty.textContent = 'No active agents';
    stage.replaceChildren(empty);
    renderedWids = [];
  } else if (mode === 'wall') {
    renderedWids = wids.slice(); // before the build: the lazy dock reads it
    const gs = div('grid-stack', {}, ...wids.map((wid, i) => tileEl(wid, spot(wid, { x: (i % 2) * 6, y: Math.floor(i / 2) * 5 }))));
    stage.replaceChildren(gs);
    mountTileParts(gs);
    G.initGrid(gs);
    for (const wid of wids) if (minimizedPanes.get().has(wid)) minimizeItem(G.gridItem(wid) as G.GridItem);
    G.applyWallInteractivity(); // a reload keeps the locked / auto / Focus feel
    replayRoomAccents();
  } else {
    rememberPane(active); // every single-mode switch lands here: a tap, a swipe, a focus
    stage.replaceChildren(div('term-pane', {}, ...paneParts(active, false)));
    mountTileParts(stage);
    renderedWids = [active];
  }
  applyTermStatus();
  loadContext(ctxRead).catch(() => {});
  refit(); // AFTER the dock is at its final height: the fill leaves room for it
  pinKeybar(); // CMX-102: a fresh pane re-fits to the keybar if the keyboard is up
  bindStage(stage);
  refreshRooms();
}

/** Take in panes that went live WITHOUT touching the ones on screen (a rebuild reloads every
 * iframe): the wall appends tiles below the lowest one; single mode has nothing to draw. Deferred
 * while a background teardown holds the frames blank. */
export async function absorbFreshTerminals(live: string[]): Promise<void> {
  const fresh = live.filter((w) => !renderedWids.includes(w));
  if (isSuspended() || !fresh.length || mode !== 'wall') return;
  const gs = $('term-stage')?.querySelector('.grid-stack');
  if (!G.hasGrid() || !gs) {
    termSig = '';
    return renderTerminals();
  }
  await resolveReady(fresh);
  let nextY = G.nextFreeY();
  for (const wid of fresh) {
    if (G.gridItem(wid)) continue;
    const c = spot(wid, { x: 0, y: nextY });
    if (!Number.isInteger(G.savedLayout()[wid]?.y)) nextY += c.h; // only a tile placed here moves the free row
    const el = tileEl(wid, c);
    gs.append(el);
    mountTileParts(el);
    G.adopt(el);
    if (!renderedWids.includes(wid)) renderedWids.push(wid);
  }
  G.applyWallInteractivity(); // a tile added while locked/auto inherits no-resize / no-move
  refit();
  replayRoomAccents();
  termSig = sigOf(renderedWids);
}

export function dropTerminalPane(wid: string): void {
  stopOwnerPresence(wid);
  const el = G.gridItem(wid);
  if (mode === 'wall' && G.hasGrid() && el) {
    if (el.dataset.minimized === '1') el.remove(); // it already left the engine
    else G.unadopt(el, true);
  }
  const pane = mode === 'single' ? $('term-stage')?.querySelector('.term-pane') : null;
  if (pane) {
    pane.querySelector('[data-frame-for]')?.remove(); // its session died: a note, not a blank frame
    if (!pane.querySelector('.pane-ended')) pane.append(Object.assign(div('pane-ended'), { textContent: `${label(wid)}: session ended` }));
  }
  renderedWids = renderedWids.filter((w) => w !== wid);
  forgetReady(wid); // AFTER its slot left the page: a re-spawn re-probes its port
  setMinimized(wid, false);
  forgetAutoDock(wid);
  refit();
  termSig = sigOf(renderedWids); // what is on screen is the baseline: no rebuild of the survivors
}

/** Bring a pane to the user: the Wall, restored if minimized, scrolled to, focused, flashed. */
export function focusPaneByWid(wid: string): void {
  if (!BOOT.terminalsEnabled || !wid) return;
  paneDefaulted = ''; // an explicit focus beats the stored pane and becomes it
  rememberPane(wid);
  host.selectView('terminals');
  if (mode === 'single') return void showPane(wid);
  setTimeout(() => {
    if (minimizedPanes.get().has(wid)) restoreFromDock(wid);
    const item = G.gridItem(wid);
    if (!item) return;
    item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const ifr = item.querySelector<HTMLIFrameElement>('iframe.term-frame');
    if (ifr) focusFrame(ifr);
    const content = item.querySelector('.grid-stack-item-content') ?? item;
    content.classList.add('pane-flash');
    setTimeout(() => content.classList.remove('pane-flash'), 1100);
  }, 60); // after selectView's render settles
}

/** Drop the panes whose window is gone and absorb the new ones, by window id. */
async function reconcile(): Promise<void> {
  const live = liveWids();
  renderedWids.filter((w) => !live.includes(w)).forEach(dropTerminalPane);
  await absorbFreshTerminals(live);
}

/** The 4 s tick: reconcile, then repaint status, labels, rooms (made from the CLI too), context
 * and the sidebar (its dots and "Needs you" stay in lockstep with the wall). */
export async function termTick(): Promise<void> {
  if (!BOOT.terminalsEnabled || view.get() !== 'terminals') return;
  let fresh: unknown;
  try { fresh = await api('/api/agents'); } catch { return; } // transient: the next tick
  if (!Array.isArray(fresh)) return; // an error body is no pane list: dropping every pane would be a lie
  setRows(fresh);
  await reconcile();
  applyTermStatus();
  refreshPaneLabels();
  refreshRooms();
  try {
    const ctx = await api('/api/agents/context');
    if (Array.isArray(ctx)) context.set(ctx);
    refreshTileFoots(); // ...even when it is the same array
    updateCtxCache(ctx);
  } catch { /* keep the last fills */ }
  renderSidebarAgents(rows());
}

/** Relabel without touching an iframe (a rename only changes text). */
export function refreshPaneLabels(): void {
  if (!BOOT.terminalsEnabled) return;
  refreshPaneHeads();
  if (mode === 'wall') refreshDock();
}

// ---- Phone: switching the single pane ----------------------------------------------------------
// The sidebar is the switcher (CMX-202); a swipe on the pane HEADER steps prev/next (the iframe
// swallows its own touches), in the SIDEBAR's order (CMX-165). Unlisted panes go last.
const mobileWids = (): string[] => orderByList(liveWids(), sidebarOrder());

/** Show `wid` in single mode (a no-op when it is already shown). */
export function showPane(wid: string): Promise<void> {
  if (active === wid) return Promise.resolve();
  active = wid;
  return renderTerminals();
}

/** Step the single pane by `dir` (-1 / +1), wrapping. */
export function stepAgentMobile(dir: number): void {
  const wids = mobileWids();
  const i = Math.max(0, wids.indexOf(active));
  if (wids.length > 1) void showPane(wids[(i + dir + wids.length) % wids.length] as string);
}

// Delegated off #term-stage, bound once, so they survive rebuilds: the header swipe (phones,
// single mode) and, while Focus is on, a click on a strip pane's header promotes it (a button,
// link or menu in the header keeps its own meaning).
const bound = new WeakSet<HTMLElement>();
function bindStage(stage: HTMLElement): void {
  if (bound.has(stage)) return;
  bound.add(stage);
  let x0: number | null = null;
  let y0 = 0;
  stage.addEventListener('touchstart', (e) => {
    const t = e.touches[0];
    const head = (e.target as Element).closest?.('.gs-head, .gs-label, .pane-title');
    x0 = head && t && G.isPhone() && mode === 'single' ? t.clientX : null;
    y0 = t?.clientY ?? 0;
  }, { passive: true });
  stage.addEventListener('touchend', (e) => {
    const t = e.changedTouches[0];
    const dx = t && x0 != null ? t.clientX - x0 : 0;
    if (t && x0 != null && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(t.clientY - y0) * 1.5) stepAgentMobile(dx < 0 ? 1 : -1);
    x0 = null;
  }, { passive: true });
  stage.addEventListener('click', (e) => {
    const t = e.target as Element;
    const wid = t.closest('.gs-head')?.closest('.grid-stack-item')?.getAttribute('gs-id');
    if (wid && wallLayout.get().focus && mode === 'wall' && !t.closest('button, a, .popover')) G.promoteFocus(wid);
  });
}

// The window set changed (util.js already dropped the agent cache): reconcile by window id, so a
// pure rename is a no-op. It also fires with the Wall hidden; the guard skips it then.
onEvent('windows', () => {
  if (!BOOT.terminalsEnabled || view.get() !== 'terminals') return;
  api('/api/agents').then((fresh) => {
    if (!Array.isArray(fresh)) return; // an error body: the 4 s tick catches up
    setRows(fresh);
    void reconcile();
    refreshPaneLabels();
  }).catch(() => {}); // transient: the 4 s tick catches up
});
