// CMX-221 (CMX-212 P19): the ⌘K palette's state and rows. One fuzzy jump-to for everything:
// the open panes, sessions, views, project launches and a few actions, built fresh from the
// live caches on every render. What only legacy knows (the wall's panes, the view switch, a
// session's label) it reads through the host nav.js hands it at import (setPaletteHost).
import { BOOT } from '../../../shared/api/index.ts';
import { agents, type Agent } from '../../../shared/store/agents.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import { createStore } from '../../../shared/store/index.ts';
import { launchProject, newShellWindow } from '../../launcher/index.ts';
import { openNewTask } from '../../newtask/index.ts';
import { showAddSchedule } from '../../schedules/index.ts';
import { openSettings, settings } from '../../settings/index.ts';
import { isShared, stopShare } from '../../share/index.ts';
import { ATTENTION_RANK, DOT_CLASS, STATUS_WORD, rankItems, type PaletteItem } from '../model.ts';

export type PaletteHost = {
  /** The registered views the palette offers (views.js). */
  views(): { id: string; label: string }[];
  label(a: Agent): string;
  dotColor(a: Agent): string;
  selectView(id: string): void;
  /** A session row's click: it toggles a pane already open on the wall. */
  selectAgent(name: string): void;
  showAgentDetail(name: string): void;
  /** The exact open window a query names ("@32"), out of `wids` (static/js/windowid.ts). */
  resolveWid(q: string, wids: string[]): string | null;
  wall: {
    /** Every pane on the wall, and the minimized ones among them. */
    rendered(): string[];
    minimized(): Set<string>;
    /** `wids` in the wall's own order. */
    ordered(wids: string[]): string[];
    visible(): boolean;
    /** Leave single-terminal mode for the wall. */
    showWall(): void;
    focus(wid: string): void;
    share(wid: string): void;
  };
};

let host: PaletteHost | null = null;
export function setPaletteHost(h: PaletteHost): void {
  host = h;
}

type State = { open: boolean; query: string; sel: number; /** Driven from the sidebar's "Jump to session" box, which keeps focus. */ fromSidebar: boolean };
export const palette = createStore<State>({ open: false, query: '', sel: 0, fromSidebar: false });
const patch = (p: Partial<State>) => palette.set({ ...palette.get(), ...p });

export const shortcuts = createStore(false);

const word = (a: Agent | undefined, h: PaletteHost) => (a ? STATUS_WORD[h.dotColor(a)] || 'idle' : 'open');
const dot = (a: Agent | undefined, h: PaletteHost) => (a ? DOT_CLASS[h.dotColor(a)] || 'idle' : 'idle');
const agentOf = (wid: string) => agents.get().find((x) => x.window_id === wid);

/** Every row but the open-pane section; `skip` leaves out sessions already floated to the top. */
function baseItems(h: PaletteHost, skip?: Set<string>): PaletteItem[] {
  const items: PaletteItem[] = h.views().map((v) => ({ icon: 'wall', title: v.label, sub: 'view', run: () => h.selectView(v.id) }));
  const list = agents.get();
  for (const a of list) {
    if (skip && a.window_id && skip.has(a.window_id)) continue;
    // CMX-417: the window id rides the sub-line, so it shows on the row
    items.push({ wid: a.window_id, dot: dot(a, h), title: h.label(a), sub: `session · ${word(a, h)}${a.window_id ? ' · ' + a.window_id : ''}`,
      run: () => h.selectAgent(String(a.name)) });
  }
  if (BOOT.terminalsEnabled) {
    // Share / Stop sharing per live session (the same server flag as the pane button).
    for (const a of list) {
      const wid = a.window_id;
      if (!wid) continue;
      const shared = isShared(wid);
      items.push({ icon: shared ? 'close' : 'share', title: (shared ? 'Stop sharing ' : 'Share ') + h.label(a), sub: shared ? 'shared session' : 'session',
        run: () => { if (shared) void stopShare(wid); else h.wall.share(wid); } });
    }
    const seen = new Set<string>();
    const l = launcher.get();
    for (const e of [...(l.favorites || []), ...(l.recent || [])]) {
      if (!e?.path || seen.has(e.path)) continue;
      seen.add(e.path);
      const path = e.path;
      items.push({ icon: 'launch', title: 'Launch ' + (e.label || path), sub: 'project', run: () => void launchProject(path) });
    }
  }
  items.push({ icon: 'shell', title: 'New shell window', sub: 'action', run: () => void newShellWindow() });
  // CMX-6: create a Linear issue.
  items.push({ icon: 'add', title: 'New task', sub: 'action · Linear issue', run: () => openNewTask() });
  items.push({ icon: 'schedule', title: 'Add scheduled task', sub: 'action', run: () => showAddSchedule() });
  // CMX-121: the only way to the shortcuts sheet (no dedicated global key).
  items.push({ icon: 'keyboard', title: 'Keyboard shortcuts', sub: 'help', run: () => openShortcuts() });
  // CMX-401: on a phone there is no Ctrl+, and the sidebar box is the palette: this is the way
  // to Settings. Open-only: on an open Settings it is not a toggle-off.
  items.push({ icon: 'settings', title: 'Settings', sub: 'action', run: () => { if (!settings.get().open) openSettings(); } });
  return items;
}

/** CMX-116: on an empty query every live, unminimized pane floats to the top, in wall order,
 * a pane wanting attention (waiting > working > idle) ahead of its peers (sort is stable). */
function openPaneItems(h: PaletteHost): PaletteItem[] {
  if (!BOOT.terminalsEnabled) return [];
  const min = h.wall.minimized();
  const live = h.wall.rendered().filter((w) => !min.has(w));
  if (!live.length) return [];
  const rank = (wid: string) => {
    const a = agentOf(wid);
    return ATTENTION_RANK[a ? h.dotColor(a) : 'grey'] ?? 2;
  };
  return h.wall.ordered(live).slice().sort((a, b) => rank(a) - rank(b)).map((wid) => {
    const a = agentOf(wid);
    return { wid, dot: dot(a, h), title: a ? h.label(a) : wid, sub: `open pane · ${word(a, h)}`, run: () => h.wall.focus(wid) };
  });
}

/** CMX-417: a query that is exactly an open window's id jumps to it. A jump, never a toggle
 * (a row click minimizes an open pane); any open window counts, a plain shell too. */
function windowIdItem(h: PaletteHost, q: string): PaletteItem | null {
  const known = agents.get().map((a) => a.window_id).filter((w): w is string => !!w);
  const wid = h.resolveWid(q, known.concat(BOOT.terminalsEnabled ? h.wall.rendered() : []));
  if (!wid) return null;
  const a = agentOf(wid);
  return {
    wid, dot: dot(a, h), title: a ? h.label(a) : wid, sub: `window · ${wid}`,
    run: () => {
      if (BOOT.terminalsEnabled) {
        if (!h.wall.visible()) h.wall.showWall();
        h.wall.focus(wid);
      } else if (a) h.showAgentDetail(String(a.name));
    },
  };
}

/** The rows for `q`, and how many lead as the open-pane section (a divider follows them). */
export function paletteRows(q: string): { items: PaletteItem[]; panes: number } {
  if (!host) return { items: [], panes: 0 };
  if (q) return { items: rankItems(q, baseItems(host), windowIdItem(host, q)), panes: 0 };
  const panes = openPaneItems(host);
  return { items: panes.concat(baseItems(host, new Set(panes.map((p) => p.wid as string)))), panes: panes.length };
}

export function openPalette(): void {
  patch({ open: true, query: '', sel: 0, fromSidebar: false });
}

export function closePalette(): void {
  patch({ open: false, fromSidebar: false });
}

export function togglePalette(): void {
  if (palette.get().open) closePalette();
  else openPalette();
}

export function setPaletteQuery(query: string): void {
  patch({ query, sel: 0 });
}

/**
 * CMX-377: the sidebar's "Jump to session" box drives this same palette, in place: it opens it
 * (never re-clearing an open one) and filters by the box's own value, while focus STAYS in the
 * box, so typing goes on there; the arrows and Enter work off the palette being open.
 */
export function sidebarJumpInput(el: HTMLInputElement): void {
  const s = palette.get();
  patch(s.open ? { query: el.value, sel: 0 } : { open: true, query: el.value, sel: 0, fromSidebar: true });
}

export function movePalette(d: number): void {
  const n = paletteRows(palette.get().query).items.length;
  if (!n) return;
  patch({ sel: (palette.get().sel + d + n) % n });
}

export function hoverPalette(i: number): void {
  if (palette.get().sel !== i) patch({ sel: i });
}

/** Run row `i` (the selected one by default): the palette closes first. */
export function runPalette(i = palette.get().sel): void {
  const it = paletteRows(palette.get().query).items[i];
  if (!it) return;
  closePalette();
  try {
    it.run();
  } catch { /* a row whose target went away: nothing to do */ }
}

export function openShortcuts(): void {
  shortcuts.set(true);
}

export function closeShortcuts(): void {
  shortcuts.set(false);
}
