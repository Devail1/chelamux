// The project switcher's list (CMX-315), shared by the phone's bottom sheet (PhoneBar.tsx) and
// the desktop's dropdown (DeskHead.tsx, CMX-316): a search field, "All projects" first, then
// one row per project with its counts (recently picked first). Single-select: a pick replaces
// the filter.
import type { MouseEvent } from 'react';
import type { WorkPayload } from '../../../shared/store/work.ts';
import { RadioGroup, TextInput } from '../../../shared/ui/index.ts';
import { pruneFilter } from '../board.ts';
import { countsLine, projectRows, totalCounts } from '../phone.ts';
import type { WorkPrefs } from './prefs.ts';

/** RadioGroup's value for "All projects" (a radio's value cannot be empty). */
const ALL = '*';

type Props = {
  data: WorkPayload | null;
  prefs: WorkPrefs;
  /** Draw the search field (the phone: past SEARCH_AFTER projects; the desktop: always). */
  search: boolean;
  query: string;
  setQuery: (q: string) => void;
  /** After a pick, and on a tap on the row already picked. */
  onClose: () => void;
  autoFocus?: boolean;
};

export function ProjectList({ data, prefs, search, query, setQuery, onClose, autoFocus }: Props) {
  const paths = (data?.workflows || []).map((wf) => wf.path);
  const filter = pruneFilter(prefs.filter, paths);
  const rows = projectRows(data, prefs.recent, search ? query : '');
  // A multi-select saved before CMX-316 is none of these single choices.
  const value = !filter.length ? ALL : filter.length === 1 ? (filter[0] ?? ALL) : '';
  const all = totalCounts(data, []);
  const options = [
    { value: ALL, className: 'wproj-row', label: <span className="wproj-name">All projects</span>, hint: <span className="wproj-counts">{countsLine(all)}</span> },
    ...rows.map((r) => ({
      value: r.path,
      className: 'wproj-row',
      label: <span className="wproj-name">{r.name}</span>,
      hint: (
        <>
          <span className="wproj-counts">{r.counts}</span>
          {r.needsYou > 0 && <span className="wproj-needs">{r.needsYou} needs you</span>}
        </>
      ),
    })),
  ];
  function pick(v: string) {
    prefs.pickProject(v === ALL ? '' : v);
    onClose();
  }
  // A tap on the row already picked fires no change: it closes the switcher all the same. (The
  // click bubbles here before the radio repaints, so only the old pick reads as checked.)
  function tapped(e: MouseEvent) {
    if ((e.target as Element).closest('.wproj-row')?.querySelector('[data-state="checked"]')) onClose();
  }
  return (
    <>
      {search && (
        <div className="wproj-search">
          <TextInput search plain value={query} onChange={setQuery} label="Search projects" placeholder="Search projects" autoFocus={autoFocus} />
        </div>
      )}
      <div className="wproj-list" onClick={tapped}>
        <RadioGroup label="Project" value={value} onChange={pick} options={options} />
        {search && rows.length === 0 && <p className="wproj-none">No project matches.</p>}
      </div>
    </>
  );
}
