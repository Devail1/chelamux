// The Board segment of WORK. Ported from static/js/kanban.js by CMX-233 (CMX-212 P12 + P13).
// It reads the payload the one /api/dispatcher poll published; it never fetches. The project is
// the title bar's (WorkView), and every layout draws the compact card (Compact.tsx).
// CMX-315, on a phone: Rows (one list grouped by status) or Board (the status tabs ARE the
// swiped columns). CMX-316, wider: Board, with Todo, In Progress and In Review as fixed-width
// columns and Backlog and Done collapsed to rails at the edges (each remembers whether it was
// left open, per project view), or Rows, a sortable table (RowsTable.tsx).
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { IconButton, TextButton, useMediaQuery } from '../../../shared/ui/index.ts';
import type { WorkPayload } from '../../../shared/store/work.ts';
import { boardColumns, cardKey, pruneFilter, type Column } from '../board.ts';
import { arrange, loadRails, saveRails, shownColumns, viewKey, type Side } from '../desktop.ts';
import { loadColPrefs, splitColumns } from '../hidden.ts';
import { rowGroups } from '../phone.ts';
import { CompactCard, RowItem } from './Compact.tsx';
import { LS, read, save, useWorkPrefs, type WorkPrefs } from './prefs.ts';
import { RowsTable } from './RowsTable.tsx';

const COLLAPSED_KEY = 'chela_kanban_collapsed';
// The Rows list's low-traffic groups start collapsed. Review is NOT here: a failed run lives
// there and must stay in view.
const DEFAULT_COLLAPSED = ['backlog', 'done', 'archived', 'tr-backlog', 'tr-done'];
const PHONE = '(max-width: 768px)';

function loadCollapsed(): Set<string> {
  try {
    const raw = read(COLLAPSED_KEY);
    const v: unknown = raw === null ? DEFAULT_COLLAPSED : JSON.parse(raw);
    return new Set(Array.isArray(v) ? v.filter((k): k is string => typeof k === 'string') : DEFAULT_COLLAPSED);
  } catch {
    return new Set(DEFAULT_COLLAPSED);
  }
}

const onEnter = (fn: () => void) => (e: KeyboardEvent) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    fn();
  }
};

/** A column of the desktop board. An edge column (Backlog, Done) that is open carries the
 * control that folds it back to its rail. */
function BoardColumn({ col, edge, onCollapse }: { col: Column; edge?: Side; onCollapse?: () => void }) {
  return (
    <section className={`kanban-col kanban-col-${col.key}${edge ? ` kanban-col-edge kanban-col-edge-${edge}` : ''}`} data-col={col.key} aria-label={col.label}>
      <div className="kanban-col-head">
        <span className="kanban-col-label">{col.label}</span>
        <span className="col-count">{col.cards.length}</span>
        {onCollapse && (
          <IconButton className="kanban-col-collapse" label={`Collapse ${col.label}`} aria-expanded onClick={onCollapse}>
            <icons.close size={14} aria-hidden />
          </IconButton>
        )}
      </div>
      <div className="kanban-cards">
        {col.cards.length ? col.cards.map((c, i) => <CompactCard key={cardKey(c, i)} card={c} />) : <div className="kanban-empty-col">Nothing here</div>}
      </div>
    </section>
  );
}

/** A collapsed edge column: its count over its name, written down the rail. A click opens it. */
function Rail({ col, side, onExpand }: { col: Column; side: Side; onExpand: () => void }) {
  const n = col.cards.length;
  return (
    <TextButton className={`kanban-rail kanban-rail-${side} kanban-col-${col.key}`} data-col={col.key} aria-expanded={false}
      aria-label={`Expand ${col.label}, ${n} ${n === 1 ? 'task' : 'tasks'}`} title={`Expand ${col.label}`} onClick={onExpand}>
      <span className="kanban-rail-count">{n}</span>
      <span className="kanban-rail-label">{col.label}</span>
    </TextButton>
  );
}

/** The desktop board: the left edge, the fixed columns (which scroll sideways when there are
 * many), the right edge. The edges never scroll away, so Done is never cut off. */
function DeskBoard({ columns, view }: { columns: Column[]; view: string }) {
  // Keyed by `view` (the caller's), so another project view reads back its own rails.
  const [open, setOpen] = useState(() => loadRails(LS, view));
  function toggle(key: string) {
    const next = open.includes(key) ? open.filter((k) => k !== key) : [...open, key];
    setOpen(next);
    saveRails(LS, view, next);
  }
  const { left, middle, right } = arrange(columns);
  const edge = (side: Side) => (c: Column) =>
    open.includes(c.key) ? <BoardColumn key={c.key} col={c} edge={side} onCollapse={() => toggle(c.key)} /> : <Rail key={c.key} col={c} side={side} onExpand={() => toggle(c.key)} />;
  return (
    <div className="kanban-board" id="kanban-board">
      {left.map(edge('left'))}
      <div className="kanban-middle">
        {middle.map((c) => <BoardColumn key={c.key} col={c} />)}
      </div>
      {right.map(edge('right'))}
    </div>
  );
}

function Empty() {
  return (
    <div id="kanban-empty" className="work-empty">
      <div className="work-empty-icon"><icons.board aria-hidden size={30} /></div>
      <div className="work-empty-title">A board for work in flight</div>
      <div className="work-empty-body">
        The Kanban board tracks dispatched work across all your <code>WORKFLOW.md</code> files: open tasks, agents running now, PRs awaiting review, and
        done, all in one place. It fills in once the dispatcher has workflows to watch.
      </div>
      <div className="work-empty-hint">
        Enable it: set <code>CHELA_DISPATCH_WORKFLOWS</code> to a colon-separated list of <code>WORKFLOW.md</code> paths, then restart the daemon.
      </div>
    </div>
  );
}

/** CMX-315: the phone Rows layout: one list grouped by status, in reading order (work in
 * flight first), each group's head sticky with its count and a tap folding it. */
function Rows({ columns, collapsed, onToggle }: { columns: Column[]; collapsed: Set<string>; onToggle: (key: string) => void }) {
  const groups = rowGroups(columns);
  if (!groups.length) return <div className="kanban-empty-col">Nothing here</div>;
  return (
    <div className="wrows" id="work-rows">
      {groups.map((g) => {
        const folded = collapsed.has(g.key);
        return (
          <section key={g.key} className={`wrows-group${folded ? ' folded' : ''}`} data-col={g.key} aria-label={g.label}>
            <div className="wrows-head" role="button" tabIndex={0} aria-expanded={!folded} onClick={() => onToggle(g.key)} onKeyDown={onEnter(() => onToggle(g.key))}>
              <span className="wrows-label">{g.label}</span>
              <span className="wrows-count">{g.cards.length}</span>
              <icons.expand className="wrows-caret" aria-hidden size={14} />
            </div>
            {!folded && g.cards.map((c, i) => <RowItem key={cardKey(c, i)} card={c} />)}
          </section>
        );
      })}
    </div>
  );
}

/** CMX-315: the phone Board: the status tabs ARE the columns, one full screen wide, swiped
 * (scroll snap); the active tab is underlined and follows the swipe. No column header and no
 * peek of the next column. */
function SwipeBoard({ columns }: { columns: Column[] }) {
  const boardEl = useRef<HTMLDivElement>(null);
  const tabsEl = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState(0);
  const idx = Math.max(0, Math.min(on, columns.length - 1));
  useEffect(() => {
    tabsEl.current?.children[idx]?.scrollIntoView?.({ behavior: 'smooth', inline: 'nearest', block: 'nearest' });
  }, [idx]);
  function go(i: number) {
    setOn(i);
    boardEl.current?.children[i]?.scrollIntoView?.({ behavior: 'smooth', inline: 'start', block: 'nearest' });
  }
  function swiped() {
    const b = boardEl.current;
    if (b && b.clientWidth > 0) setOn(Math.round(b.scrollLeft / b.clientWidth));
  }
  if (!columns.length) return <div className="kanban-empty-col">Nothing here</div>;
  return (
    <>
      <div className="wtabs" id="kanban-nav-strip" ref={tabsEl} role="group" aria-label="Status columns">
        {columns.map((c, i) => (
          <TextButton key={c.key} className={`wtab${i === idx ? ' active' : ''}`} data-col={c.key} aria-current={i === idx} onClick={() => go(i)}>
            <span className="wtab-label">{c.label}</span> <span className="wtab-count">{c.cards.length}</span>
          </TextButton>
        ))}
      </div>
      <div className="kanban-board wboard" id="kanban-board" ref={boardEl} onScroll={swiped}>
        {columns.map((c) => (
          <div key={c.key} className={`wcol kanban-col-${c.key}`} data-col={c.key}>
            {c.cards.length ? c.cards.map((card, i) => <CompactCard key={cardKey(card, i)} card={card} />) : <div className="kanban-empty-col">Nothing here</div>}
          </div>
        ))}
      </div>
    </>
  );
}

/** The #work-board pane. `live` is the latest payload; `prefs` is the Work view's (the title
 * bar picks the project and the layout); a Board on its own keeps its own. */
export function Board({ live: data, active, prefs }: { live: WorkPayload | null; active: boolean; prefs?: WorkPrefs }) {
  const own = useWorkPrefs();
  const { filter: selected, layout, deskLayout, hiddenCols } = prefs ?? own;
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const phone = useMediaQuery(PHONE);

  const configured = !!(data?.configured && data.workflows?.length);
  const workflows = configured ? (data.workflows || []).map((wf) => wf.path) : [];
  const filter = pruneFilter(selected, workflows);
  const columns = configured ? boardColumns(data, filter) : [];

  function toggleCol(key: string) {
    const next = new Set(collapsed);
    if (!next.delete(key)) next.add(key);
    setCollapsed(next);
    save(COLLAPSED_KEY, JSON.stringify([...next]));
  }

  if (phone) {
    // CMX-136: a phone leaves an empty column out (unless the viewer pinned it).
    const { visible } = splitColumns(columns, loadColPrefs(LS));
    return (
      <div className={['work-pane', active && 'active', `work-phone-${layout}`].filter(Boolean).join(' ')} id="work-board" data-seg="board" data-layout={layout}>
        {/* Nothing before the first poll: an empty list would read as an empty project. */}
        {!data ? null : !configured ? <Empty /> : layout === 'rows' ? <Rows columns={visible} collapsed={collapsed} onToggle={toggleCol} /> : <SwipeBoard columns={visible} />}
      </div>
    );
  }
  const shown = shownColumns(columns, hiddenCols);
  return (
    <div className={active ? 'work-pane active' : 'work-pane'} id="work-board" data-seg="board" data-layout={deskLayout}>
      {!data ? null : !configured ? (
        <Empty />
      ) : deskLayout === 'rows' ? (
        <RowsTable columns={shown} collapsed={collapsed} onToggle={toggleCol} />
      ) : (
        <DeskBoard key={viewKey(filter)} columns={shown} view={viewKey(filter)} />
      )}
    </div>
  );
}
