// WORK: one view over one poll (static/js/work.js and index.html's #panel-work until CMX-233,
// CMX-212 P14). Three SEGMENTS of the same work: Board (the kanban), Runs (the per-workflow
// dispatch tables) and Schedules (the pokes), with the toolbar's New task, Init a repo and
// Pause/Resume dispatch. The segment is saved per browser, so a reload lands where you were.
import { useEffect, useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import { view } from '../../../shared/store/view.ts';
import { refreshWork, work, type DispatchHold as Hold } from '../../../shared/store/work.ts';
import { Button, TextButton } from '../../../shared/ui/index.ts';
import { openNewTask } from '../../newtask/index.ts';
import { Runs, openInitRepo } from '../../runs/index.ts';
import { Schedules, refreshSchedules } from '../../schedules/index.ts';
import { Board } from './Board.tsx';

export const SEGMENTS = [
  { id: 'board', label: 'Board' },
  { id: 'runs', label: 'Runs' },
  { id: 'schedules', label: 'Schedules' },
] as const;
type Segment = (typeof SEGMENTS)[number]['id'];
export const SEGMENT_KEY = 'chela_work_segment';

function loadSegment(): Segment {
  try {
    const v = localStorage.getItem(SEGMENT_KEY);
    return SEGMENTS.find((s) => s.id === v)?.id ?? 'board';
  } catch {
    return 'board';
  }
}

/** CMX-206: `chela dispatch --pause` from the dashboard. Drawn only once a poll has landed,
 * so it never flashes "Pause" over a queue that is actually held. */
function DispatchHold({ hold }: { hold: Hold }) {
  const [busy, setBusy] = useState(false);
  async function toggle() {
    // Pause takes the hold (the CLI's default TTL); Resume releases it unconditionally. It acts
    // on the payload the button was drawn from, so what it shows is what it does.
    if (!hold && !window.confirm('Pause dispatch? No new task will be claimed until you resume.')) return;
    setBusy(true);
    try {
      const resp = (await api(hold ? '/api/dispatcher/resume' : '/api/dispatcher/pause', { method: 'POST' })) as { ok?: boolean; error?: string } | null;
      if (!resp || resp.ok === false) window.alert(resp?.error || 'That failed. Dispatch state is unchanged.');
    } catch {
      window.alert('Request failed. Dispatch state is unchanged.');
    } finally {
      setBusy(false);
    }
    await refreshWork();
  }
  return (
    <>
      <Button id="dispatch-hold-btn" variant={hold ? 'secondary' : 'primary'} className={hold ? 'btn-warn' : undefined} disabled={busy} onClick={() => void toggle()}>
        {hold ? 'Resume dispatch' : 'Pause dispatch'}
      </Button>
      <span className="work-toolbar-hint" id="dispatch-hold-hint">{hold ? hold.summary || 'Dispatch is paused.' : ''}</span>
    </>
  );
}

/** The island in #panel-work. */
export function WorkView() {
  const data = useStore(work);
  const current = useStore(view);
  const [seg, setSeg] = useState(loadSegment);
  // Schedules has its own endpoint: read when its segment shows, and again on every poll then.
  useEffect(() => {
    if (current === 'work' && seg === 'schedules') refreshSchedules();
  }, [data, current, seg]);
  function pick(s: Segment) {
    setSeg(s);
    try {
      localStorage.setItem(SEGMENT_KEY, s);
    } catch {
      /* the choice holds for this page */
    }
  }
  // CMX-6: chela creates issues only in a Linear tracker.
  const linear = (data?.workflows || []).some((w) => w?.tracker_kind === 'linear');
  return (
    <>
      <div className="work-toolbar">
        <div className="work-seg" id="work-seg" role="group" aria-label="Work view">
          {SEGMENTS.map((s) => (
            <TextButton key={s.id} className={`work-seg-btn${seg === s.id ? ' active' : ''}`} data-seg={s.id} aria-pressed={seg === s.id} onClick={() => pick(s.id)}>
              {s.label}
            </TextButton>
          ))}
        </div>
        {linear && <Button variant="primary" id="new-task-btn" onClick={openNewTask}>+ New task</Button>}
        <Button variant="primary" title="Seed a starter WORKFLOW.md + TODO.md into a repo" onClick={() => openInitRepo(launcher.get())}>+ Init a repo</Button>
        {data && <DispatchHold hold={data.dispatch_hold ?? null} />}
      </div>
      <Board live={data} active={seg === 'board'} />
      <div className={`work-pane${seg === 'runs' ? ' active' : ''}`} id="work-runs" data-seg="runs"><Runs /></div>
      <div className={`work-pane${seg === 'schedules' ? ' active' : ''}`} id="work-schedules" data-seg="schedules"><Schedules /></div>
    </>
  );
}
