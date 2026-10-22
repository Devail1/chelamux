// CMX-242 (CMX-212 P27): a wall tile's recap line, Claude's own "what happened while you were
// away" (the row's `recap`), one dim line under the header. A click PINS it open to the full
// text; the pin is kept per window across the 4 s repaint and a reload. Hidden while there is no
// recap: degrade quietly, never an empty bar. From terminals.js `_recapLineHTML`.
import { recapView } from '../../../../../static/js/wallmodel.ts';
import { createStore } from '../../../shared/store/index.ts';
import type { HeadAgent } from '../../panehead/index.ts';

const OPEN_KEY = 'pc_wall_recap_open';

function loadOpen(): ReadonlySet<string> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(OPEN_KEY) || '[]');
    return new Set(Array.isArray(v) ? v.filter((w): w is string => typeof w === 'string') : []);
  } catch {
    return new Set();
  }
}

/** The windows whose recap is pinned open. */
export const recapOpen = createStore<ReadonlySet<string>>(loadOpen());

export function toggleRecap(wid: string): void {
  const next = new Set(recapOpen.get());
  if (next.has(wid)) next.delete(wid);
  else next.add(wid);
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify([...next]));
  } catch {
    /* private mode: the pin still holds for this page */
  }
  recapOpen.set(next);
}

export function Recap({ wid, agent, open }: { wid: string; agent: HeadAgent | undefined; open: boolean }) {
  const r = recapView(agent);
  const pinned = !!r && open;
  // One line, clamped; the tooltip carries the FULL recap and its as-of time.
  const title = r ? r.text + (r.tsTitle ? `\n\n(as of ${r.tsTitle})` : '') : undefined;
  return (
    <div className={pinned ? 'pane-recap recap-open' : 'pane-recap'} data-recap-for={wid} hidden={!r} title={title}
      role="button" tabIndex={r ? 0 : -1} aria-expanded={pinned}
      onClick={() => toggleRecap(wid)}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        toggleRecap(wid);
      }}>
      {r ? r.text : ''}
    </div>
  );
}
