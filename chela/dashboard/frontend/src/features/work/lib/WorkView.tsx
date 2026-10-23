// WORK: one view over one poll (static/js/work.js and index.html's #panel-work until CMX-233,
// CMX-212 P14). Three SEGMENTS of the same work: Board (the kanban), Runs (the per-workflow
// dispatch tables) and Schedules (the pokes), with New task, Init a repo and Pause/Resume
// dispatch (and Pause all agents, CMX-252). The segment is saved per browser, so a reload lands where you were.
// CMX-315: on a phone the controls are ONE title bar (PhoneBar.tsx): the drawer's ☰, the project
// as the title (its switcher), New task and ⋯ (the layout and the dispatch actions).
// CMX-316: wider, ONE header row (DeskHead.tsx): the project switcher, the segments, Pause
// dispatch, New task and ⋯.
import { useEffect, useState } from 'react';
import { useStore } from '../../../shared/store/index.ts';
import { view } from '../../../shared/store/view.ts';
import { work, type FleetPause as Fleet } from '../../../shared/store/work.ts';
import { TextButton, useMediaQuery } from '../../../shared/ui/index.ts';
import { Runs } from '../../runs/index.ts';
import { Schedules, refreshSchedules } from '../../schedules/index.ts';
import { boardColumns, pruneFilter } from '../board.ts';
import { Board } from './Board.tsx';
import { DeskHead } from './DeskHead.tsx';
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
  // The board's columns, for ⋯ › Column options (the board draws them from the same payload).
  const paths = data?.configured ? (data.workflows || []).map((wf) => wf.path) : [];
  const columns = !phone && paths.length ? boardColumns(data, pruneFilter(prefs.filter, paths)) : [];
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
        <DeskHead data={data} prefs={prefs} linear={linear} segments={segs} columns={columns} />
      )}
      {data?.fleet_pause && <FleetPaused fleet={data.fleet_pause} />}
      <Board live={data} active={seg === 'board'} prefs={prefs} />
      <div className={`work-pane${seg === 'runs' ? ' active' : ''}`} id="work-runs" data-seg="runs"><Runs /></div>
      <div className={`work-pane${seg === 'schedules' ? ' active' : ''}`} id="work-schedules" data-seg="schedules"><Schedules /></div>
    </>
  );
}
