// CMX-233 (CMX-212 P12-P14): the Work view (board, runs, schedules), its one /api/dispatcher
// poll (a scheduler lane, registered on import) and its toasts. The pure models (board.ts,
// lanes.ts, linear.ts, hidden.ts, and CMX-315's phone.ts) are entry points too.
import './lib/poll.ts';

export { Board, moveCard } from './lib/Board.tsx';
// CMX-315: the compact card (and the Rows row), the phone's; the desktop follow-up reuses it.
export { CompactCard, RowItem } from './lib/Compact.tsx';
export { pollWork } from './lib/poll.ts';
export { showToast, toasts } from './lib/toasts.ts';
export { SEGMENTS, SEGMENT_KEY, WorkView } from './lib/WorkView.tsx';
export { WorkToasts } from './lib/WorkToasts.tsx';
