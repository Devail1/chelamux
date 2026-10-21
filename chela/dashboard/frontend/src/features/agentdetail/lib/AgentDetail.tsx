// The agent-detail drill-in (a virtual view, views.js): one window's metadata, its actions and
// its context bar. Reached from the palette or a sidebar row when there is no Wall to land on.
// Ported from nav.js renderAgentDetail and static/js/agents.js by CMX-219 (CMX-212 P21).
import { useEffect, useState } from 'react';
import { BASE_PATH, BOOT } from '../../../shared/api/index.ts';
import { ageStr, shortTime } from '../../../shared/format/index.ts';
import { refresh } from '../../../shared/scheduler/index.ts';
import { agents, type Agent } from '../../../shared/store/agents.ts';
import { context, loadContext, type ContextRow } from '../../../shared/store/context.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Button, Dialog, DialogClose, IconButton, TextArea } from '../../../shared/ui/index.ts';

/** The window name drilled into; null when the view is not showing one. */
export const detailAgent = createStore<string | null>(null);

/** What only legacy nav.js knows: the view switch, the role (the orchestrator slot) and the
 * attention dot. nav.js sets it at import. */
export type AgentDetailHost = { selectView(view: string): void; roleLabel(a: Agent): string; dotColor(a: Agent): string };
let host: AgentDetailHost = { selectView() {}, roleLabel: () => '', dotColor: () => 'grey' };
export function setAgentDetailHost(h: AgentDetailHost) {
  host = h;
}

// Bumped when a fact the host reports changed with no store of its own (the orchestrator slot).
const hostChanged = createStore(0);
/** Redraw the open detail: what the host reports changed (nav.js, on an orchestrator change). */
export function redrawAgentDetail() {
  hostChanged.set(hostChanged.get() + 1);
}

const msgTarget = createStore<string | null>(null);

/** Open the send-message dialog for `agent` (the detail's Message, with terminals off). */
export function openSendMsg(agent: string) {
  msgTarget.set(agent);
}

async function post(path: string, body: object) {
  await fetch(BASE_PATH + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

type Action = { label: string; busy?: string; path: string; confirm?: boolean; variant?: 'primary' | 'danger' };

function ActionButton({ name, a }: { name: string; a: Action }) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (a.confirm && !window.confirm(`${a.label} ${name}?`)) return;
    setBusy(true);
    await post(a.path, { agent: name });
    // the agent row changes on the next tick; a Trigger leaves the button as it was
    if (!a.busy) setBusy(false);
    void refresh();
  };
  return <Button variant={a.variant} disabled={busy} onClick={() => void run()}>{busy && a.busy ? a.busy : a.label}</Button>;
}

function ContextBar({ name, row }: { name: string; row: ContextRow | undefined }) {
  const pct = row?.used_pct;
  let label = 'Context: --';
  const tips: string[] = [];
  if (row && pct != null) {
    const parts = [`Context: ${row.used}/${row.total} (${pct}%${row.estimated ? '~' : ''})`];
    if (row.model) parts.push(row.model);
    if (row.cost_usd != null) parts.push(`$${row.cost_usd}`);
    if (row.estimated) parts.push('est');
    label = parts.join(' · ');
    if (row.session_name) tips.push(row.session_name);
    if (row.estimated) tips.push('estimate from transcript; run `chela install-statusline` for exact context %');
  } else if (row) {
    label = 'Context: unavailable';
  }
  const tone = pct == null ? '' : pct > 80 ? ' ctx-danger' : pct > 60 ? ' ctx-warn' : '';
  return (
    <div className="context-bar-wrap" id={`ctx-${name}`}>
      <div className="context-bar"><div className={`context-bar-fill${tone}`} style={{ width: `${pct ?? 0}%` }} /></div>
      <span className="context-label" title={tips.join(' · ') || undefined}>{label}</span>
    </div>
  );
}

/** How old the recap is, as of now (each redraw re-reads the clock). */
function recapAge(ts: string): string {
  return ageStr((Date.now() - new Date(ts).getTime()) / 1000);
}

function Back() {
  // agent-detail has no nav item of its own: back goes where the page lands on load
  return <IconButton className="detail-back" label="Back" onClick={() => host.selectView(BOOT.terminalsEnabled ? 'terminals' : 'work')}>← Back</IconButton>;
}

export function AgentDetail() {
  const name = useStore(detailAgent);
  const list = useStore(agents);
  const ctx = useStore(context);
  useStore(hostChanged);
  useEffect(() => {
    if (name) loadContext().catch(() => {}); // the context bar, fresh on drill-in
  }, [name]);
  if (!name) return null;
  const a = list.find((x) => x.name === name);
  if (!a) {
    return (
      <>
        <div className="detail-head"><Back /><span className="detail-title">{name}</span></div>
        <div className="side-empty">This agent's window is no longer present.</div>
      </>
    );
  }
  const running = !!a.claude_running;
  const actions: Action[] = [];
  if (a.has_schedules) actions.push({ label: 'Trigger', path: '/api/agents/trigger' });
  if (running) {
    actions.push({ label: 'Restart', busy: 'Restarting...', path: '/api/agents/restart', confirm: true });
    actions.push({ label: 'Stop', busy: 'Stopping...', path: '/api/agents/stop', confirm: true, variant: 'danger' });
  } else {
    actions.push({ label: 'Start', busy: 'Starting...', path: '/api/agents/start', variant: 'primary' });
  }
  const str = (k: string) => (typeof a[k] === 'string' ? (a[k] as string) : '');
  const rows: [string, string][] = [
    ['Window', a.window_id || '-'],
    ['Type', str('window_type') || (running ? 'claude' : 'shell')],
    ['Role', host.roleLabel(a)],
    ['Claude', running ? 'running' : 'stopped'],
    ['Status', str('session_status') || (running ? 'running' : 'offline')],
    ['Liveness', str('liveness') || '-'],
    ['CWD', str('cwd') || '-'],
  ];
  if (str('schedule_next_run')) rows.push(['Next run', shortTime(str('schedule_next_run'))]);
  if (str('schedule_last_run')) rows.push(['Last run', shortTime(str('schedule_last_run'))]);
  if (str('ai_title')) rows.push(['Title', str('ai_title')]);
  const pr = a.pr as { url?: string; number?: number; state?: string } | undefined;
  // CMX-41: say when the PR is history: a transcript's pr-link outlives the merge
  const prState = pr?.state === 'merged' || pr?.state === 'closed' ? ` · ${pr.state}` : '';
  const recapTs = str('recap_ts');
  return (
    <>
      <div className="detail-head">
        <Back />
        <span className={`health-dot ${host.dotColor(a)}`} />
        <span className="detail-title">{name}</span>
        {pr?.url && <a className="pr-badge" href={pr.url} target="_blank" rel="noopener noreferrer">{(pr.number ? `PR #${pr.number}` : 'PR') + prState}</a>}
      </div>
      <div className="canvas-toolbar">
        {/* Message only with no Wall to type into directly */}
        {!BOOT.terminalsEnabled && <Button onClick={() => openSendMsg(name)}>Message</Button>}
        {actions.map((x) => <ActionButton key={x.label} name={name} a={x} />)}
      </div>
      <div className="detail-grid">
        {rows.map(([k, v]) => [<div key={`k${k}`} className="k">{k}</div>, <div key={`v${k}`} className="v">{v}</div>])}
      </div>
      <ContextBar name={name} row={ctx.find((r) => r.name === name)} />
      {str('recap') && (
        <>
          <h4 className="detail-recap-head">Recap{recapTs ? ` · ${recapAge(recapTs)}` : ''}</h4>
          <div className="detail-recap">{str('recap')}</div>
        </>
      )}
    </>
  );
}

/** Send a message into an agent's window (/api/agents/msg). */
export function SendMessage() {
  const target = useStore(msgTarget);
  const [text, setText] = useState('');
  const send = async () => {
    const message = text.trim();
    if (!message || !target) return;
    await post('/api/agents/msg', { agent: target, message });
    msgTarget.set(null);
    setText('');
  };
  return (
    <Dialog open={!!target} onOpenChange={(o) => {
        if (o) return;
        msgTarget.set(null);
        setText('');
      }} title={`Send Message to ${target ?? ''}`}>
      <TextArea label="Message" value={text} onChange={setText} rows={4} />
      <div className="modal-actions">
        <DialogClose asChild><Button>Cancel</Button></DialogClose>
        <Button variant="primary" onClick={() => void send()}>Send</Button>
      </div>
    </Dialog>
  );
}
