// CMX-316 (Liav's approved mockup, 10-23): the desktop Rows layout, a table. One row per card
// with the columns ID, title, state, PR, CI, risk and age, grouped by status in the phone Rows'
// reading order (work in flight first; a group head folds it, shared with the phone's fold).
// A column head sorts within each group (ascending, descending, then back to the board's
// order). The keyboard walks the rows: ↑ ↓ Home End move, Enter or Space opens the task, and
// each row's ⋮ holds the card's actions (cardActions.tsx).
import { useState, type KeyboardEvent } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { Markdown } from '../../../shared/markdown/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { TextButton } from '../../../shared/ui/index.ts';
import { openTaskModal } from '../../taskmodal/index.ts';
import { displayTitle } from '../../taskmodal/model.ts';
import { cardKey, type Card as CardData, type Column } from '../board.ts';
import { nextSort, rowCells, sortCards, TABLE_COLUMNS, type Sort } from '../desktop.ts';
import { rowGroups } from '../phone.ts';
import { useCardMenu } from './cardActions.tsx';
import { polledAt } from './poll.ts';

function TableRow({ card, now }: { card: CardData; now: number }) {
  const { menu, strip } = useCardMenu(card);
  const c = rowCells(card, now);
  function key(e: KeyboardEvent) {
    if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return;
    e.preventDefault();
    openTaskModal(card);
  }
  return (
    <tr className={`wtable-row wtable-row-${card.status}`} data-task-id={card.task_id || undefined} tabIndex={0} onClick={() => openTaskModal(card)} onKeyDown={key}>
      <td className="wtable-id">{c.id}</td>
      <td className="wtable-title">
        <div className="wtable-title-text" title={c.title}><Markdown inline source={displayTitle(c.title).slice(0, 200)} /></div>
        {strip}
      </td>
      <td className="wtable-state" title={c.stateTitle}>{c.state}</td>
      <td className="wtable-pr">{c.pr}</td>
      <td className={`wtable-ci${c.ciWarn ? ' warn' : ''}`}>{c.ci}</td>
      <td className={`wtable-risk${c.riskWarn ? ' warn' : ''}`}>{c.risk}</td>
      <td className="wtable-age">{c.age}</td>
      <td className="wtable-menu">{menu}</td>
    </tr>
  );
}

const ARIA_SORT = { asc: 'ascending', desc: 'descending' } as const;

/** ↑ ↓ Home End move between the rows; any other key is the row's own. */
function walk(e: KeyboardEvent<HTMLTableElement>) {
  const row = (e.target as Element).closest('.wtable-row');
  if (!row || row !== e.target) return;
  const rows = [...e.currentTarget.querySelectorAll<HTMLElement>('.wtable-row')];
  const i = rows.indexOf(row as HTMLElement);
  const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: rows.length - 1 }[e.key];
  if (to === undefined) return;
  e.preventDefault();
  rows[Math.max(0, Math.min(to, rows.length - 1))]?.focus();
}

export function RowsTable({ columns, collapsed, onToggle }: { columns: Column[]; collapsed: Set<string>; onToggle: (key: string) => void }) {
  const [sort, setSort] = useState<Sort>(null);
  // The clock the age column reads: the last poll's (the Work view runs no timer of its own,
  // tests/views.test.mjs), or the table's opening before one.
  const [opened] = useState(Date.now);
  const now = Math.max(useStore(polledAt), opened);
  const groups = rowGroups(columns);
  if (!groups.length) return <div className="kanban-empty-col">Nothing here</div>;
  return (
    <div className="wtable-wrap" id="work-rows">
      <table className="wtable" id="work-table" aria-label="Work" onKeyDown={walk}>
        <thead>
          <tr>
            {TABLE_COLUMNS.map((col) => {
              const on = sort?.key === col.key ? sort.dir : null;
              const Arrow = on === 'desc' ? icons.chevronDown : icons.chevronUp;
              return (
                <th key={col.key} scope="col" className={`wtable-th wtable-th-${col.key}`} aria-sort={on ? ARIA_SORT[on] : 'none'}>
                  <TextButton className="wtable-sort" data-sort={col.key} onClick={() => setSort(nextSort(sort, col.key))}>
                    {col.label}
                    {on && <Arrow size={12} aria-hidden />}
                  </TextButton>
                </th>
              );
            })}
            <th scope="col" className="wtable-th wtable-th-menu"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        {groups.map((g) => {
          const folded = collapsed.has(g.key);
          // Keyed by the board's order, so a re-sort moves a row (its focus, its confirm) rather than remounting it.
          const keys = new Map(g.cards.map((c, i) => [c, cardKey(c, i)]));
          return (
            <tbody key={g.key} className={`wtable-group${folded ? ' folded' : ''}`} data-col={g.key}>
              <tr className="wtable-group-head">
                <th colSpan={TABLE_COLUMNS.length + 1} scope="rowgroup">
                  <TextButton className="wtable-group-btn" aria-expanded={!folded} onClick={() => onToggle(g.key)}>
                    <icons.expand className="wrows-caret" aria-hidden size={14} />
                    <span className="wrows-label">{g.label}</span>
                    <span className="wrows-count">{g.cards.length}</span>
                  </TextButton>
                </th>
              </tr>
              {!folded && sortCards(g.cards, sort, now).map((c) => <TableRow key={keys.get(c)} card={c} now={now} />)}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
