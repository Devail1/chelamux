// WORK: one view over one poll (static/js/work.js and index.html's #panel-work until CMX-233,
// CMX-212 P14). Three SEGMENTS of the same work: Board (the kanban), Runs (the per-workflow
// dispatch tables) and Schedules (the pokes), with the toolbar's New task, Init a repo and
// Pause/Resume dispatch (and Pause all agents, CMX-252). The segment is saved per browser, so a reload lands where you were.
// CMX-315: on a phone the toolbar is ONE title bar (PhoneBar.tsx): the drawer's ☰, the project
// as the title (its switcher), New task and ⋯ (the layout and the dispatch actions).
import { useEffect, useState } from 'react';
import { useStore } from '../../../shared/store/index.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import { view } from '../../../shared/store/view.ts';
import { work, type DispatchHold as HoldState, type FleetPause as Fleet } from '../../../shared/store/work.ts';
import { Button, TextButton, useMediaQuery } from '../../../shared/ui/index.ts';
import { openNewTask } from '../../newtask/index.ts';
import { Runs, openInitRepo } from '../../runs/index.ts';
import { Schedules, refreshSchedules } from '../../schedules/index.ts';
import { Board } from './Board.tsx';
import { PAUSE_ALL_ASK, useDispatchHold } from './hold.ts';
import { WorkBar } from './PhoneBar.tsx';
import { useWorkPrefs } from './prefs.ts';

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
 * so it never flashes "Pause" over a queue that is actually held. CMX-252: "Pause all agents"
 * is `--pause --all`, and Resume resumes that fleet too, as `chela dispatch --resume` does. */
function DispatchHold({ hold, fleet }: { hold: HoldState; fleet: Fleet }) {
  const h = useDispatchHold(hold, fleet);
  function pauseAll() {
    if (window.confirm(PAUSE_ALL_ASK)) h.pauseAll();
  }
  return (
    <>
      <Button id="dispatch-hold-btn" variant={h.paused ? 'secondary' : 'primary'} className={h.paused ? 'btn-warn' : undefined} disabled={h.busy} onClick={() => h.toggle((q) => window.confirm(q))}>
        {h.label}
      </Button>
      {!fleet && (
        <Button id="fleet-pause-btn" variant="secondary" className="btn-warn" disabled={h.busy} onClick={pauseAll}>
          Pause all agents
        </Button>
      )}
      <span className="work-toolbar-hint" id="dispatch-hold-hint">{hold ? hold.summary || 'Dispatch is paused.' : ''}</span>
    </>
  );
}

/** CMX-252: what the fleet pause stopped — each agent, each frozen judge, since when, and
 * when its hold lapses (the daemon resumes them then). */
function FleetPaused({ fleet }: { fleet: NonNullable<Fleet> }) {
  const since = fleet.since_ago ? ` ${fleet.since_ago} ago` : '';
  const agents = fleet.agents || [];
  const judges = fleet.judges || [];
  return (
    <section className="fleet-pause" id="fleet-pause" aria-label="Paused agents">
      <p className="fleet-pause-head">
        Agents paused{since} by {fleet.by || '?'}.{' '}
        {fleet.expires_in ? `The hold expires in ${fleet.expires_in}; the agents resume then.` : 'The hold is gone; the next dispatch tick resumes the agents.'}
      </p>
      {agents.length + judges.length === 0 ? (
        <p className="fleet-pause-empty">No agent was running.</p>
      ) : (
        <ul className="fleet-pause-list">
          {agents.map((a) => (
            <li key={`a-${a.task}`} data-task={a.task}>
              agent {a.task}{a.wid ? ` in ${a.wid}` : ''}: {a.state || '?'}{a.finishing ? ` (${a.finishing})` : ''}
            </li>
          ))}
          {judges.map((j) => (
            <li key={`j-${j.task}`} data-task={j.task}>
              judge {j.task}: frozen{j.frozen_for ? ` ${j.frozen_for}` : ''} ({j.processes ?? 0} process(es))
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The island in #panel-work. */
export function WorkView() {
  const data = useStore(work);
  const current = useStore(view);
  const [seg, setSeg] = useState(loadSegment);
  const prefs = useWorkPrefs();
  const phone = useMediaQuery('(max-width: 768px)');
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
  const segs = (
    <div className="work-seg" id="work-seg" role="group" aria-label="Work view">
      {SEGMENTS.map((s) => (
        <TextButton key={s.id} className={`work-seg-btn${seg === s.id ? ' active' : ''}`} data-seg={s.id} aria-pressed={seg === s.id} onClick={() => pick(s.id)}>
          {s.label}
        </TextButton>
      ))}
    </div>
  );
  return (
    <>
      {phone ? (
        <>
          <WorkBar data={data} prefs={prefs} linear={linear} />
          <div className="work-phone-seg">{segs}</div>
        </>
      ) : (
        <div className="work-toolbar">
          {segs}
          {linear && <Button variant="primary" id="new-task-btn" onClick={openNewTask}>+ New task</Button>}
          <Button variant="primary" title="Seed a starter WORKFLOW.md + TODO.md into a repo" onClick={() => openInitRepo(launcher.get())}>+ Init a repo</Button>
          {data && <DispatchHold hold={data.dispatch_hold ?? null} fleet={data.fleet_pause ?? null} />}
        </div>
      )}
      {data?.fleet_pause && <FleetPaused fleet={data.fleet_pause} />}
      <Board live={data} active={seg === 'board'} prefs={prefs} />
      <div className={`work-pane${seg === 'runs' ? ' active' : ''}`} id="work-runs" data-seg="runs"><Runs /></div>
      <div className={`work-pane${seg === 'schedules' ? ' active' : ''}`} id="work-schedules" data-seg="schedules"><Schedules /></div>
    </>
  );
}
