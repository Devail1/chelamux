// The Runs segment of WORK: one card per workflow, its open tasks and its run tables (ported
// from static/js/dispatcher.js by CMX-231, FRONTEND_PORT_PLAN.md P10). It reads the payload
// work.js's one /api/dispatcher poll published (shared/store/work); it never fetches it.
import { useState, type ReactNode } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { shortTime } from '../../../shared/format/index.ts';
import { work, type OpenTask, type Run, type Workflow } from '../../../shared/store/work.ts';
import { Button, IconButton } from '../../../shared/ui/index.ts';
import { linearGroups, prLabel, riskChip, runBuckets, runCardNote, runDisplayId, runStatusBadgeClass, taskProgress, trackerHref } from '../model.ts';
import { postWorkDelete, type DeletePayload } from './delete.ts';
import { InitRepo } from './InitRepo.tsx';

// data-label + cell-empty: the phone layout (style.css, max-width 768px) re-flows these tables
// as stacked label/value rows and hides a cell with nothing in it.
function Cell({ label, className, children }: { label: string; className?: string; children?: ReactNode }) {
  const empty = children == null || children === '' || children === false;
  const cls = [empty && 'cell-empty', className].filter(Boolean).join(' ');
  return (
    <td data-label={label} className={cls || undefined}>
      {empty ? null : children}
    </td>
  );
}

// CMX-94: on a Linear workflow the id links to its issue (the server's `tracker_url`).
function TrackerLink({ item, children }: { item: { tracker_url?: string | null }; children: ReactNode }) {
  const href = trackerHref(item);
  return href ? (
    <a className="run-tracker-link" href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : (
    <>{children}</>
  );
}

type Confirm = 'ask' | 'busy' | { error: string };

/** A row whose × asks first: the row collapses to one cell holding the confirm, in place. */
function DeletableRow({ payload, cols, taskId, children }: { payload: DeletePayload | null; cols: number; taskId?: string; children: ReactNode }) {
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  async function remove(p: DeletePayload) {
    setConfirm('busy');
    const err = await postWorkDelete(p);
    // Deleted: the refreshed payload has already dropped the row; if it has not, it is back.
    setConfirm(err ? { error: err } : null);
  }
  return (
    <tr data-task-id={taskId} className={confirm ? 'row-confirming' : undefined}>
      {children}
      <Cell label="Delete">{payload && !confirm && <IconButton className="dispatcher-delete-btn" label="Delete this row" onClick={() => setConfirm('ask')}><icons.close aria-hidden size={14} /></IconButton>}</Cell>
      {confirm && payload && (
        <td className="row-confirm-cell" colSpan={cols}>
          <div className="kanban-confirm">
            {confirm === 'ask' ? (
              <>
                <span className="kanban-confirm-msg">Delete this row?</span>
                <Button variant="danger" size="sm" className="btn-confirm" onClick={() => void remove(payload)}>Delete</Button>
                <Button size="sm" onClick={() => setConfirm(null)}>Cancel</Button>
              </>
            ) : confirm === 'busy' ? (
              <span className="kanban-confirm-msg">Deleting…</span>
            ) : (
              <>
                <span className="kanban-confirm-msg runs-error">{confirm.error}</span>
                <Button size="sm" onClick={() => setConfirm(null)}>Close</Button>
              </>
            )}
          </div>
        </td>
      )}
    </tr>
  );
}

const RUN_COLS = ['Task', 'Status', 'Tasks', 'Branch', 'Window', 'Started', 'Ended', 'Attempt', 'PR', 'Error', ''];

function Table({ heads, children }: { heads: string[]; children: ReactNode }) {
  return (
    <div className="table-wrap dispatcher-table-wrap">
      <table className="dispatcher-table">
        <thead>
          <tr>{heads.map((h, i) => <th key={i}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function RunRow({ r, linear }: { r: Run; linear: boolean }) {
  const id = runDisplayId(r);
  const progress = taskProgress(r.tasks);
  const note = runCardNote(r);
  return (
    <DeletableRow payload={r.task_id ? { kind: 'run', task_id: r.task_id } : null} cols={RUN_COLS.length} taskId={r.task_id ?? ''}>
      <td data-label="Task" title={r.task_id ?? ''}>
        <b>{(r.title || '').slice(0, 80)}</b>
        <div className="ts">{linear ? <TrackerLink item={r}>{id}</TrackerLink> : id}</div>
      </td>
      <Cell label="Status"><span className={`badge ${runStatusBadgeClass(r.status)}`}>{r.status || '?'}</span></Cell>
      <Cell label="Tasks">{progress && <span className="task-progress-chip" title={progress.title}><icons.tasks aria-hidden size={12} /> {progress.label}</span>}</Cell>
      <Cell label="Branch">{r.branch_name}</Cell>
      <Cell label="Window">{r.window_name}</Cell>
      <Cell label="Started" className="ts">{shortTime(r.started_at)}</Cell>
      <Cell label="Ended" className="ts">{shortTime(r.ended_at)}</Cell>
      <Cell label="Attempt">{r.attempt || ''}</Cell>
      <Cell label="PR">{r.pr_url && <a className="pr-badge" href={r.pr_url} target="_blank" rel="noopener noreferrer" title={r.pr_url}>{prLabel(r.pr_url)}</a>}</Cell>
      {/* The Error cell; for a run closed by hand, "Closed: <reason>" instead (CMX-406). */}
      <Cell label="Error">{note && <span title={note.text}>{note.text.slice(0, 60)}</span>}</Cell>
    </DeletableRow>
  );
}

function RunsTable({ runs, label, linear }: { runs: Run[]; label: string; linear: boolean }) {
  if (!runs.length) return <div className="runs-empty">No {label}.</div>;
  return <Table heads={RUN_COLS}>{runs.map((r, i) => <RunRow key={`${r.task_id ?? ''}:${r.attempt ?? ''}:${i}`} r={r} linear={linear} />)}</Table>;
}

function OpenTasks({ tasks }: { tasks: OpenTask[] }) {
  if (!tasks.length) return <div className="runs-empty">No open tasks.</div>;
  return (
    <Table heads={['Task ID', 'Title', 'Source', '']}>
      {tasks.map((t, i) => (
        <DeletableRow key={`${t.id}:${i}`} payload={t.file && t.title ? { kind: 'source-line', file: t.file, text: t.title } : null} cols={4}>
          <td data-label="Task ID"><code>{t.id}</code></td>
          <td data-label="Title">{(t.title || '').slice(0, 200)}</td>
          <td data-label="Source" className="ts">{t.file}:{t.line_number}</td>
        </DeletableRow>
      ))}
    </Table>
  );
}

// CMX-94: a Linear issue row: its identifier linked, its Linear state, its risk. No
// file:line (a Linear issue has none) and no ×: the dashboard does not write to Linear.
function LinearTasks({ tasks }: { tasks: OpenTask[] }) {
  if (!tasks.length) return null;
  return (
    <Table heads={['Issue', 'Title', 'State', 'Risk']}>
      {tasks.map((t, i) => {
        const risk = riskChip(t);
        return (
          <tr key={`${t.id}:${i}`}>
            <Cell label="Issue"><TrackerLink item={t}><code>{t.id}</code></TrackerLink></Cell>
            <td data-label="Title">{(t.title || '').slice(0, 200)}</td>
            <Cell label="State">{(t.tracker_state || '') + (t.blocked ? ' · blocked' : '')}</Cell>
            <Cell label="Risk">{risk && <span className={`kanban-risk-chip ${risk.cls}`} title={risk.reason}>{risk.label}</span>}</Cell>
          </tr>
        );
      })}
    </Table>
  );
}

function LinearOpenTasks({ open }: { open: OpenTask[] }) {
  const { ready, started, backlog } = linearGroups(open);
  return (
    <>
      <div className="open-section runs-section">Ready (Todo) · {ready.length}</div>
      <LinearTasks tasks={ready} />
      <div className="open-section runs-section">In progress / In review · {started.length}</div>
      <LinearTasks tasks={started} />
      <details className="open-backlog">
        <summary className="open-section runs-section">Backlog · {backlog.length}</summary>
        <LinearTasks tasks={backlog} />
      </details>
    </>
  );
}

function WorkflowCard({ wf }: { wf: Workflow }) {
  const linear = wf.tracker_kind === 'linear';
  const open = wf.open_tasks || [];
  const b = runBuckets(wf);
  // On Linear the headline number is READY (Todo), not every non-terminal issue.
  const openSummary = linear ? `${linearGroups(open).ready.length} ready` : `${open.length} open task${open.length === 1 ? '' : 's'}`;
  const tables: [string, string, Run[], string][] = [
    ['needs_human', 'Needs a human', b.needsHuman, 'runs needing a human'],
    ['active', 'Active runs', b.active, 'active runs'],
    ['awaiting', 'Awaiting review', b.awaiting, 'awaiting review runs'],
    ['recent', 'Recent runs', b.recent, 'recent runs'],
  ];
  return (
    <div className="card">
      <h3>{wf.path}</h3>
      <div className="runs-summary">
        {openSummary} · {b.needsHuman.length} needing a human · {b.active.length} active · {b.awaiting.length} awaiting review · {b.recent.length} recent
      </div>
      {wf.error && <div className="runs-error">Error: {wf.error}</div>}
      {linear ? (
        <LinearOpenTasks open={open} />
      ) : (
        <>
          <div className="runs-section">Open tasks</div>
          <OpenTasks tasks={open} />
        </>
      )}
      {tables.map(([key, title, runs, label]) => (
        <div key={key}>
          <div className="runs-section">{title}</div>
          <div data-runs={key}>
            <RunsTable runs={runs} label={label} linear={linear} />
          </div>
        </div>
      ))}
    </div>
  );
}

function Empty() {
  return (
    <div className="work-empty">
      <div className="work-empty-icon"><icons.runs aria-hidden size={30} /></div>
      <div className="work-empty-title">Turn a TODO list into pull requests</div>
      <div className="work-empty-body">
        Point chela at a <code>WORKFLOW.md</code>, a plain markdown checklist. For every open <code>- [ ]</code> item it spins up a git worktree,
        launches a Claude agent in a fresh tmux window with that task as its prompt, and opens a PR when the agent finishes. Merge the PR and the
        line checks itself off on the next tick. This is what makes chela an orchestrator, not just a viewer.
      </div>
      <div className="work-empty-hint">
        Use <strong>+ Init a repo</strong> above to seed the files, then set <code>CHELA_DISPATCH_WORKFLOWS</code> to a colon-separated list of{' '}
        <code>WORKFLOW.md</code> paths and restart the daemon.
      </div>
    </div>
  );
}

/** The island in #work-runs: nothing until the first poll, the explainer with no workflows. */
export function Runs() {
  const data = useStore(work);
  const wfs = data?.configured ? data.workflows || [] : [];
  return (
    <>
      {data && (wfs.length ? <div id="dispatcher-list">{wfs.map((wf) => <WorkflowCard key={wf.path} wf={wf} />)}</div> : <Empty />)}
      <InitRepo />
    </>
  );
}
