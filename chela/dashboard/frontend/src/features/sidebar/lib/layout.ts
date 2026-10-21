// The layout (CMX-82): SERVER state, shared by every device. chela/sidebar_layout.py, behind
// /api/sidebar/layout: the group order, the custom groups and their membership, the View
// menu's choices and (CMX-278) the desktop width. Read on start and on every sidebar refresh;
// a change here is written there. `loaded` stays false until the first answer, and NOTHING is
// written back before it: a render off the defaults must never overwrite the layout a device
// saved. `gen` counts local writes: once loaded, a read that started before one is stale.
import { api } from '../../../shared/api/index.ts';
import type { SidebarLayout } from '../../../shared/bridge/index.ts';
import { clampWidth } from '../model.ts';
import { isObj, lsJson, lsRead, lsRemove, postJson, publish } from './page.ts';

// Pre-CMX-82 these were per browser; a browser that still holds them imports them ONCE.
const LEGACY_KEYS = { order: 'chela_sb_group_order', custom: 'chela_sb_custom_groups', view: 'chela_sb_view' } as const;

type Fields = Partial<Pick<SidebarLayout, 'order' | 'custom' | 'view' | 'width'>>;
let layout: SidebarLayout & { gen: number } = { order: [], custom: null, view: null, width: null, loaded: false, gen: 0 };

export const getLayout = (): Readonly<SidebarLayout> => layout;

/** The desktop column's width (style.css `--sidebar-w`). Not while the handle is being
 * dragged: the drag owns the column until it lets go (a poll must not snap it back). */
export function applyWidth(w: unknown): void {
  if (document.body.classList.contains('sidebar-resizing')) return;
  document.documentElement.style.setProperty('--sidebar-w', `${clampWidth(w)}px`);
}

function publishLayout() {
  const { order, custom, view, width, loaded } = layout;
  publish('sidebarLayout', { order, custom, view, width, loaded });
  applyWidth(width);
}

function setLayout(l: unknown) {
  const o = isObj(l) ? l : {};
  const before = layout;
  layout = {
    ...layout,
    order: Array.isArray(o.order) ? o.order.filter((k): k is string => typeof k === 'string') : [],
    custom: isObj(o.custom) ? o.custom : null,
    view: isObj(o.view) ? o.view : null,
    width: typeof o.width === 'number' ? o.width : null,
    loaded: true,
  };
  // Every tick re-reads the layout: publish (and repaint) only what changed.
  const same = (k: 'order' | 'custom' | 'view' | 'width') => JSON.stringify(before[k]) === JSON.stringify(layout[k]);
  if (before.loaded && same('order') && same('custom') && same('view') && same('width')) {
    layout = before;
    return;
  }
  publishLayout();
}

/** Keep the named fields at once (the next render shows them); send them once loaded. */
export function writeLayout(fields: Fields): void {
  layout = { ...layout, ...fields, gen: layout.gen + 1 };
  publishLayout();
  if (!layout.loaded) return;
  const gen = layout.gen;
  postJson('/api/sidebar/layout', fields)
    .then(res => { if (isObj(res) && res.layout && gen === layout.gen) setLayout(res.layout); })
    .catch(() => { /* transient: the next refresh re-reads the server's copy */ });
}

function legacyLayout(): Fields | null {
  const out: Fields = {};
  if (lsRead(LEGACY_KEYS.order, null) != null) {
    const o = lsJson(LEGACY_KEYS.order);
    out.order = Array.isArray(o) ? (o as string[]) : [];
  }
  const c = lsJson(LEGACY_KEYS.custom);
  if (isObj(c)) out.custom = c;
  const v = lsJson(LEGACY_KEYS.view);
  if (isObj(v)) out.view = v;
  return Object.keys(out).length ? out : null;
}

function dropLegacyLayout() {
  Object.values(LEGACY_KEYS).forEach(lsRemove);
}

// One-time import, only while the server has NO layout. The server writes it only then too,
// so of two racing devices the first wins and the second gets the winner's layout back.
async function migrateLayout(fresh: () => boolean): Promise<boolean> {
  const old = legacyLayout();
  if (!old) return false;
  const res = await postJson('/api/sidebar/layout', { import: true, ...old });
  if (!isObj(res) || !res.layout) return false;
  if (fresh()) setLayout(res.layout);
  dropLegacyLayout();
  return true;
}

async function readLayout() {
  const wasLoaded = layout.loaded, gen = layout.gen;
  const fresh = () => !wasLoaded || gen === layout.gen;
  let st: unknown;
  try {
    st = await api('/api/sidebar/layout');
  } catch {
    return; // keep the last answer
  }
  if (!isObj(st) || !('layout' in st)) return;
  if (st.layout) {
    if (fresh()) setLayout(st.layout);
    dropLegacyLayout(); // a layout exists: this browser's old one is never imported
    return;
  }
  try {
    if (await migrateLayout(fresh)) return;
  } catch { /* the next refresh retries */ }
  if (fresh()) setLayout(null); // nothing saved anywhere yet: the defaults
}

// One read at a time (CMX-89): two overlapping reads that both saw "nothing saved" raced the
// import. A caller during a read shares it.
let reading: Promise<void> | null = null;
export function refreshLayout(): Promise<void> {
  reading ??= readLayout().finally(() => { reading = null; });
  return reading;
}
