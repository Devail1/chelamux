// CMX-315: the compact Work card and the Rows row (Liav's approved phone mockups, 10-23). The
// compact card is the shared component the desktop follow-up reuses: the title (two lines at
// most), one meta line (the id in mono, the PR, the state with its icon), one flags line
// (rework, risk, CI) and at most two lines of note. No branch line, no ✕ and no requeue on the
// face: those live in the card's ⋮ (cardActions.tsx). Orange marks only a real warning, and
// always on a word. A tap on the card or row opens the task modal with the board's own object.
import type { KeyboardEvent } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { Markdown } from '../../../shared/markdown/index.ts';
import { openTaskModal } from '../../taskmodal/index.ts';
import { displayTitle } from '../../taskmodal/model.ts';
import type { Card as CardData } from '../board.ts';
import { cardFlags, cardIds, cardNote, cardState, rowFlag } from '../phone.ts';
import { useCardMenu } from './cardActions.tsx';

const title = (card: CardData) => <Markdown inline source={displayTitle(card.title || '').slice(0, 200)} />;

// Enter or Space on the card itself (never on its ⋮) opens it, like a click.
const onOpen = (card: CardData) => (e: KeyboardEvent) => {
  if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return;
  e.preventDefault();
  openTaskModal(card);
};

export function CompactCard({ card }: { card: CardData }) {
  const { menu, strip } = useCardMenu(card);
  const { id, pr } = cardIds(card);
  const state = cardState(card);
  const State = icons[state.icon];
  const flags = cardFlags(card);
  const note = cardNote(card);
  return (
    <div className={`wcard wcard-${card.status}`} data-task-id={card.task_id || undefined} role="button" tabIndex={0}
      onClick={() => openTaskModal(card)} onKeyDown={onOpen(card)}>
      <div className="wcard-head">
        <div className="wcard-title">{title(card)}</div>
        {menu}
      </div>
      <div className="wcard-meta">
        {id && <span className="wcard-id">{id}</span>}
        {pr && <span className="wcard-pr">{pr}</span>}
        <span className="wcard-state"><State aria-hidden size={12} /> {state.label}</span>
      </div>
      {flags.length > 0 && (
        <div className="wcard-flags">
          {flags.map((f) => {
            const Icon = f.icon ? icons[f.icon] : null;
            return (
              <span key={f.label} className={`wcard-flag${f.warn ? ' warn' : ''}`} title={f.title}>
                {Icon && <Icon aria-hidden size={12} />} {f.label}
              </span>
            );
          })}
        </div>
      )}
      {note && <div className="wcard-note" title={note.text}>{note.warn && <icons.warn aria-hidden size={12} />} {note.text}</div>}
      {strip}
    </div>
  );
}

export function RowItem({ card }: { card: CardData }) {
  const { menu, strip } = useCardMenu(card);
  const { id, pr } = cardIds(card);
  const flag = rowFlag(card);
  return (
    <div className={`wrow wrow-${card.status}`} data-task-id={card.task_id || undefined} role="button" tabIndex={0}
      onClick={() => openTaskModal(card)} onKeyDown={onOpen(card)}>
      <div className="wrow-main">
        <div className="wrow-title">{title(card)}</div>
        <div className="wrow-meta">
          {id && <span className="wrow-id">{id}</span>}
          {pr && <span className="wrow-pr">{pr}</span>}
          {flag.label && <span className={`wrow-flag${flag.warn ? ' warn' : ''}`}>{flag.label}</span>}
        </div>
        {strip}
      </div>
      {menu}
    </div>
  );
}
