// The Board segment of WORK: the project filter, the columns and the Hidden columns panel
// (CMX-136). Ported from static/js/kanban.js by CMX-233 (CMX-212 P12 + P13). It reads the
// payload the one /api/dispatcher poll published; it never fetches. CMX-315: on a phone the
// project is the title bar's (WorkView) and the layout is Rows (one list grouped by status) or
// Board (the status tabs ARE the swiped columns), both drawing the compact card.
import { useEffect, useRef, useState, type CSSProperties, type DragEvent, type KeyboardEvent } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { TextButton, useMediaQuery } from '../../../shared/ui/index.ts';
import type { WorkPayload } from '../../../shared/store/work.ts';
import { wfShort } from '../../taskmodal/model.ts';
import { boardColumns, pruneFilter, toggleProject, type Card as CardData, type Column } from '../board.ts';
import { hiddenSummary, loadColPrefs, saveColPrefs, splitColumns, type ColPrefs } from '../hidden.ts';
import { rowGroups } from '../phone.ts';
import { Card } from './Card.tsx';
import { CompactCard, RowItem } from './Compact.tsx';
import { LS, read, save, useWorkPrefs, type WorkPrefs } from './prefs.ts';
import { showToast } from './toasts.ts';

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

/** CMX-136: a card dropped on a hidden column. chela has no "move a card" API yet, so the drop
 * announces itself (`chela:kanban-move`) and says plainly that nothing moved; the follow-up
 * that writes to the tracker replaces only this function. */
export function moveCard(card: CardData, col: Column) {
  document.dispatchEvent(new CustomEvent('chela:kanban-move', { detail: { card, to: col.key, label: col.label } }));
  showToast(`Moving cards between columns is not wired to the tracker yet: ${card.task_id || card.title || 'this card'} was not moved to ${col.label}.`);
}

const onEnter = (fn: () => void) => (e: KeyboardEvent) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    fn();
  }
};

// A run (or task) keeps its key when a poll adds a card above it, so an open delete confirm or a
// failure note survives the repaint; only an id-less backlog bullet falls back to its position.
const cardKey = (c: CardData, i: number) => (c.task_id ? `${c.status}:${c.task_id}:${c.attempt ?? ''}` : `${c.status}:${i}:${c.title ?? ''}`);

type ColumnProps = {
  col: Column;
  pinned: boolean;
  onHide: () => void;
  onDragStart: (card: CardData, e: DragEvent) => void;
  onDragEnd: () => void;
};

function BoardColumn({ col, pinned, onHide, onDragStart, onDragEnd }: ColumnProps) {
  return (
    <div className={`kanban-col kanban-col-${col.key}`} data-col={col.key}>
      <div className="kanban-col-head">
        <span className="kanban-col-label">{col.label}</span>
        <span className="col-count">{col.cards.length}</span>
        {pinned && !col.cards.length && (
          <TextButton className="kanban-col-hide" title="Hide this column when it is empty" onClick={(e) => { e.stopPropagation(); onHide(); }}>Hide</TextButton>
        )}
      </div>
      <div className="kanban-cards">
        {col.cards.length ? (
          col.cards.map((c, i) => <Card key={cardKey(c, i)} card={c} onDragStart={onDragStart} onDragEnd={onDragEnd} />)
        ) : (
          <div className="kanban-empty-col">Nothing here</div>
        )}
      </div>
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

/** The #work-board pane. `live` is the latest payload; a drag in flight freezes what is drawn,
 * so a poll that lands mid-drag never moves a drop row under the cursor. `prefs` is the Work
 * view's (the title bar picks the project and the layout); a Board on its own keeps its own. */
export function Board({ live, active, prefs }: { live: WorkPayload | null; active: boolean; prefs?: WorkPrefs }) {
  const own = useWorkPrefs();
  const { filter: selected, pickFilter, layout } = prefs ?? own;
  const [frozen, setFrozen] = useState<WorkPayload | null>(null);
  const dragging = useRef<CardData | null>(null);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [colPrefs, setColPrefs] = useState<ColPrefs>(() => loadColPrefs(LS));
  const [hiddenOpen, setHiddenOpen] = useState(true);
  const [overRow, setOverRow] = useState<string | null>(null);
  const phone = useMediaQuery(PHONE);

  const data = frozen ?? live;
  const configured = !!(data?.configured && data.workflows?.length);
  const workflows = configured ? (data.workflows || []).map((wf) => wf.path) : [];
  const filter = pruneFilter(selected, workflows);
  const { visible, hidden } = splitColumns(configured ? boardColumns(data, filter) : [], colPrefs);

  function toggleCol(key: string) {
    const next = new Set(collapsed);
    if (!next.delete(key)) next.add(key);
    setCollapsed(next);
    save(COLLAPSED_KEY, JSON.stringify([...next]));
  }
  function pin(key: string, show: boolean) {
    const next: ColPrefs = Object.fromEntries(Object.entries(colPrefs).filter(([k]) => k !== key));
    if (show) next[key] = 'show';
    setColPrefs(next);
    saveColPrefs(LS, next);
  }
  function dragStart(card: CardData, e: DragEvent) {
    dragging.current = card;
    setFrozen(live);
    // Firefox starts no drag without data on the transfer.
    e.dataTransfer?.setData('text/plain', card.task_id || card.title || 'card');
  }
  function dragEnd() {
    dragging.current = null;
    setOverRow(null);
    setFrozen(null);
  }
  function drop(e: DragEvent, col: Column) {
    e.preventDefault();
    setOverRow(null);
    if (dragging.current) moveCard(dragging.current, col);
  }

  if (phone) {
    return (
      <div className={['work-pane', active && 'active', `work-phone-${layout}`].filter(Boolean).join(' ')} id="work-board" data-seg="board" data-layout={layout}>
        {data && !configured ? <Empty /> : layout === 'rows' ? <Rows columns={visible} collapsed={collapsed} onToggle={toggleCol} /> : <SwipeBoard columns={visible} />}
      </div>
    );
  }
  return (
    <div className={active ? 'work-pane active' : 'work-pane'} id="work-board" data-seg="board">
      <div className="kanban-filters" id="kanban-filters" role="group" aria-label="Projects">
        {configured && (
          <>
            <TextButton className={`kanban-filter-chip${filter.length ? '' : ' active'}`} aria-pressed={!filter.length} onClick={() => pickFilter([])}>
              All ({workflows.length})
            </TextButton>
            {workflows.map((wf) => (
              <TextButton key={wf} className={`kanban-filter-chip${filter.includes(wf) ? ' active' : ''}`} data-wf={wf} aria-pressed={filter.includes(wf)} title={wf}
                onClick={() => pickFilter(toggleProject(filter, wf))}>
                {wfShort(wf) || '?'}
              </TextButton>
            ))}
          </>
        )}
      </div>
      {data && !configured ? (
        <Empty />
      ) : (
        <div className="kanban-board-wrap">
          <div className="kanban-board" id="kanban-board" style={{ '--kanban-cols': String(visible.length) } as CSSProperties}>
            {configured && visible.map((c) => (
              <BoardColumn key={c.key} col={c} pinned={colPrefs[c.key] === 'show'} onHide={() => pin(c.key, false)} onDragStart={dragStart} onDragEnd={dragEnd} />
            ))}
          </div>
          {/* Next to the board, never inside it, so the swipe carousel's columns stay its only children. */}
          {configured && hidden.length > 0 && (
            <aside id="kanban-hidden" className="kanban-hidden-panel" aria-label="Hidden columns">
              <details className="kanban-hidden-details" open={hiddenOpen} onToggle={(e) => setHiddenOpen(e.currentTarget.open)}>
                <summary className="kanban-hidden-head">Hidden columns</summary>
                <ul className="kanban-hidden-rows">
                  {hidden.map((c) => (
                    <li key={c.key} className={`kanban-hidden-row kanban-col-${c.key}${overRow === c.key ? ' kanban-hidden-row-over' : ''}`} data-col={c.key} role="button" tabIndex={0}
                      title={`Show ${c.label} as a column`} onClick={() => pin(c.key, true)} onKeyDown={onEnter(() => pin(c.key, true))}
                      onDragOver={(e) => { if (!dragging.current) return; e.preventDefault(); setOverRow(c.key); }}
                      onDragLeave={() => setOverRow(null)} onDrop={(e) => drop(e, c)}>
                      <span className="kanban-hidden-dot" aria-hidden="true">●</span>
                      <span className="kanban-hidden-label">{c.label}</span>
                      <span className="col-count">{c.cards.length}</span>
                    </li>
                  ))}
                </ul>
              </details>
              <div className="kanban-hidden-inline">{hiddenSummary(hidden)}</div>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
