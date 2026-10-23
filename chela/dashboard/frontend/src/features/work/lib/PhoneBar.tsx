// CMX-315 (Liav's approved mockups, 10-23): the phone Work view's ONE title bar, like the
// phone pane's (CMX-313): the drawer's ☰, the current project as the title with a chevron over
// "Work · N running · M queued", New task (+) and ⋯. The title opens the project switcher (a
// bottom sheet, single-select, replacing the desktop's project pills), ⋯ the Work options
// sheet (the layout, Init a repo, Pause dispatch, Pause all agents, which asks to confirm).
import { useState } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import type { WorkPayload } from '../../../shared/store/work.ts';
import { Button, Dialog, DialogClose, IconButton, ItemButton, RadioGroup, Sheet, TextButton, TextInput } from '../../../shared/ui/index.ts';
import { openNewTask } from '../../newtask/index.ts';
import { openInitRepo } from '../../runs/index.ts';
import { toggleSidebar } from '../../sidebar/controller.ts';
import { pruneFilter } from '../board.ts';
import { barSubtitle, countsLine, projectRows, projectTitle, SEARCH_AFTER, totalCounts } from '../phone.ts';
import { PAUSE_ALL_ASK, useDispatchHold } from './hold.ts';
import type { PhoneLayout, WorkPrefs } from './prefs.ts';

/** RadioGroup's value for "All projects" (a radio's value cannot be empty). */
const ALL = '*';

function ProjectSheet({ open, onOpenChange, data, prefs }: { open: boolean; onOpenChange: (open: boolean) => void; data: WorkPayload | null; prefs: WorkPrefs }) {
  const [query, setQuery] = useState('');
  const paths = (data?.workflows || []).map((wf) => wf.path);
  const filter = pruneFilter(prefs.filter, paths);
  const search = paths.length > SEARCH_AFTER;
  const rows = projectRows(data, prefs.recent, search ? query : '');
  // A desktop multi-select of several projects is none of these single choices.
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
    setQuery('');
    onOpenChange(false);
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Project" className="work-sheet work-project-sheet">
      {search && (
        <div className="wproj-search">
          <TextInput search plain value={query} onChange={setQuery} label="Search projects" placeholder="Search projects" />
        </div>
      )}
      <div className="wproj-list">
        <RadioGroup label="Project" value={value} onChange={pick} options={options} />
        {search && rows.length === 0 && <p className="wproj-none">No project matches.</p>}
      </div>
    </Sheet>
  );
}

const LAYOUTS: { id: PhoneLayout; label: string }[] = [
  { id: 'rows', label: 'Rows' },
  { id: 'board', label: 'Board' },
];

function OptionsSheet({ open, onOpenChange, data, prefs, onPauseAll }: { open: boolean; onOpenChange: (open: boolean) => void; data: WorkPayload | null; prefs: WorkPrefs; onPauseAll: () => void }) {
  const hold = data?.dispatch_hold ?? null;
  const fleet = data?.fleet_pause ?? null;
  const h = useDispatchHold(hold, fleet);
  function then(fn: () => void) {
    onOpenChange(false);
    fn();
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Work options" className="work-sheet work-options-sheet">
      <h4 className="wopt-head">Layout</h4>
      <div className="wopt-layout" role="group" aria-label="Layout">
        {LAYOUTS.map((l) => (
          <TextButton key={l.id} className={`wopt-layout-btn${prefs.layout === l.id ? ' active' : ''}`} data-layout={l.id} aria-pressed={prefs.layout === l.id}
            onClick={() => prefs.pickLayout(l.id)}>
            {l.label}
          </TextButton>
        ))}
      </div>
      <div className="wopt-sep" />
      <ItemButton className="wopt-item" onClick={() => then(() => openInitRepo(launcher.get()))}>
        <icons.initRepo aria-hidden size={18} /> <span className="wopt-label">Init a repo</span>
      </ItemButton>
      {/* Drawn only once a poll has landed, so it never offers Pause over a held queue. */}
      {data && (
        <ItemButton className="wopt-item" id="wopt-hold" disabled={h.busy} onClick={() => then(() => h.toggle(() => true))}>
          {h.paused ? <icons.launch aria-hidden size={18} /> : <icons.pause aria-hidden size={18} />} <span className="wopt-label">{h.label}</span>
          <span className="wopt-hint">{hold ? hold.summary || 'Dispatch is paused.' : fleet ? 'agents are paused' : 'no new tasks'}</span>
        </ItemButton>
      )}
      {data && !fleet && (
        <ItemButton className="wopt-item wopt-warn" id="wopt-pause-all" disabled={h.busy} onClick={() => then(onPauseAll)}>
          <icons.warn aria-hidden size={18} /> <span className="wopt-label">Pause all agents</span>
          <span className="wopt-hint">asks to confirm</span>
        </ItemButton>
      )}
    </Sheet>
  );
}

function PauseAllConfirm({ open, onOpenChange, data }: { open: boolean; onOpenChange: (open: boolean) => void; data: WorkPayload | null }) {
  const h = useDispatchHold(data?.dispatch_hold ?? null, data?.fleet_pause ?? null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Pause all agents?" description={PAUSE_ALL_ASK} className="work-confirm">
      <div className="work-confirm-actions">
        <DialogClose asChild>
          <Button>Cancel</Button>
        </DialogClose>
        <Button variant="danger" id="wopt-pause-all-confirm" disabled={h.busy}
          onClick={() => {
            onOpenChange(false);
            h.pauseAll();
          }}>
          Pause all agents
        </Button>
      </div>
    </Dialog>
  );
}

export function WorkBar({ data, prefs, linear }: { data: WorkPayload | null; prefs: WorkPrefs; linear: boolean }) {
  const [sheet, setSheet] = useState<'project' | 'options' | 'pause-all' | null>(null);
  const openChange = (which: typeof sheet) => (open: boolean) => setSheet(open ? which : null);
  const paths = (data?.workflows || []).map((wf) => wf.path);
  const filter = pruneFilter(prefs.filter, paths);
  const name = projectTitle(filter);
  return (
    <>
      <header className="work-bar">
        {/* the drawer's opener, where the floating ☰ used to be (the controller keeps aria-expanded) */}
        <IconButton className="work-bar-btn work-bar-menu" label="Open menu" aria-expanded={document.body.classList.contains('sidebar-open')} onClick={() => toggleSidebar()}>
          <icons.menu size={20} aria-hidden />
        </IconButton>
        <TextButton className="work-bar-title" aria-haspopup="dialog" aria-label={`Switch project, current ${name}`} onClick={() => setSheet('project')}>
          <span className="work-bar-name">
            <span className="work-bar-project">{name}</span>
            <icons.chevronDown size={16} aria-hidden />
          </span>
          <span className="work-bar-sub">{barSubtitle(data, filter)}</span>
        </TextButton>
        <IconButton className="work-bar-btn work-bar-new" label={linear ? 'New task' : 'New task (needs a Linear tracker)'} disabled={!linear} onClick={openNewTask}>
          <icons.add size={20} aria-hidden />
        </IconButton>
        <IconButton className="work-bar-btn work-bar-more" label="Work options" aria-haspopup="dialog" onClick={() => setSheet('options')}>
          <icons.more size={20} aria-hidden />
        </IconButton>
      </header>
      <ProjectSheet open={sheet === 'project'} onOpenChange={openChange('project')} data={data} prefs={prefs} />
      <OptionsSheet open={sheet === 'options'} onOpenChange={openChange('options')} data={data} prefs={prefs} onPauseAll={() => setSheet('pause-all')} />
      <PauseAllConfirm open={sheet === 'pause-all'} onOpenChange={openChange('pause-all')} data={data} />
    </>
  );
}
