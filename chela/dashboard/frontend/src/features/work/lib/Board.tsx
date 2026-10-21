// The Board segment of WORK: the project filter, the phone layout controls, the columns and
// the Hidden columns panel (CMX-136). Ported from static/js/kanban.js by CMX-233 (CMX-212
// P12 + P13). It reads the payload the one /api/dispatcher poll published; it never fetches.
import { useRef, useState, type CSSProperties, type DragEvent, type KeyboardEvent } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { TextButton, useMediaQuery } from '../../../shared/ui/index.ts';
import type { WorkPayload } from '../../../shared/store/work.ts';
import { wfShort } from '../../taskmodal/model.ts';
import { FILTER_KEY, boardColumns, loadFilter, pruneFilter, toggleProject, type Card as CardData, type Column } from '../board.ts';
import { hiddenSummary, loadColPrefs, saveColPrefs, splitColumns, type ColPrefs } from '../hidden.ts';
import { Card } from './Card.tsx';
import { showToast } from './toasts.ts';

// Every storage call inside a lambda, so a missing or throwing localStorage stays inside the
// models' try/catch: storage failing is the default view, never a crash.
const LS = { getItem: (k: string) => localStorage.getItem(k), setItem: (k: string, v: string) => localStorage.setItem(k, v) };
function save(key: string, value: string) {
  try {
    LS.setItem(key, value);
  } catch {
    /* the choice holds for this page */
  }
}
function read(key: string): string | null {
  try {
    return LS.getItem(key);
  } catch {
    return null;
  }
}

const LAYOUT_KEY = 'chela_kanban_mlayout';
const COLLAPSED_KEY = 'chela_kanban_collapsed';
// The Rows accordion's low-traffic lanes start collapsed. Review is NOT here: a failed run
// lives there and must stay in view.
const DEFAULT_COLLAPSED = ['backlog', 'done', 'archived'];
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

type ColumnProps = {
  col: Column;
  collapsed: boolean;
  pinned: boolean;
  onToggle: () => void;
  onHide: () => void;
  onDragStart: (card: CardData, e: DragEvent) => void;
  onDragEnd: () => void;
};

function BoardColumn({ col, collapsed, pinned, onToggle, onHide, onDragStart, onDragEnd }: ColumnProps) {
  return (
    <div className={`kanban-col kanban-col-${col.key}${collapsed ? ' kanban-col-collapsed' : ''}`} data-col={col.key}>
      {/* A tap toggle in the phone Rows accordion only (onToggle no-ops wider). */}
      <div className="kanban-col-head" role="button" tabIndex={0} aria-expanded={!collapsed} onClick={onToggle} onKeyDown={onEnter(onToggle)}>
        <span className="kanban-col-caret" aria-hidden="true" />
        <span className="kanban-col-label">{col.label}</span>
        <span className="col-count">{col.cards.length}</span>
        {pinned && !col.cards.length && (
          <TextButton className="kanban-col-hide" title="Hide this column when it is empty" onClick={(e) => { e.stopPropagation(); onHide(); }}>Hide</TextButton>
        )}
      </div>
      <div className="kanban-cards">
        {col.cards.length ? (
          col.cards.map((c, i) => <Card key={`${c.task_id || c.title || ''}:${i}`} card={c} onDragStart={onDragStart} onDragEnd={onDragEnd} />)
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

/** The #work-board pane. `live` is the latest payload; a drag in flight freezes what is drawn,
 * so a poll that lands mid-drag never moves a drop row under the cursor. */
export function Board({ live, active }: { live: WorkPayload | null; active: boolean }) {
  const [frozen, setFrozen] = useState<WorkPayload | null>(null);
  const dragging = useRef<CardData | null>(null);
  const [selected, setSelected] = useState(() => loadFilter(LS));
  const [layout, setLayout] = useState<'swipe' | 'rows'>(() => (read(LAYOUT_KEY) === 'rows' ? 'rows' : 'swipe'));
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [prefs, setPrefs] = useState<ColPrefs>(() => loadColPrefs(LS));
  const [hiddenOpen, setHiddenOpen] = useState(true);
  const [overRow, setOverRow] = useState<string | null>(null);
  const [navOn, setNavOn] = useState<string | null>(null);
  const boardEl = useRef<HTMLDivElement>(null);
  const phone = useMediaQuery(PHONE);

  const data = frozen ?? live;
  const configured = !!(data?.configured && data.workflows?.length);
  const workflows = configured ? (data.workflows || []).map((wf) => wf.path) : [];
  const filter = pruneFilter(selected, workflows);
  const { visible, hidden } = splitColumns(configured ? boardColumns(data, filter) : [], prefs);

  function pickFilter(next: string[]) {
    setSelected(next);
    save(FILTER_KEY, JSON.stringify(next));
  }
  function pickLayout(next: 'swipe' | 'rows') {
    setLayout(next);
    save(LAYOUT_KEY, next);
  }
  function toggleCol(key: string) {
    if (!phone) return;
    const next = new Set(collapsed);
    if (!next.delete(key)) next.add(key);
    setCollapsed(next);
    save(COLLAPSED_KEY, JSON.stringify([...next]));
  }
  function pin(key: string, show: boolean) {
    const next: ColPrefs = Object.fromEntries(Object.entries(prefs).filter(([k]) => k !== key));
    if (show) next[key] = 'show';
    setPrefs(next);
    saveColPrefs(LS, next);
  }
  function navTo(key: string) {
    setNavOn(key);
    const el = [...(boardEl.current?.children ?? [])].find((c) => (c as HTMLElement).dataset.col === key);
    el?.scrollIntoView?.({ behavior: 'smooth', inline: 'start', block: 'nearest' });
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

  const cls = ['work-pane', active && 'active', `kanban-mobile-${layout}`].filter(Boolean).join(' ');
  return (
    <div className={cls} id="work-board" data-seg="board">
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
      {/* Phone only (style.css): Swipe = scroll-snap carousel, Rows = collapsible accordion. */}
      <div className="kanban-mobile-controls" id="kanban-mobile-controls">
        <div className="kanban-layout-toggle" role="group" aria-label="Mobile board layout">
          {(['swipe', 'rows'] as const).map((l) => (
            <TextButton key={l} className={`kanban-layout-btn${layout === l ? ' active' : ''}`} data-layout={l} aria-pressed={layout === l} onClick={() => pickLayout(l)}>
              {l === 'swipe' ? 'Swipe' : 'Rows'}
            </TextButton>
          ))}
        </div>
        <div className="kanban-nav-strip" id="kanban-nav-strip" role="group" aria-label="Jump to column">
          {configured && visible.map((c) => (
            <TextButton key={c.key} className={`kanban-nav-chip${navOn === c.key ? ' active' : ''}`} data-col={c.key} aria-current={navOn === c.key} onClick={() => navTo(c.key)}>
              <span className="kanban-nav-label">{c.label}</span>
              <span className="kanban-nav-count">{c.cards.length}</span>
            </TextButton>
          ))}
        </div>
      </div>
      {data && !configured ? (
        <Empty />
      ) : (
        <div className="kanban-board-wrap">
          <div className="kanban-board" id="kanban-board" ref={boardEl} style={{ '--kanban-cols': String(visible.length) } as CSSProperties}>
            {configured && visible.map((c) => (
              <BoardColumn key={c.key} col={c} collapsed={collapsed.has(c.key)} pinned={prefs[c.key] === 'show'} onToggle={() => toggleCol(c.key)} onHide={() => pin(c.key, false)}
                onDragStart={dragStart} onDragEnd={dragEnd} />
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
