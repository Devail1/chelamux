// The Schedules segment of WORK: chela's schedules (enable, disable, delete, add) and the
// read-only system cron. Ported from static/js/schedules.js by CMX-231 (FRONTEND_PORT_PLAN.md
// P11). /api/schedules is its own dataset, fetched only when work.js asks (refreshSchedules():
// the segment is showing, or a poll landed while it is).
import { useEffect, useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { humanSchedule, relativeTime, shortTime } from '../../../shared/format/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Badge, Button, Dialog, DialogClose, FieldRow, Select, TextArea, TextInput } from '../../../shared/ui/index.ts';
import { SCHEDULE_TYPES, clip, scheduleHint, type CronJob, type Schedule } from '../model.ts';

/** Bumped per refresh; 0 = never asked, so nothing is fetched before the segment shows. */
const tick = createStore(0);
const adding = createStore(false);

export function refreshSchedules() {
  tick.set(tick.get() + 1);
}

export function showAddSchedule() {
  adding.set(true);
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

function ScheduleRow({ t }: { t: Schedule }) {
  async function toggle() {
    await api(`/api/schedules/${t.id}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ enabled: !t.enabled }) });
    refreshSchedules();
  }
  async function remove() {
    if (!window.confirm(`Delete task ${t.id}?`)) return;
    await api(`/api/schedules/${t.id}`, { method: 'DELETE' });
    refreshSchedules();
  }
  return (
    <tr>
      <td>{t.id}</td>
      <td>{t.agent_name}</td>
      <td><b>{humanSchedule(t.schedule_type, t.schedule_value)}</b></td>
      <td title={t.prompt}>{clip(t.prompt, 60, '...')}</td>
      <td className="ts">{shortTime(t.last_run)}</td>
      <td className="ts">{t.next_run ? relativeTime(t.next_run) : '-'}</td>
      <td><Badge tone={t.enabled ? 'ok' : 'bad'}>{t.enabled ? 'ON' : 'OFF'}</Badge></td>
      <td>
        <Button size="sm" onClick={() => void toggle()}>{t.enabled ? 'Disable' : 'Enable'}</Button>{' '}
        <Button size="sm" variant="danger" onClick={() => void remove()}>Del</Button>
      </td>
    </tr>
  );
}

function ScheduleTable({ rows }: { rows: Schedule[] | null }) {
  return (
    <div className="card">
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>ID</th><th>Agent</th><th>Schedule</th><th>Prompt</th><th>Last Run</th><th>Next Run</th><th>Status</th><th>Actions</th></tr>
          </thead>
          <tbody>{rows?.map((t) => <ScheduleRow key={t.id} t={t} />)}</tbody>
        </table>
      </div>
      {rows?.length === 0 && (
        <div className="work-empty">
          <div className="work-empty-icon"><icons.schedule aria-hidden size={30} /></div>
          <div className="work-empty-title">Put an agent on a schedule</div>
          <div className="work-empty-body">
            A schedule pokes an agent on a recurring cadence: every <code>30m</code>, a <code>cron</code> expression, or <code>once</code> at a set
            time. When it fires, chela types your prompt into that agent&apos;s tmux window. Use it to let a researcher refresh itself hourly, or run a
            risk check every 20&nbsp;minutes, with no human in the loop.
          </div>
          <Button variant="primary" onClick={showAddSchedule}>Schedule an agent</Button>
        </div>
      )}
    </div>
  );
}

// Read-only, parsed from `crontab -l` by /api/cron: the dashboard never edits the crontab.
function CronTable({ cron }: { cron: { jobs?: CronJob[]; error?: string } | null }) {
  const jobs = cron?.jobs || [];
  return (
    <div className="card">
      <div className="table-wrap">
        <table>
          <thead><tr><th>Schedule</th><th>Project</th><th>Command</th><th>Next Run</th></tr></thead>
          <tbody>
            {jobs.map((j, i) => (
              <tr key={i}>
                <td><b>{j.schedule}</b>{j.tz && j.tz !== 'local' && <> <Badge>{j.tz}</Badge></>}</td>
                <td>{j.project || <span className="sched-none">-</span>}</td>
                <td title={j.command}><code>{clip(j.command, 70)}</code></td>
                <td className="ts">{j.next_run ? relativeTime(j.next_run) : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cron && !jobs.length && <div className="cron-empty">{cron.error || 'No cron jobs'}</div>}
    </div>
  );
}

function AddForm() {
  const names = useStore(agents).map((a) => String(a.name ?? '')).filter(Boolean).sort((a, b) => a.localeCompare(b));
  const [agent, setAgent] = useState('');
  const [type, setType] = useState('interval');
  const [value, setValue] = useState('');
  const [prompt, setPrompt] = useState('');
  const hint = scheduleHint(type);
  const agentName = names.includes(agent) ? agent : (names[0] ?? '');
  async function add() {
    const body = { agent_name: agentName, schedule_type: type, schedule_value: value.trim(), prompt: prompt.trim() };
    if (!body.agent_name || !body.schedule_value || !body.prompt) return;
    await api('/api/schedules', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) });
    adding.set(false);
    refreshSchedules();
  }
  return (
    <>
      <FieldRow label="Agent"><Select value={agentName} onChange={setAgent} options={names.map((n) => ({ value: n, label: n }))} /></FieldRow>
      <FieldRow label="Schedule type"><Select value={type} onChange={setType} options={SCHEDULE_TYPES} /></FieldRow>
      <FieldRow label={hint.label}><TextInput value={value} onChange={setValue} placeholder={hint.placeholder} /></FieldRow>
      <FieldRow label="Prompt"><TextArea value={prompt} onChange={setPrompt} /></FieldRow>
      <div className="modal-actions">
        <DialogClose asChild><Button>Cancel</Button></DialogClose>
        <Button variant="primary" onClick={() => void add()}>Add</Button>
      </div>
    </>
  );
}

/** The island in #work-schedules. */
export function Schedules() {
  const n = useStore(tick);
  const isAdding = useStore(adding);
  const [rows, setRows] = useState<Schedule[] | null>(null);
  const [cron, setCron] = useState<{ jobs?: CronJob[]; error?: string } | null>(null);
  useEffect(() => {
    if (!n) return;
    let live = true;
    // Independent reads: a failed cron read keeps the schedules, and the other way round.
    api('/api/cron').then((c) => live && setCron((c ?? {}) as typeof cron), () => {});
    api('/api/schedules').then((s) => live && setRows(Array.isArray(s) ? (s as Schedule[]) : []), () => {});
    return () => {
      live = false;
    };
  }, [n]);
  return (
    <>
      <div className="canvas-toolbar">
        <Button variant="primary" onClick={showAddSchedule}>+ Add Task</Button>
      </div>
      <ScheduleTable rows={rows} />
      <div className="sched-cron-head">System cron <span>(read-only, from <code>crontab -l</code>)</span></div>
      <CronTable cron={cron} />
      <Dialog open={isAdding} onOpenChange={(o) => adding.set(o)} title="Add Scheduled Task">
        {isAdding && <AddForm />}
      </Dialog>
    </>
  );
}
