// CMX-315 (Liav's approved mockups, 10-23): the phone Work view's ONE title bar, like the
// phone pane's (CMX-313): the drawer's ☰, the current project as the title with a chevron over
// "Work · N running · M queued", New task (+) and ⋯. The title opens the project switcher (a
// bottom sheet, single-select, replacing the desktop's project pills), ⋯ the Work options
// sheet (the layout, Init a repo, Pause dispatch, Pause all agents, which asks to confirm).
import { useState } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import type { WorkPayload } from '../../../shared/store/work.ts';
import { IconButton, ItemButton, Sheet, TextButton } from '../../../shared/ui/index.ts';
import { openNewTask } from '../../newtask/index.ts';
import { openInitRepo } from '../../runs/index.ts';
import { toggleSidebar } from '../../sidebar/controller.ts';
import { pruneFilter } from '../board.ts';
import { barSubtitle, projectTitle, SEARCH_AFTER } from '../phone.ts';
import { useDispatchHold, type Hold } from './hold.ts';
import { PauseAllConfirm } from './PauseAll.tsx';
import type { PhoneLayout, WorkPrefs } from './prefs.ts';
import { ProjectList } from './ProjectPicker.tsx';

function ProjectSheet({ open, onOpenChange, data, prefs }: { open: boolean; onOpenChange: (open: boolean) => void; data: WorkPayload | null; prefs: WorkPrefs }) {
  const [query, setQuery] = useState('');
  const search = (data?.workflows || []).length > SEARCH_AFTER;
  function close() {
    setQuery('');
    onOpenChange(false);
  }
  return (
    <Sheet open={open} onOpenChange={(o) => (o ? onOpenChange(true) : close())} title="Project" className="work-sheet work-project-sheet">
      <ProjectList data={data} prefs={prefs} search={search} query={query} setQuery={setQuery} onClose={close} />
    </Sheet>
  );
}

const LAYOUTS: { id: PhoneLayout; label: string }[] = [
  { id: 'rows', label: 'Rows' },
  { id: 'board', label: 'Board' },
];

function OptionsSheet({ open, onOpenChange, data, prefs, h, onPauseAll }: { open: boolean; onOpenChange: (open: boolean) => void; data: WorkPayload | null; prefs: WorkPrefs; h: Hold; onPauseAll: () => void }) {
  const hold = data?.dispatch_hold ?? null;
  const fleet = data?.fleet_pause ?? null;
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

export function WorkBar({ data, prefs, linear }: { data: WorkPayload | null; prefs: WorkPrefs; linear: boolean }) {
  const [sheet, setSheet] = useState<'project' | 'options' | 'pause-all' | null>(null);
  // ONE hold for the sheet and the confirm, so a request in flight disables both.
  const hold = useDispatchHold(data?.dispatch_hold ?? null, data?.fleet_pause ?? null);
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
      <OptionsSheet open={sheet === 'options'} onOpenChange={openChange('options')} data={data} prefs={prefs} h={hold} onPauseAll={() => setSheet('pause-all')} />
      <PauseAllConfirm open={sheet === 'pause-all'} onOpenChange={openChange('pause-all')} h={hold} />
    </>
  );
}
