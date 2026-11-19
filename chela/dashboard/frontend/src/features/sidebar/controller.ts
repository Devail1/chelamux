// CMX-278 (CMX-172 slice C): the sidebar's state and commands, moved out of nav.js. It owns
// the /api/agents read the sidebar draws, the per-device collapsed groups, the server layout
// and archive, Recent sessions, the nav rows' state, the collapsed rail / phone drawer and the
// Alt fast switch. Its components draw what it publishes to the bridge; their clicks come back
// here through the actions the shell hands them (features/shell). What belongs to the rest of
// the page (the wall, the agent detail view, the launcher, the orchestrator slot) it reaches
// through the host the shell configures.
import { api, BOOT } from '../../shared/api/index.ts';
// panehead's model, not its index: PaneHead imports this controller (no-circular)
import { headLabel, isShell, type HeadAgent } from '../panehead/model.ts';
import { registerLane } from '../../shared/scheduler/index.ts';
import { type Agent, agents as agentsStore } from '../../shared/store/agents.ts';
import { onOverlayOpen } from '../../shared/ui/index.ts';
import { archiveLoaded, archivedKeys, archivedSessions, pruneHidden, refreshArchive, setArchive } from './lib/archive.ts';
import { commitLayout, getLayout, refreshLayout, writeLayout } from './lib/layout.ts';
import { isObj, lsJson, lsRead, lsSet, postJson, publish, say, sayRefused } from './lib/page.ts';
import {
  ARCHIVED_SECTION_KEY, type AgentRow, assignKey, buildItems, canOrderRows, clampWidth, type Custom, customKey, descendants,
  dismissKey, dropAssignee, type Group, type Item, isArchivable, isRunsRow, isSingleFolder, kidsKey, moveGroup, pinKey, placeGroup,
  placeRow, type NavState,
  normalizeView, reconcileAssign, type RecentRow, type RowOrder, SESSION_KEY_PREFIX, type RecentState, recentView,
  sidebarModel,
  sidebarOrder as modelSidebarOrder, type SidebarState, type View, type Wants,
} from './model.ts';

/** What the sidebar needs from the rest of the page (nav.js `configureSidebar`). */
export interface SidebarHost {
  /** A window's display label (terminals.js `_displayLabel`, CMX-62). */
  label(a: AgentRow): string;
  /** The wids open on the wall (rendered, not minimized). */
  onWall(): string[];
  orchWid(): string | null;
  wants: Wants;
  /** The tab title and favicon, in step with the list. */
  tabSignal(agents: AgentRow[]): void;
  /** CMX-169: a boot's default pane moves to the top row. */
  adoptDefault(): void;
  /** Open (never toggle) a window: its pane on the wall, else its detail view. */
  openPane(a: AgentRow): void;
  launchProject(cwd: string, opts: { fresh: boolean }): unknown;
}

let host: SidebarHost = {
  label: a => a.name ?? '', onWall: () => [], orchWid: () => null, wants: () => false,
  tabSignal() {}, adoptDefault() {}, openPane() {}, launchProject() {},
};
export { refreshArchive } from './lib/archive.ts';
export { refreshLayout } from './lib/layout.ts';

export function configureSidebar(h: SidebarHost): void {
  host = h;
}

const TERMINALS_ON = BOOT.terminalsEnabled;
const agentsNow = (): AgentRow[] => agentsStore.get() as unknown as AgentRow[];
const setAgents = (rows: AgentRow[]) => agentsStore.set(rows as unknown as Agent[]);
const rerender = () => renderSidebarAgents(agentsNow());

// --- The sidebar itself: one control, two behaviours -------------------------------------
// PHONE (≤768px): an off-canvas drawer over a scrim. DESKTOP: the same control collapses the
// column to an icon rail (persisted; pure CSS off a body class, so no row re-renders).
// index.html's inline <body> script sets the class before first paint (CMX-89).
const SIDEBAR_COLLAPSED_KEY = 'chela_sidebar_collapsed';
// optional: a page with no matchMedia (jsdom) is not a phone
const isPhone = () => !!window.matchMedia?.('(max-width: 768px)').matches;

// The canvas changed width without a window resize, so poke the wall's re-fit (a re-fit, not
// a rebuild: no terminal reloads, tests/wall.test.mjs).
const refit = () => setTimeout(() => window.dispatchEvent(new Event('resize')), 220);

function setCollapsed(collapsed: boolean) {
  document.body.classList.toggle('sidebar-collapsed', collapsed);
  lsSet(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
  document.getElementById('btn-menu')?.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  refit();
}

/** true = show the sidebar (drawer open / rail expanded), false = hide it, none = toggle. */
export function toggleSidebar(force?: boolean): void {
  if (!isPhone()) {
    setCollapsed(force === undefined ? !document.body.classList.contains('sidebar-collapsed') : !force);
    return;
  }
  const sb = document.querySelector('.sidebar');
  if (!sb) return;
  const open = force === undefined ? !sb.classList.contains('open') : !!force;
  sb.classList.toggle('open', open);
  document.getElementById('sidebar-scrim')?.classList.toggle('open', open);
  // CMX-377: the phone's floating opener hides while the drawer is open
  document.body.classList.toggle('sidebar-open', open);
  // CMX-313: and the pane title bar's hamburger opens the same drawer (CMX-315: and the Work bar's)
  for (const b of document.querySelectorAll('#btn-menu-mobile, .gs-head-menu, .work-bar-menu')) b.setAttribute('aria-expanded', open ? 'true' : 'false');
}

/** Navigating dismisses the phone drawer; it never folds the desktop rail. */
export function closeSidebar(): void {
  if (isPhone()) toggleSidebar(false);
}
// CMX-306: a modal, sheet or the palette opened from the drawer opens with it shut, never
// behind it (legacy closeSidebar() did this per opener; the kit does it for every overlay).
onOverlayOpen(closeSidebar);

if (lsRead(SIDEBAR_COLLAPSED_KEY, null) === '1') {
  document.body.classList.add('sidebar-collapsed');
  document.getElementById('btn-menu')?.setAttribute('aria-expanded', 'false');
}

/** The width handle's commit: kept in the server layout, so it follows across devices. */
export function setSidebarWidth(w: number): void {
  void writeLayout({ width: clampWidth(w) });
  refit();
}

// --- The nav rows (views.js) and their Work badges (the Work poll) ------------------------
let nav: NavState = { items: [], active: null, badges: {} };
export function setNav(patch: Partial<NavState>): void {
  nav = { ...nav, ...patch };
  publish('nav', nav);
}
export function setNavBadges(badges: Record<string, string>): void {
  setNav({ badges: { ...nav.badges, ...badges } });
}

// --- Per-DEVICE state (CMX-35): which groups are collapsed ---------------------------------
const SB_COLLAPSED_KEY = 'chela_grp_collapsed';
let memCollapsed: string[] | null = null;
function collapsedGroups(): Set<string> {
  const v = lsJson(SB_COLLAPSED_KEY, memCollapsed);
  return new Set(Array.isArray(v) ? (v as string[]) : []);
}
function saveCollapsed(s: Set<string>) {
  memCollapsed = [...s];
  lsSet(SB_COLLAPSED_KEY, memCollapsed);
}
function flip(key: string) {
  const s = collapsedGroups();
  if (s.has(key)) s.delete(key); else s.add(key);
  saveCollapsed(s);
  rerender();
}
export function toggleGroup(key: string): void {
  flip(key);
}
/** The Archived group is COLLAPSED by default, so what is persisted is that it was opened. */
export function toggleArchivedSection(): void {
  flip(`${ARCHIVED_SECTION_KEY}:open`);
}

// --- Context % per window, fed by this refresh and by the wall's tick ----------------------
let ctxByWid: Record<string, { used_pct?: number | null }> = {};
export function updateCtxCache(ctx: unknown): void {
  if (!Array.isArray(ctx)) return;
  const m: typeof ctxByWid = {};
  for (const c of ctx as { window_id?: string; used_pct?: number | null }[]) if (c?.window_id) m[c.window_id] = c;
  ctxByWid = m;
}

// --- The layout's view and custom groups ---------------------------------------------------
const view = (): View => normalizeView(getLayout().view);
function custom(): Custom {
  const c = getLayout().custom ?? {};
  const groups = Array.isArray(c.groups)
    ? (c.groups as Custom['groups']).filter(g => isObj(g) && typeof g.id === 'string' && typeof g.name === 'string') : [];
  const seen = isObj(c.seen) ? { ...(c.seen as Record<string, number>) } : {};
  return { groups, assign: isObj(c.assign) ? { ...(c.assign as Record<string, string>) } : {}, seen };
}
// CMX-381: the Manual sort's row order per group; an entry that is not {keys, at} is skipped.
function rowOrders(): Record<string, RowOrder> {
  const out: Record<string, RowOrder> = {};
  for (const [g, e] of Object.entries(getLayout().rows ?? {})) {
    if (!isObj(e) || !Array.isArray(e.keys) || typeof e.at !== 'number') continue;
    out[g] = { keys: e.keys.filter((k): k is string => typeof k === 'string'), at: e.at };
  }
  return out;
}
// `seen` only while it holds a stamp: the server omits an empty one (sidebar_layout.py).
const customFields = (c: Custom) => ({
  custom: { groups: c.groups, assign: c.assign, ...(c.seen && Object.keys(c.seen).length ? { seen: c.seen } : {}) },
});
const saveCustom = (c: Custom) => void writeLayout(customFields(c));
const without = (assign: Record<string, string>, drop: (k: string, v: string) => boolean) =>
  Object.fromEntries(Object.entries(assign).filter(([k, v]) => !drop(k, v)));

// CMX-390: the pinned sessions (`s:<session id>`), in the server layout like the rest.
const pins = (): string[] => [...getLayout().pins];

// --- What the island draws -----------------------------------------------------------------
// The last render's model: what the menus act on (they open off what is on screen). `lists`
// are the row lists a drag may reorder: each group's rows, and (CMX-390) each parent's children.
let sb: { groups: Group[]; items: Map<string, Item>; lists: Map<string, Item[]> } =
  { groups: [], items: new Map(), lists: new Map() };
let state: SidebarState | null = null;
let activeRow: string | null = null;
let altHeld = false;
// CMX-378: each window's session id at the last render, so a `/clear` carries its group, and
// (CMX-431) the window's Claude process start (`created`): a `/clear` keeps that process, so
// the window id names the same window only while it is unchanged. A tmux restart (a reboot)
// hands the old ids to OTHER sessions, which must never inherit a group through them.
let lastWin: ReadonlyMap<string, { sid: string; created: AgentRow['created'] }> = new Map();

function publishSidebar(patch: Partial<SidebarState>) {
  if (!state) return;
  state = { ...state, ...patch };
  publish('sidebar', state);
}

/** The row lit as the current view (an agent drilled into), or none. */
export function setActiveRow(name: string | null): void {
  activeRow = name;
  publishSidebar({ active: name });
}

/** CMX-165: the window ids in the order the sidebar last drew them, View filters lifted. */
export function sidebarOrder(): string[] {
  return modelSidebarOrder(state);
}

export function renderSidebarAgents(agents: AgentRow[] | null | undefined): void {
  const rows = agents || [];
  host.tabSignal(rows);
  if (!document.getElementById('sidebar-agents')) return;
  const c = custom();
  const labels: Record<string, string> = {};
  // CMX-330: a plain shell reads `Shell` here too, by the pane header's own rule (headLabel).
  for (const a of rows) if (a?.window_id) labels[a.window_id] = isShell(a as HeadAgent) ? headLabel(a as HeadAgent, a.window_id).title : host.label(a);
  const ctx: Record<string, number> = {};
  for (const [wid, x] of Object.entries(ctxByWid)) if (x.used_pct != null) ctx[wid] = x.used_pct;
  state = {
    agents: rows, wants: host.wants, orchWid: host.orchWid(), view: view(), order: getLayout().order, rows: rowOrders(), custom: c,
    archived: archivedKeys(), sessions: archivedSessions(), labels, now: Date.now(), collapsed: [...collapsedGroups()],
    ctx, onWall: TERMINALS_ON ? host.onWall() : [], active: activeRow, altHeld,
    // CMX-388: CHELA_DISPATCH=off, no Dispatched section (model.ts plainFolderKey).
    dispatchOff: BOOT.dispatchOffByChoice,
    pins: pins(),
  };
  if (!rows.length) {
    // "No agents". One empty read must not unarchive every hidden row, nor drop every
    // custom-group assignment.
    publish('sidebar', state);
    return;
  }
  // CMX-378: assignments follow the SESSION, not the window (model.reconcileAssign has the
  // rules): a legacy `w:@N` of a live window moves to its session key, a gone window's
  // `w:` is dropped, a session absent right now keeps its group. Applied to THIS render too,
  // so a session resumed under a new window id lands in its group on the first draw.
  const prevSid = new Map<string, string>();
  for (const a of rows) {
    const w = a?.window_id ?? '', was = lastWin.get(w);
    if (was && was.created === a?.created) prevSid.set(w, was.sid);
  }
  const next = reconcileAssign(c, rows, archiveLoaded() ? state.sessions : null, state.now, prevSid);
  lastWin = new Map(rows.filter(a => a?.window_id && a.session_id)
    .map(a => [a.window_id as string, { sid: a.session_id as string, created: a.created }]));
  if (next) {
    state.custom = next;
    saveCustom(next);
  }
  const model = sidebarModel(state);
  pruneHidden(model.hidden);
  const items = new Map<string, Item>();
  const roots = [...model.needsYou, ...model.pinned, ...model.groups.flatMap(g => g.items), ...model.archived];
  for (const it of roots.flatMap(r => [r, ...descendants(r, model.children)])) items.set(it.key, it);
  const lists = new Map<string, Item[]>(model.groups.map(g => [g.key, g.items]));
  for (const [k, kids] of Object.entries(model.children)) {
    const parent = items.get(k);
    if (parent) lists.set(kidsKey(parent), kids);
  }
  sb = { groups: model.groups, items, lists };
  publish('sidebar', state);
  host.adoptDefault();
}

// The one /api/agents read (it also primes the agents store every module reads). CMX-166:
// `onAgents` fires once the list is cached, before the archive, layout and Recent reads.
// `ctxRead` is the tick's one /api/agents/context read.
export async function refreshSidebar(onAgents?: () => void, ctxRead?: Promise<unknown> | null): Promise<void> {
  try {
    const [agents, ctx] = await Promise.all([
      api('/api/agents'),
      TERMINALS_ON ? (ctxRead || api('/api/agents/context')).catch(() => null) : Promise.resolve(null),
    ]);
    // A non-list reply (an error body) is a failed read: keep the last render.
    if (!Array.isArray(agents)) throw new Error('/api/agents: not a list');
    setAgents(agents as AgentRow[]);
    if (ctx) updateCtxCache(ctx);
    onAgents?.();
    // Before the render: what another device archived or resumed shows here this tick.
    await Promise.all([refreshArchive(), refreshLayout()]);
    rerender();
  } catch { /* transient: keep the last render, the next tick retries */ }
  // Awaited: a caller (resumeSession) must see BOTH halves settled.
  await refreshRecentSessions();
}

// The sidebar is always visible, so it refreshes on every tick whatever the view; its
// /api/agents read is what lets the view's tick start (CMX-166).
registerLane('sidebar', t => refreshSidebar(t.paneListKnown, t.context).finally(t.paneListKnown));

// --- The archive's commands ----------------------------------------------------------------
// A plain session is CLOSED server-side (resumable from the Archived group), a settled run is
// hidden. Refusals are reported, never swallowed.
export async function archiveKeys(keys: string[]): Promise<unknown> {
  if (!keys?.length) return null;
  let res: unknown = null;
  try {
    res = await postJson('/api/sidebar/archive', { keys });
  } catch { /* below */ }
  if (isObj(res) && res.state) setArchive(res.state);
  // The windows the server CLOSED are gone: drop them before the next poll says so.
  const closed = new Set(isObj(res) && Array.isArray(res.closed) ? (res.closed as string[]) : []);
  if (closed.size) setAgents(agentsNow().filter(a => !(a?.window_id && closed.has(a.window_id))));
  rerender();
  if (!res) say('Archive failed. The dashboard did not answer.');
  else sayRefused(res);
  return res;
}

/** A closed session is RESUMED (a new window, `claude --resume`); a hidden row shown again. */
export async function unarchiveKeys(keys: string[]): Promise<unknown> {
  if (!keys?.length) return null;
  let res: unknown = null;
  try {
    res = await postJson('/api/sidebar/unarchive', { keys });
  } catch { /* below */ }
  if (isObj(res) && res.state) setArchive(res.state);
  rerender();
  if (!res) say('Unarchive failed. The dashboard did not answer.');
  else sayRefused(res);
  return res;
}

/** A row's Unarchive (the island disables its button at once: one resume, one window). */
export async function unarchiveRow(_btn: unknown, key: string): Promise<unknown> {
  const res = await unarchiveKeys([key]);
  if (isObj(res) && Array.isArray(res.resumed) && res.resumed.length) await refreshSidebar();
  return res;
}

// --- The group, row and VIEW menus' commands (CMX-35, CMX-66, CMX-81) ------------------------
// "Archive all" archives the group's FINISHED rows (model.isArchivable); a row working, blocked
// on you, the orchestrator or a judge mid-battery is never archived.
const archivable = (it: Item) => isArchivable(it, { wants: host.wants, orchWid: host.orchWid(), pins: pins() });

export function groupMenuAction(key: string, act: string): unknown {
  const g = sb.groups.find(x => x.key === key);
  if (!g) return undefined;
  if (act === 'new') return groupNewSession(key);
  if (act === 'up' || act === 'down') {
    const next = moveGroup(sb.groups.map(x => x.key), getLayout().order, key, act === 'up' ? -1 : 1);
    if (next) void commitOrder(next);
  } else if (act === 'collapse-all') {
    // CMX-379: a one-session folder has no header to fold. Stamping its key would fold it
    // later, unasked, the moment it gains a second session.
    const by = view().groupBy;
    saveCollapsed(new Set([...collapsedGroups(), ...sb.groups.filter(x => !isSingleFolder(x, by)).map(x => x.key)]));
  } else if (act === 'expand-all') {
    const keys = new Set(sb.groups.map(x => x.key));
    saveCollapsed(new Set([...collapsedGroups()].filter(k => !keys.has(k))));
  } else if (act === 'archive') {
    return archiveKeys(g.items.filter(archivable).map(it => it.key));
  }
  rerender();
  return undefined;
}

/** `+` on a group header: a NEW session in its folder (`fresh` skips the focus-existing dedup). */
export function groupNewSession(key: string): unknown {
  const g = sb.groups.find(x => x.key === key);
  if (!g?.cwd) return undefined;
  return host.launchProject(g.cwd, { fresh: true });
}

export function rowMenuAction(key: string, act: string): unknown {
  const it = sb.items.get(key);
  if (!it) return undefined;
  if (act === 'archive') {
    // re-checked at click time: the row may have woken up since the menu opened
    if (archivedKeys().includes(it.key) || !archivable(it)) return null;
    return archiveKeys([it.key]);
  }
  if (act === 'pin' || act === 'unpin') return setPinned(it, act === 'pin');
  const w = act === 'judge' ? it.judge : it.agent;
  if (w) host.openPane(w);
  return undefined;
}

// CMX-380: the group order and a row's group are what a viewer moved by hand: shown at once,
// and put back (saying why) when the server refuses or does not answer (layout.commitLayout).
// Move up / Move down and a drag share commitOrder; Move to group and a drop share
// assignGroup, so the two paths can never write different things.
function commitOrder(next: string[]): Promise<boolean> {
  return commitLayout({ order: next }, 'Moving the group', rerender);
}

/** CMX-438: is `itemKey` (an assignment key) a dispatched run's row? A run lives in the Runs
 * group and is never assigned to a custom group, by a drop or by Move to group. */
function isRunKey(itemKey: string): boolean {
  const opts = { orchWid: host.orchWid(), dispatchOff: BOOT.dispatchOffByChoice };
  for (const it of sb.items.values()) if (assignKey(it) === itemKey && isRunsRow(it, opts)) return true;
  return false;
}

function assignGroup(itemKey: string, id: string | null): Promise<boolean> {
  if (isRunKey(itemKey)) return Promise.resolve(false);
  const c = custom();
  c.assign = without(c.assign, k => k === itemKey);
  const seen: Record<string, number> = Object.fromEntries(Object.entries(c.seen ?? {}).filter(([k]) => k !== itemKey));
  if (id && c.groups.some(g => g.id === id)) {
    c.assign[itemKey] = id;
    // stamped here, in the one write: else the next render's reconcileAssign stamps it in a
    // second write, and a failed first write could no longer be put back under it
    if (itemKey.startsWith(SESSION_KEY_PREFIX)) seen[itemKey] = Date.now();
  }
  c.seen = seen;
  const done = commitLayout(customFields(c), 'Moving the session', rerender);
  rerender();
  return done;
}

/** CMX-390: pin (or unpin) a row's SESSION: the Pinned section, and no archive while pinned.
 * Optimistic, put back saying why when the write fails. False when the row has no session. */
function setPinned(it: Item, on: boolean): Promise<boolean> | false {
  const k = pinKey(it);
  if (!k) return false;
  const now = pins();
  const next = on ? (now.includes(k) ? now : [...now, k]) : now.filter(x => x !== k);
  const done = commitLayout({ pins: next }, on ? 'Pinning the session' : 'Unpinning the session', rerender);
  rerender();
  return done;
}

/** Assign a row (by its STABLE assignment key, model.assignKey) to custom group `id`, or to
 * Ungrouped (null). */
export function moveToGroup(itemKey: string, id: string | null): void {
  void assignGroup(itemKey, id);
}

/** A group header (or single-session folder row) dropped just before group `before` (null:
 * after the last one). False when it moved nothing (a pinned group, no change). */
export function dropGroup(key: string, before: string | null): boolean {
  const next = placeGroup(sb.groups.map(x => x.key), getLayout().order, key, before);
  if (!next) return false;
  void commitOrder(next);
  rerender();
  return true;
}

/** A row (by its assignment key) dropped on group `groupKey` (null: on no group, which
 * unassigns it, as Ungrouped does). False when that is no drop target (a pinned section). */
export function dropOnGroup(itemKey: string, groupKey: string | null): boolean {
  const id = dropAssignee(groupKey, custom());
  if (id === undefined || isRunKey(itemKey)) return false;
  void assignGroup(itemKey, id);
  return true;
}

/** CMX-381: under Manual, row `itemKey` of group `groupKey` dropped just before row `before`
 * (null: last). Persists the group's row order by the rows' stable keys (model.placeRow), with
 * the same optimistic write and snap-back as a group move. False when it moved nothing: not
 * Manual, grouped by state or date, a pinned section, or no change. */
export function dropRow(groupKey: string, itemKey: string, before: string | null): boolean {
  // CMX-390: `groupKey` may also name a parent's children (`~kids:<parent>`), reordered alike
  const list = sb.lists.get(groupKey);
  if (!list || !canOrderRows(view(), groupKey)) return false;
  const all = rowOrders();
  // a stored key is kept only while its session is still somewhere: live in a window (a
  // filter may hide it) or held in the archive. A gone one is dropped from the order.
  const known = new Set<string>();
  for (const it of buildItems(agentsNow())) known.add(assignKey(it)).add(it.key);
  for (const r of archivedSessions() ?? []) if (r?.session_id) known.add(`${SESSION_KEY_PREFIX}${r.session_id}`);
  // before the archive has answered, an archived session is not known yet: drop nothing
  const keep = archiveLoaded() ? (k: string) => known.has(k) : () => true;
  const next = placeRow(list, all[groupKey], itemKey, before, Date.now(), keep);
  if (!next) return false;
  void commitLayout({ rows: { ...all, [groupKey]: next } }, 'Moving the session', rerender);
  rerender();
  return true;
}

function askName(msg: string, dflt = ''): string {
  try {
    return (window.prompt(msg, dflt) || '').trim();
  } catch {
    return '';
  }
}

export function newCustomGroup(name?: string | null): { id: string; name: string } | null {
  const n = name != null ? String(name).trim() : askName('New group name:');
  if (!n) return null;
  const c = custom();
  const g = { id: `g${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, name: n };
  c.groups.push(g);
  saveCustom(c);
  rerender();
  return g;
}

export function renameCustomGroup(id: string, name?: string | null): void {
  const c = custom();
  const g = c.groups.find(x => x.id === id);
  if (!g) return;
  const n = name != null ? String(name).trim() : askName('Rename group:', g.name);
  if (!n) return;
  g.name = n;
  saveCustom(c);
  rerender();
}

/** Deleting a group moves its rows to Ungrouped; it never hides or closes them. */
export function deleteCustomGroup(id: string): void {
  const c = custom();
  c.groups = c.groups.filter(g => g.id !== id);
  c.assign = without(c.assign, (_k, v) => v === id);
  const rows = rowOrders();
  if (customKey(id) in rows) {
    // CMX-381: its manual row order goes with it (a later group of the same id starts fresh)
    void writeLayout({ ...customFields(c), rows: Object.fromEntries(Object.entries(rows).filter(([k]) => k !== customKey(id))) });
  } else {
    saveCustom(c);
  }
  rerender();
}

/** One View choice: `status:all`, `env:judge` (toggles), `activity:30d`, `group:state`,
 * `sort:name`, `toggle:showEmpty`. */
export function viewMenuAction(act: string): void {
  const v = view();
  const [k, val = ''] = String(act).split(':');
  if (k === 'status') v.status = val;
  else if (k === 'activity') v.activity = val;
  else if (k === 'group') v.groupBy = val;
  else if (k === 'sort') v.sort = val;
  else if (k === 'env') v.env = v.env.includes(val) ? v.env.filter(x => x !== val) : [...v.env, val];
  else if (k === 'toggle' && (val === 'showEmpty' || val === 'showPR')) v[val] = !v[val];
  else return;
  writeLayout({ view: normalizeView(v) as unknown as Record<string, unknown> });
  rerender();
}

// --- The orchestrator: the head's inbox icon (CMX-76) --------------------------------------
// The Decisions island (features/decisions) owns the icon and calls this on a click.
/** Focus the orchestrator's pane and row (its group expanded). False with no orchestrator. */
export function jumpToOrchestrator(): boolean {
  const wid = host.orchWid();
  const a = wid ? agentsNow().find(x => x?.window_id === wid) : null;
  if (!a) return false;
  host.openPane(a);
  const rowOf = () => [...document.querySelectorAll<HTMLElement>('#sidebar-agents .agent-row')]
    .find(r => r.dataset.agent === a.name) ?? null;
  let row = rowOf();
  const g = row?.closest<HTMLElement>('.side-group.collapsed');
  if (g?.dataset.g && g.dataset.g !== ARCHIVED_SECTION_KEY) {
    toggleGroup(g.dataset.g);
    row = rowOf();
  }
  row?.scrollIntoView?.({ block: 'nearest' });
  return true;
}

// --- Alt fast switch (CMX-85) --------------------------------------------------------------
// HOLDING Alt shows 1..9 on the first nine visible session rows (the island draws them), and
// Alt+N opens the Nth. Visible = a row in #sidebar-agents outside a collapsed group and the
// Archived group; Recent sessions are not sessions you can switch to.
function altRows(): HTMLElement[] {
  const el = document.getElementById('sidebar-agents');
  if (!el) return [];
  return [...el.querySelectorAll<HTMLElement>('.agent-row[data-agent]')]
    .filter(r => !r.closest('.side-group.collapsed, .side-archived')).slice(0, 9);
}

export function setAltHeld(on: boolean): void {
  if (!!on === altHeld) return;
  altHeld = !!on;
  publishSidebar({ altHeld });
}

/** Alt+1..9's row number, or 0. Keyed on e.code: macOS Option+1 reports e.key "¡". */
export function altRowNumber(e: KeyboardEvent): number {
  if (!e.altKey || e.ctrlKey || e.metaKey) return 0;
  const m = /^Digit([1-9])$/.exec(e.code || '');
  return m ? Number(m[1]) : 0;
}

const isAltOnly = (e: KeyboardEvent) => e.key === 'Alt' && !e.ctrlKey && !e.metaKey && !e.shiftKey;

/** Open the Nth visible row: a pane already open is focused, never minimized. */
export function openSidebarRow(n: number): boolean {
  const row = altRows()[n - 1];
  if (!row) return false;
  const name = row.dataset.agent ?? '';
  host.openPane(agentsNow().find(x => x.name === name) ?? { name });
  return true;
}

/** The ONE keydown handler for the dashboard and each pane's iframe (terminals.js). Returns
 * true when it consumed the key. */
export function altSwitchKeydown(e: KeyboardEvent): boolean {
  if (isAltOnly(e)) {
    setAltHeld(true);
    return false; // Alt itself must still reach a pane
  }
  const n = altRowNumber(e);
  return n ? openSidebarRow(n) : false;
}

// Capture phase, so nothing further down swallows Alt+N. Typing in a field keeps every key.
document.addEventListener('keydown', e => {
  const t = e.target as HTMLElement | null;
  if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
  if (document.getElementById('palette')?.classList.contains('open')) return;
  if (altSwitchKeydown(e)) e.preventDefault();
}, true);
document.addEventListener('keyup', e => { if (e.key === 'Alt') setAltHeld(false); }, true);
window.addEventListener('blur', () => setAltHeld(false));
document.addEventListener('visibilitychange', () => setAltHeld(false));

// --- Recent sessions (CMX-208): one-click resume of a session a tmux death orphaned --------
let recent: RecentState = { rows: [], dispatcher_rows: [], revealed: false, undo: null };
function setRecent(patch: Partial<RecentState>) {
  recent = { ...recent, ...patch };
  publish('recent', recent);
}

/** The session ids running in a window of this epoch (/api/agents). */
export const liveSessionIds = (): Set<string> =>
  new Set(agentsNow().map(a => a?.session_id).filter((s): s is string => !!s));

/** The /api/restore object ({rows, dispatcher_rows}), or a bare array of resumable rows. */
export function renderRecentSessions(data: unknown): void {
  const d: Record<string, unknown> = Array.isArray(data) ? { rows: data } : isObj(data) ? data : {};
  setRecent({
    rows: Array.isArray(d.rows) ? (d.rows as RecentRow[]) : [],
    dispatcher_rows: Array.isArray(d.dispatcher_rows) ? (d.dispatcher_rows as RecentRow[]) : [],
  });
}

export async function refreshRecentSessions(): Promise<void> {
  if (!TERMINALS_ON) return;
  try {
    renderRecentSessions(await api('/api/restore'));
  } catch { /* transient: keep the last render */ }
}

export function toggleDispatcherSessions(): void {
  setRecent({ revealed: !recent.revealed });
}

// Dismiss (CMX-437): the row leaves at once, the server records it (every device agrees), and
// an Undo toast offers it back. Nothing is deleted: not the transcript, not the bookkeeping.
const UNDO_MS = 6000;
let undoTimer: ReturnType<typeof setTimeout> | undefined;

export function closeRecentUndo(): void {
  clearTimeout(undoTimer);
  if (recent.undo) setRecent({ undo: null });
}

async function postDismiss(path: string, sids: string[]): Promise<boolean> {
  try {
    const res = await postJson(path, { session_ids: sids });
    return isObj(res) && !!res.ok;
  } catch {
    return false;
  }
}

async function dismiss(keys: string[], text: string) {
  const sids = keys.filter(Boolean);
  if (!sids.length) return;
  const gone = new Set(sids);
  const { rows, dispatcher_rows } = recent;
  const keep = (r: RecentRow) => !gone.has(dismissKey(r));
  setRecent({ rows: rows.filter(keep), dispatcher_rows: dispatcher_rows.filter(keep) });
  if (!(await postDismiss('/api/restore/dismiss', sids))) {
    setRecent({ rows, dispatcher_rows });
    say('Could not dismiss. Nothing was changed.');
    return;
  }
  clearTimeout(undoTimer);
  setRecent({ undo: { sids, text } });
  undoTimer = setTimeout(closeRecentUndo, UNDO_MS);
}

export function dismissRecentSession(key: string): Promise<void> {
  const r = [...recent.rows, ...recent.dispatcher_rows].find(x => dismissKey(x) === key);
  return dismiss([key], `Dismissed ${r ? r.label || r.cwd || r.wid : 'session'}`);
}

/** "Clear all": every resumable row the section shows. */
export async function clearRecentSessions(): Promise<void> {
  const sids = recentView(recent, liveSessionIds()).resumable.map(dismissKey).filter(Boolean);
  const n = sids.length;
  if (!n) return;
  const s = n === 1 ? '' : 's';
  if (typeof confirm === 'function' && !confirm(`Dismiss all ${n} recent session${s}? Transcripts are kept; Undo is offered briefly.`)) return;
  await dismiss(sids, `Dismissed ${n} session${s}`);
}

export async function undoDismissRecent(sids: string[]): Promise<void> {
  closeRecentUndo();
  if (!(await postDismiss('/api/restore/undismiss', sids))) say('Could not undo the dismiss.');
  await refreshRecentSessions();
}

/** Resume: `claude --resume <session>` in the row's cwd (/api/restore/resume, the launcher's
 * own window-open path). False when it failed (the row's button comes back). */
export async function resumeSession(r: RecentRow): Promise<boolean> {
  let res: unknown = null;
  try {
    res = await postJson('/api/restore/resume', {
      store: r.store, wid: r.wid, session_id: r.session_id ?? '', stamped_epoch: r.stamped_epoch || null,
    });
  } catch { /* handled below */ }
  if (!isObj(res) || res.error || res.ok === false) {
    say((isObj(res) && typeof res.error === 'string' && res.error)
      || 'Could not resume this session. It may have changed since the list was loaded.');
    void refreshRecentSessions();
    return false;
  }
  await refreshSidebar(); // the live list and Recent sessions both
  return true;
}
