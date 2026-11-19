// CMX-276: the sidebar's session list (#sidebar-agents): Needs you, (CMX-390) Pinned, the
// groups in their order (Dispatched last), the Archived group, and their menus (CMX-277). What
// an action DOES is controller.ts's (through `actions`, CMX-278). CMX-390: a child session is
// drawn under its parent (`model.children`), in a `.tree-kids` box with a guide line.
import { useEffect, useRef, useState, type CSSProperties, type MouseEvent, type PointerEvent, type ReactNode } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { TextButton } from '../../../shared/ui/index.ts';
import {
  ARCHIVED_SECTION_KEY, DISPATCHED_KEY, type Group, INDENT_LEVELS, type Item, NEEDS_YOU_KEY, PINNED_KEY, type SidebarState,
  assignKey, canDragGroup, canOrderRows, descendants, dropAssignee, folderLines, isSingleFolder, kidsKey, showsFolderLine,
  sidebarModel, treeKey, visibleRows,
} from '../model.ts';
import type { SidebarActions } from './actions.ts';
import { type DragSource, type DragView, useSidebarDrag } from './drag.ts';
import { GroupMenu, RowMenu, type RowGroup, useMenuOpen } from './menus.tsx';
import { type Meta, type MetaKind, ago, rowView } from './rowview.ts';

export type { SidebarActions } from './actions.ts';

const META_ICON: Partial<Record<MetaKind, (typeof icons)[keyof typeof icons]>> = {
  orchestrator: icons.orchestrator,
  pr: icons.pullRequest,
  judging: icons.judging,
  account: icons.account,
};

function MetaChip({ m }: { m: Meta }) {
  const Icon = META_ICON[m.kind];
  return (
    <span className={m.cls} data-meta={m.kind} role="img" aria-label={m.title} title={m.title}>
      {Icon && <Icon size={12} aria-hidden="true" />}
      {m.text && <span className="ar-meta-text">{m.text}</span>}
    </span>
  );
}

// An archived row's way back (CMX-75): on a closed session it RESUMES it. Disabled at once,
// so a slow resume is not double-clicked into two windows.
function Unarchive({ itemKey, resume, actions }: { itemKey: string; resume: boolean; actions: SidebarActions }) {
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);   // a second click before the re-render must not resume twice
  const what = resume ? 'Unarchive: resume this session in a new window' : 'Unarchive';
  const click = (e: MouseEvent) => {
    e.stopPropagation();
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    void Promise.resolve(actions.unarchiveRow(null, itemKey)).finally(() => {
      inFlight.current = false;
      setBusy(false);
    });
  };
  return (
    <TextButton className="row-unarchive" data-item={itemKey} title={what} aria-label={what} disabled={busy} onClick={click}>
      {busy ? (resume ? 'Resuming…' : 'Unarchiving…') : 'Unarchive'}
    </TextButton>
  );
}

// CMX-278: a long press shows the whole row: the title wraps instead of truncating (a phone
// has no hover or tooltip; on a pointer the row's tooltip does it, CMX-331). The long
// press's own click does not also open the session.
export const LONG_PRESS_MS = 500;
function useLongPress() {
  const [full, setFull] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const fired = useRef(false);
  const cancel = () => clearTimeout(timer.current);
  useEffect(() => cancel, []);
  return {
    full,
    /** True once for the click that ends a long press: swallow it. */
    consumed: () => {
      const f = fired.current;
      fired.current = false;
      return f;
    },
    handlers: {
      onTouchStart: () => {
        cancel();
        fired.current = false;
        setFull(false);
        timer.current = setTimeout(() => {
          fired.current = true;
          setFull(true);
        }, LONG_PRESS_MS);
      },
      onTouchMove: cancel,
      onTouchEnd: cancel,
    },
  };
}

interface RowProps {
  it: Item;
  s: SidebarState;
  actions: SidebarActions;
  archived?: boolean;
  altNum?: number;
  /** CMX-377: the dim second line naming the row's folder; absent when grouped by folder. */
  folder?: string;
  /** CMX-379: a single-session folder's row carries its (absent) header's actions. */
  group?: RowGroup;
  /** CMX-380: what dragging this row moves (its folder's place, or itself to a group). */
  drag?: Drag;
  /** CMX-390: how far under its top-level ancestor this row is (0: a top-level row). */
  depth?: number;
  /** CMX-390: a parent row's chevron: its subtree's size, whether it is shown, the toggle. */
  tree?: TreeToggle;
  children?: ReactNode;
}

/** CMX-390: a parent row's chevron and child count. */
interface TreeToggle { count: number; open: boolean; toggle: () => void }

// CMX-390: the chevron that folds a parent's children (per device, keyed by its session).
function TreeButton({ t }: { t: TreeToggle }) {
  const what = `${t.open ? 'Hide' : 'Show'} ${t.count} child session${t.count === 1 ? '' : 's'}`;
  return (
    <TextButton className="tree-toggle" aria-expanded={t.open} aria-label={what} title={what}
      onClick={(e: MouseEvent) => { e.stopPropagation(); t.toggle(); }}>
      <span className="tree-count">{t.count}</span>
      <span className="tree-caret"><icons.expand size={12} aria-hidden="true" /></span>
    </TextButton>
  );
}

/** CMX-380: a draggable row or header's pointerdown, and whether it is the one being dragged.
 * CMX-381: `drop` is the line a row reordered within its group lands on (this row's side). */
interface Drag { onPointerDown: (e: PointerEvent<HTMLElement>) => void; dragging: boolean; drop?: 'before' | 'after' }

// Shown on hover (style.css), never on the folded icon rail.
const Grip = () => <span className="drag-grip" aria-hidden="true"><icons.grip size={12} /></span>;

function Row({ it, s, actions, archived = false, altNum, folder, group, drag, depth = 0, tree, children }: RowProps) {
  const menu = useMenuOpen();
  const press = useLongPress();
  const v = rowView(it, s);
  if (!v) return null;
  const name = v.agent.name ?? '';
  // A plain shell already shows its live cwd as a chip (CMX-330): no second copy under it.
  const hasCwd = v.meta.some(m => m.kind === 'cwd');
  if (hasCwd) folder = undefined;
  const cls = ['agent-row', 'rich', name === s.active && 'active', v.onWall && 'on-wall',
    v.runId && 'run-row', archived && 'archived', press.full && 'show-full', folder && 'has-folder',
    drag && 'draggable', drag?.dragging && 'dragging', drag?.drop && `drop-${drag.drop}`, depth > 0 && 'nested',
    tree && 'tree-parent'].filter(Boolean).join(' ');
  const line = (
    <>
      <span className={`term-status-dot ${v.dotCls}`} role="img" aria-label={v.dotLabel} title={v.dotLabel} />
      {/* no title of its own (CMX-331): hovering the name shows the row's tooltip, which opens
          with the full title, then the window key */}
      <span className="agent-row-name" data-key={v.windowKey || undefined}>{v.label}</span>
      {/* CMX-399: only a shell's strip (its cwd chip) may shrink to nothing (style.css) */}
      <span className={hasCwd ? 'ar-meta has-cwd' : 'ar-meta'}>
        {v.meta.map(m => <MetaChip key={m.kind} m={m} />)}
        {/* CMX-417: the `@N` that chela peek and the inbox use, shown on hover */}
        {v.agent.window_id && <span className="ar-more"><span className="ar-wid">{v.agent.window_id}</span></span>}
      </span>
      {tree && <TreeButton t={tree} />}
      <RowMenu it={it} s={s} actions={actions} menu={menu} group={group} archived={archived} />
      {children}
    </>
  );
  return (
    <div className={cls} data-agent={name} data-run={v.runId ?? undefined} data-item={it.key} title={v.title}
      onClick={() => { if (!press.consumed()) actions.selectAgent(name); }} onContextMenu={menu.onContext} {...press.handlers}
      onPointerDown={drag?.onPointerDown}>
      {/* CMX-377: with a folder line, the first line keeps exactly what a one-line row has */}
      {folder ? <span className="ar-line">{line}</span> : line}
      {folder && <span className="ar-folder-line">{folder}</span>}
      {altNum ? <span className="ar-altnum" aria-hidden="true">{altNum}</span> : null}
      {drag && <Grip />}
    </div>
  );
}

// A CLOSED session's row (CMX-75): no window to open, only its resume record.
function SessionRow({ it, s, actions, depth = 0, tree }: {
  it: Item; s: SidebarState; actions: SidebarActions; depth?: number; tree?: TreeToggle;
}) {
  const r = it.record ?? { key: it.key };
  const label = r.name || r.session_id || it.key;
  const folder = String(r.cwd || '').replace(/\/+$/, '').split('/').pop() || r.cwd || '';
  const when = r.archived_at ? `archived ${ago(Math.max(0, s.now / 1000 - r.archived_at))} ago` : '';
  const title = [`${label}: archived (closed)`, r.cwd, when].filter(Boolean).join('\n');
  return (
    <div className={`agent-row rich archived session-row${depth > 0 ? ' nested' : ''}${tree ? ' tree-parent' : ''}`}
      data-item={it.key} title={title}>
      <span className="term-status-dot idle" role="img" aria-label="Session: archived" title="Session: archived" />
      <span className="agent-row-name">{label}</span>
      {folder && <span className="ar-meta"><span className="ar-folder">{folder}</span></span>}
      {tree && <TreeButton t={tree} />}
      <Unarchive itemKey={it.key} resume actions={actions} />
    </div>
  );
}

/** How a tree draws one of its rows: the row, its depth, its chevron (a parent), and its
 * parent (a child; null for a top-level row). */
type DrawRow = (it: Item, depth: number, tree: TreeToggle | undefined, parent: Item | null) => ReactNode;

// CMX-390: a row and, unless folded, its children under it (and theirs, one level further in).
function Tree({ it, depth, s, actions, kids, draw, parent = null }: {
  it: Item; depth: number; s: SidebarState; actions: SidebarActions; kids: Record<string, Item[]>; draw: DrawRow;
  parent?: Item | null;
}) {
  const list = kids[it.key] ?? [];
  if (!list.length) return <>{draw(it, depth, undefined, parent)}</>;
  const key = treeKey(it);
  const open = !s.collapsed.includes(key);
  const tree = { count: descendants(it, kids).length, open, toggle: () => actions.toggleGroup(key) };
  return (
    <>
      {draw(it, depth, tree, parent)}
      {open && (
        <div className="tree-kids" data-list={kidsKey(it)} data-depth={Math.min(depth + 1, INDENT_LEVELS)}
          style={{ '--tree-depth': Math.min(depth + 1, INDENT_LEVELS) } as CSSProperties}>
          {list.map(k => <Tree key={k.key} it={k} depth={depth + 1} s={s} actions={actions} kids={kids} draw={draw} parent={it} />)}
        </div>
      )}
    </>
  );
}

interface GroupProps {
  g: Group;
  index: number;
  count: number;
  s: SidebarState;
  actions: SidebarActions;
  alt: Map<string, number>;
  folders: Record<string, string>;
  drag: ReturnType<typeof useSidebarDrag>;
  kids: Record<string, Item[]>;
}

/** CMX-377's folder line, for a child (CMX-390): shown when the section shows them, else only
 * when it is not its parent's folder (grouped by folder, the header names the parent's). */
function childFolder(it: Item, parent: Item | null, lines: boolean, folders: Record<string, string>): string | undefined {
  const f = folders[it.key];
  if (lines || !parent) return lines ? f : undefined;
  return f !== folders[parent.key] ? f : undefined;
}

const sameSource = (a: DragSource | undefined, b: DragSource) =>
  !!a && a.kind === b.kind && (a.kind === 'group' ? a.key === (b as typeof a).key : a.item === (b as typeof a).item);

/** The drop classes a section draws while a drag runs. */
function dropCls(key: string, v: DragView | null): string {
  if (!v) return '';
  const out: string[] = [];
  if (v.line?.key === key) out.push(`drop-${v.line.side}`);
  if (v.over?.key === key) out.push(v.over.ok ? 'drop-target' : 'drop-reject');
  return out.map(c => ` ${c}`).join('');
}

/** CMX-390: a child row drags only among its siblings, under Manual (same modes as a group's rows). */
function childDrag(it: Item, parent: Item, section: string, s: SidebarState, kids: Record<string, Item[]>,
  handle: (src: DragSource) => Drag, rowLine: DragView['rowLine']): Drag | undefined {
  const list = kidsKey(parent);
  if (!canOrderRows(s.view, list) || (kids[parent.key]?.length ?? 0) < 2) return undefined;
  const d = handle({ kind: 'row', assign: assignKey(it), group: section, item: it.key, reorder: true, list });
  return rowLine?.key === it.key ? { ...d, drop: rowLine.side } : d;
}

function GroupView({ g, index, count, s, actions, alt, folders, drag, kids }: GroupProps) {
  const menu = useMenuOpen();
  // CMX-379: a folder with one session is one bare row naming its folder, never collapsed.
  const single = isSingleFolder(g, s.view.groupBy);
  const lines = single || showsFolderLine(s.view.groupBy);
  // CMX-380: a group moves by its header, a bare folder by its row; under Custom groups a
  // session row drags onto another group.
  const movable = canDragGroup(g);
  const groupSrc: DragSource = { kind: 'group', key: g.key };
  const handle = (src: DragSource): Drag => ({ onPointerDown: drag.bind(src), dragging: sameSource(drag.view?.source, src) });
  // CMX-381: under Manual (by folder, custom group or none) a row also drags within its group.
  const reorder = canOrderRows(s.view, g.key) && g.items.length > 1;
  const rowLine = drag.view?.rowLine;
  const rowDrag = (it: Item): Drag | undefined => {
    if (single) return movable ? handle(groupSrc) : undefined;
    if (s.view.groupBy !== 'custom' && !reorder) return undefined;
    const d = handle({ kind: 'row', assign: assignKey(it), group: g.key, item: it.key, reorder });
    return rowLine?.key === it.key ? { ...d, drop: rowLine.side } : d;
  };
  const draw: DrawRow = (it, depth, tree, parent) => (
    <Row key={it.key} it={it} s={s} actions={actions} altNum={alt.get(it.key)} depth={depth} tree={tree}
      folder={depth ? childFolder(it, parent, lines && !single, folders) : lines ? folders[it.key] : undefined}
      group={single && !depth ? { g, index, count } : undefined}
      drag={parent ? childDrag(it, parent, g.key, s, kids, handle, rowLine) : rowDrag(it)} />
  );
  const rows = g.items.map(it => <Tree key={it.key} it={it} depth={0} s={s} actions={actions} kids={kids} draw={draw} />);
  if (g.flat) {
    // Group by ▸ None: one flat list, no header.
    return <div className={`side-group flat${dropCls(g.key, drag.view)}`} data-g={g.key}><div className="group-rows">{rows}</div></div>;
  }
  const collapsed = !single && s.collapsed.includes(g.key);
  const n = g.items.length;
  const stop = (f: (e: MouseEvent) => void) => (e: MouseEvent) => {
    e.stopPropagation();
    f(e);
  };
  const dragging = movable && sameSource(drag.view?.source, groupSrc);
  return (
    <div className={`side-group${collapsed ? ' collapsed' : ''}${n ? '' : ' empty'}${single ? ' single' : ''}${dragging ? ' dragging' : ''}${dropCls(g.key, drag.view)}`}
      data-g={g.key}>
      {/* the header slot stays (as `false`) when there is none, so the row keeps its DOM node
          when a folder goes from 1 to 2 sessions or back (CMX-379) */}
      {!single && <div className={`group-head${movable ? ' draggable' : ''}`} data-g={g.key} aria-expanded={!collapsed}
        onClick={() => actions.toggleGroup(g.key)} onContextMenu={menu.onContext}
        onPointerDown={movable ? drag.bind(groupSrc) : undefined}>
        {movable && <Grip />}
        <span className="group-name" title={g.cwd || g.label}>{g.label}</span>
        {g.key === DISPATCHED_KEY && <span className="group-count" title={`${n} run${n === 1 ? '' : 's'}`}>{n}</span>}
        <span className="group-caret"><icons.expand size={12} aria-hidden="true" /></span>
        <span className="group-actions">
          <GroupMenu g={g} index={index} count={count} s={s} actions={actions} menu={menu} />
          {g.cwd && (
            <TextButton className="group-add" data-g={g.key} title={`New session in ${g.label}`}
              aria-label={`New session in ${g.label}`} onClick={stop(() => actions.groupNewSession(g.key))}>
              <icons.add size={14} aria-hidden="true" />
            </TextButton>
          )}
        </span>
      </div>}
      <div className="group-rows">{rows}</div>
    </div>
  );
}

// CMX-85: Alt+1..9 numbers the first nine rows a user can see, top to bottom: Needs you,
// then each group that is not collapsed (a single-session folder row never is, CMX-379).
// The Archived section is not numbered.
// CMX-390: then Pinned, and a parent's children under it unless it is folded.
function altNumbers(needsYou: Item[], pinned: Item[], groups: Group[], kids: Record<string, Item[]>, s: SidebarState): Map<string, number> {
  const out = new Map<string, number>();
  if (!s.altHeld) return out;
  const shown = (g: Group) => g.flat || isSingleFolder(g, s.view.groupBy) || !s.collapsed.includes(g.key);
  const visible = visibleRows([...needsYou, ...pinned, ...groups.filter(shown).flatMap(g => g.items)], kids, s.collapsed);
  for (const it of visible.filter(it => rowView(it, s)).slice(0, 9)) out.set(it.key, out.size + 1);
  return out;
}

export function Sidebar({ state: s, actions }: { state: SidebarState | null; actions: SidebarActions }) {
  // CMX-380: what a drop writes is the controller's: the same order Move up / Move down
  // write, the same assignment Move to group writes.
  const drag = useSidebarDrag({
    group: (key, before) => actions.dropGroup(key, before),
    accepts: key => !!s && s.view.groupBy === 'custom' && dropAssignee(key, s.custom) !== undefined,
    row: (assign, key) => actions.dropOnGroup(assign, key),
    reorder: (key, item, before) => actions.dropRow(key, item, before),
  });
  if (!s || !s.agents.length) return <div className="side-empty">No agents</div>;
  const model = sidebarModel(s);
  const kids = model.children;
  const alt = altNumbers(model.needsYou, model.pinned, model.groups, kids, s);
  const showArchived = s.view.status !== 'active';
  const archivedOpen = s.view.status === 'archived' || s.collapsed.includes(`${ARCHIVED_SECTION_KEY}:open`);
  const nArch = model.archived.length;
  const drawsArchive = showArchived && nArch > 0;
  // Every row's folder line; grouped by folder only a single-session folder's row draws its
  // own (CMX-379), so Needs you and Archived draw theirs only when not grouped by folder.
  const roots = [...model.needsYou, ...model.pinned, ...model.groups.flatMap(g => g.items), ...model.archived];
  const all = folderLines(roots.flatMap(it => [it, ...descendants(it, kids)]));
  const lines = showsFolderLine(s.view.groupBy);
  const folders = lines ? all : {};
  // Needs you and Pinned: a row and its subtree, no drag but a child's among its siblings
  const triage = (section: string): DrawRow => (it, depth, tree, parent) => {
    const rowLine = drag.view?.rowLine;
    const handle = (src: DragSource): Drag => ({ onPointerDown: drag.bind(src), dragging: sameSource(drag.view?.source, src) });
    return (
      <Row key={it.key} it={it} s={s} actions={actions} altNum={alt.get(it.key)} depth={depth} tree={tree}
        folder={depth ? childFolder(it, parent, lines, all) : folders[it.key]}
        drag={parent && section === PINNED_KEY ? childDrag(it, parent, section, s, kids, handle, rowLine) : undefined} />
    );
  };
  const nPinned = model.pinned.reduce((n, it) => n + 1 + descendants(it, kids).length, 0);
  return (
    <>
      {model.needsYou.length > 0 && (
        <div className={`side-triage side-needs-you${dropCls(NEEDS_YOU_KEY, drag.view)}`}>
          <div className="triage-head">Needs you <span className="triage-count">{model.needsYou.length}</span></div>
          {model.needsYou.map(it => <Tree key={it.key} it={it} depth={0} s={s} actions={actions} kids={kids} draw={triage(NEEDS_YOU_KEY)} />)}
        </div>
      )}
      {model.pinned.length > 0 && (
        <div className={`side-triage side-pinned${dropCls(PINNED_KEY, drag.view)}`} data-g={PINNED_KEY}>
          <div className="triage-head">
            <icons.pin size={12} aria-hidden="true" /> Pinned <span className="triage-count">{nPinned}</span>
          </div>
          {model.pinned.map(it => <Tree key={it.key} it={it} depth={0} s={s} actions={actions} kids={kids} draw={triage(PINNED_KEY)} />)}
        </div>
      )}
      {model.groups.map((g, i) => (
        <GroupView key={g.key} g={g} index={i} count={model.groups.length} s={s} actions={actions} alt={alt}
          folders={all} drag={drag} kids={kids} />
      ))}
      {drawsArchive && (
        <div className={`side-group side-archived${archivedOpen ? '' : ' collapsed'}${dropCls(ARCHIVED_SECTION_KEY, drag.view)}`}
          data-g={ARCHIVED_SECTION_KEY}>
          <div className="group-head" data-g={ARCHIVED_SECTION_KEY} aria-expanded={archivedOpen}
            onClick={() => actions.toggleArchivedSection()}>
            <span className="group-name">Archived</span>
            <span className="group-count archived-count" title={`${nArch} archived`}>{nArch}</span>
            <span className="group-caret"><icons.expand size={12} aria-hidden="true" /></span>
          </div>
          <div className="group-rows">
            {model.archived.map(r => <Tree key={r.key} it={r} depth={0} s={s} actions={actions} kids={kids} draw={(it, depth, tree) => (
              it.kind === 'session'
                ? <SessionRow key={it.key} it={it} s={s} actions={actions} depth={depth} tree={tree} />
                : (
                  <Row key={it.key} it={it} s={s} actions={actions} archived folder={folders[it.key]} depth={depth} tree={tree}>
                    <Unarchive itemKey={it.key} resume={false} actions={actions} />
                  </Row>
                ))} />)}
          </div>
        </div>
      )}
      {!model.needsYou.length && !model.pinned.length && !model.groups.length && !drawsArchive
        && <div className="side-empty">No sessions match this view</div>}
    </>
  );
}
