// CMX-316 (Liav's approved mockup, 10-23): the desktop Work view's ONE header row, replacing
// the toolbar of four coloured buttons and the row of project pills under it. The project
// switcher (the current project and a chevron over "N running · M queued · dispatch on"), the
// Board / Runs / Schedules control, then Pause dispatch (a quiet outline button that reads the
// state), New task (the one primary button) and ⋯: Init a repo, Pause all agents (which asks to
// confirm), the Board | Rows layout and the board's column options.
import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import type { WorkPayload } from '../../../shared/store/work.ts';
import { Button, IconButton, Menu, Popover, TextButton, type MenuItem } from '../../../shared/ui/index.ts';
import { openNewTask } from '../../newtask/index.ts';
import { openInitRepo } from '../../runs/index.ts';
import { pruneFilter, type Column } from '../board.ts';
import { headSubtitle } from '../desktop.ts';
import { projectTitle } from '../phone.ts';
import { useDispatchHold } from './hold.ts';
import { PauseAllConfirm } from './PauseAll.tsx';
import type { WorkPrefs } from './prefs.ts';
import { ProjectList } from './ProjectPicker.tsx';

function Switcher({ data, prefs }: { data: WorkPayload | null; prefs: WorkPrefs }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const paths = (data?.workflows || []).map((wf) => wf.path);
  const filter = pruneFilter(prefs.filter, paths);
  const name = projectTitle(filter);
  function openChange(o: boolean) {
    setOpen(o);
    if (!o) setQuery('');
  }
  return (
    <Popover open={open} onOpenChange={openChange} align="start" className="work-switcher" label="Switch project"
      trigger={
        <TextButton className="work-head-title" aria-label={`Switch project, current ${name}`}>
          <span className="work-head-name">
            <span className="work-head-project">{name}</span>
            <icons.chevronDown size={16} aria-hidden />
          </span>
          <span className="work-head-sub">{headSubtitle(data, filter)}</span>
        </TextButton>
      }>
      <ProjectList data={data} prefs={prefs} search query={query} setQuery={setQuery} onClose={() => openChange(false)} autoFocus />
    </Popover>
  );
}

/** The safety pills (.safety-float, the shell's) float in the page's top-right corner, over
 * this row: keep the row's end clear of them, as wide as they are now (0 with none lit). */
function useFloatClearance(head: RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    const float = document.querySelector<HTMLElement>('.safety-float');
    const el = head.current;
    if (!float || !el) return;
    const fit = () => {
      const w = float.getBoundingClientRect().width;
      el.style.setProperty('--work-head-float', w > 0 ? `${Math.ceil(w) + 16}px` : '0px');
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(float);
    return () => ro.disconnect();
  }, [head]);
}

type Props = { data: WorkPayload | null; prefs: WorkPrefs; linear: boolean; segments: ReactNode; columns: Column[] };

export function DeskHead({ data, prefs, linear, segments, columns }: Props) {
  const hold = data?.dispatch_hold ?? null;
  const fleet = data?.fleet_pause ?? null;
  const h = useDispatchHold(hold, fleet);
  const [menuOpen, setMenuOpen] = useState(false);
  const [page, setPage] = useState<'main' | 'columns'>('main');
  const [confirm, setConfirm] = useState(false);
  const headEl = useRef<HTMLElement>(null);
  useFloatClearance(headEl);

  function menuChange(o: boolean) {
    setMenuOpen(o);
    if (!o) setPage('main');
  }
  // The confirm waits for the menu to close: a dialog opened while the menu still holds its
  // focus trap and pointer lock can leave the page unclickable.
  const later = (fn: () => void) => () => setTimeout(fn, 0);

  const main: MenuItem[] = [{ label: 'Init a repo', icon: icons.initRepo, onSelect: later(() => openInitRepo(launcher.get())) }];
  // Drawn only once a poll has landed, so it never offers a pause over a paused fleet.
  if (data && !fleet) {
    main.push({ label: 'Pause all agents', icon: icons.warn, className: 'wmenu-warn', title: 'Asks to confirm first', data: { 'data-action': 'pause-all' }, onSelect: later(() => setConfirm(true)) });
  }
  main.push(
    'separator',
    { label: 'Board', check: 'radio', checked: prefs.deskLayout === 'board', keepOpen: true, data: { 'data-layout': 'board' }, onSelect: () => prefs.pickDeskLayout('board') },
    { label: 'Rows', check: 'radio', checked: prefs.deskLayout === 'rows', keepOpen: true, data: { 'data-layout': 'rows' }, onSelect: () => prefs.pickDeskLayout('rows') },
  );
  if (prefs.deskLayout === 'board' && columns.length) {
    const shown = columns.filter((c) => !prefs.hiddenCols.includes(c.key)).length;
    main.push('separator', { label: 'Column options', icon: icons.view, value: `${shown} of ${columns.length}`, submenu: true, keepOpen: true, data: { 'data-action': 'columns' }, onSelect: () => setPage('columns') });
  }
  const columnsPage: MenuItem[] = [
    { label: 'Columns', back: true, keepOpen: true, onSelect: () => setPage('main') },
    'separator',
    ...columns.map((c): MenuItem => ({
      label: c.label,
      check: 'checkbox',
      checked: !prefs.hiddenCols.includes(c.key),
      keepOpen: true,
      data: { 'data-col': c.key },
      onSelect: () => prefs.toggleColumn(c.key),
    })),
  ];

  return (
    <>
      <header className="work-head" ref={headEl}>
        <Switcher data={data} prefs={prefs} />
        {segments}
        <span className="work-head-spacer" />
        {data && (
          <Button id="dispatch-hold-btn" className={`work-head-hold${h.paused ? ' btn-warn' : ''}`} disabled={h.busy}
            title={hold ? hold.summary || 'Dispatch is paused.' : fleet ? 'Agents are paused.' : 'No new task is claimed while paused.'}
            onClick={() => h.toggle((q) => window.confirm(q))}>
            {h.paused ? <icons.launch size={14} aria-hidden /> : <icons.pause size={14} aria-hidden />}
            {h.label}
          </Button>
        )}
        <Button variant="primary" id="new-task-btn" className="work-head-new" disabled={!linear} title={linear ? undefined : 'New task needs a workflow with a Linear tracker'} onClick={openNewTask}>
          <icons.add size={14} aria-hidden />
          New task
        </Button>
        <Menu open={menuOpen} onOpenChange={menuChange} className="work-head-menu" items={page === 'main' ? main : columnsPage}
          trigger={
            <IconButton id="work-more-btn" className="work-head-more" label="Work options">
              <icons.more size={16} aria-hidden />
            </IconButton>
          } />
      </header>
      <PauseAllConfirm open={confirm} onOpenChange={setConfirm} h={h} />
    </>
  );
}
