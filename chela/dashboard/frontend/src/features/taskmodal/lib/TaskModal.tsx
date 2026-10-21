// ---------------------------------------------------------------------------
// The task-detail modal: a Jira-like "issue" view a kanban card click (or a Decisions row)
// opens. LEFT (wide): tabs, Brief (the task's brief as markdown) | Review (N) (one
// collapsible card per `review_history` round, newest first and open). RIGHT (narrow): the
// compact details sidebar (workflow / branch / PR / CI / attempts / rework / judge /
// timestamps / error) and a round list that jumps to a round's card.
//
// History, so it doesn't get re-litigated: the timeline sat in the sidebar (2026-07-25) so
// the brief could lead. CMX-122 (Liav, 2026-10-13): after 13 rounds a sidebar of full judge
// reports was unreadable, so the reports moved to the wide column behind a tab, and the
// default tab follows the run: Review while a verdict is what needs reading, Brief otherwise
// (defaultTab()). Ported from static/js/taskmodal.js by CMX-228 (CMX-212 P09).
//
// openTaskModal(item) never fetches: it renders the SAME object its caller already holds
// (kanban.js's card from the one /api/dispatcher poll, or a decision's resolved run). Every
// field is optional (TaskItem), because an open task and a backlog item carry far fewer.
// ---------------------------------------------------------------------------
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { runStatusBadgeClass } from '../../../../../static/js/runstate.js';
import { icons } from '../../../shared/icons/index.ts';
import { Markdown } from '../../../shared/markdown/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Dialog, DialogClose, IconButton, Tabs } from '../../../shared/ui/index.ts';
import {
  CI_CHIP,
  briefSource,
  ciKind,
  defaultTab,
  displayTitle,
  judgeBadgeClass,
  prLabel,
  riskChip,
  runDisplayId,
  shortTime,
  statusLabel,
  timelineSteps,
  trackerHref,
  wfShort,
  type Round,
  type TabName,
  type TaskItem,
} from '../model.ts';

/** What the modal shows; `seq` makes every open a fresh mount, even of the same item. */
const target = createStore<{ item: TaskItem; seq: number } | null>(null);
let seq = 0;

/** Open the modal on `item` (a run, an open task or a backlog item). */
export function openTaskModal(item: TaskItem | null | undefined): void {
  if (item) target.set({ item, seq: ++seq });
}

export function closeTaskModal(): void {
  target.set(null);
}

// Per-task last tab, for this page session only (gone on reload, by design). Only a viewer's
// own choice is kept, never the default the modal opened on, or a run that later moves to
// awaiting_review would stay pinned to Brief.
const tabByTask = new Map<string, TabName>();

const CI_ICON = { passing: icons.ciPassing, failing: icons.ciFailing, pending: icons.ciPending, none: icons.ciNone, unknown: icons.ciUnknown };

function Ts({ children }: { children: ReactNode }) {
  return <span className="ts">{children}</span>;
}

function Badge({ cls, children }: { cls: string; children: ReactNode }) {
  return <span className={`badge ${cls}`}>{children}</span>;
}

function Verdict({ round }: { round: Round }) {
  return <Badge cls={runStatusBadgeClass(round.state)}>{statusLabel(round.state)}</Badge>;
}

const roundNo = (r: Round, i: number) => String(r.round != null ? r.round : i + 1);

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="task-modal-row">
      <div className="task-modal-row-label">{label}</div>
      <div className="task-modal-row-value">{children}</div>
    </div>
  );
}

function PrValue({ item }: { item: TaskItem }) {
  if (!item.pr_url) return null;
  const ci = ciKind(item.pr_checks);
  const Icon = CI_ICON[ci];
  return (
    <>
      <a className="pr-badge" href={item.pr_url} target="_blank" rel="noopener noreferrer" title={item.pr_url}>
        {prLabel(item.pr_url)}
      </a>
      {item.pr_state && <> <Ts>{item.pr_state}</Ts></>}{' '}
      <span className={`kanban-ci-chip ${CI_CHIP[ci].cls}`}>
        <Icon aria-hidden size={11} /> {CI_CHIP[ci].label}
      </span>
    </>
  );
}

// The details rows that have a value, in order; a row with nothing to say is left out.
function sideRows(item: TaskItem): [string, ReactNode][] {
  const risk = riskChip(item);
  const rows: [string, ReactNode][] = [
    ['Workflow', wfShort(item.workflow_path)],
    ['Branch', item.branch_name],
    ['Model', item.model],
    ['PR', item.pr_url && <PrValue item={item} />],
    ['Attempt', item.attempt != null ? String(item.attempt) : ''],
    // ⚖️🎚️ CMX-405: the run's risk level, with WHY it has it (marker / label / inferred / default).
    ['Risk', risk && (
      <>
        <span className={`kanban-risk-chip ${risk.cls}`}>{risk.label}</span>
        {risk.reason && <> <Ts>{risk.reason}</Ts></>}
      </>
    )],
    ['Reworks', item.rework_count ? String(item.rework_count) : ''],
    ['Judge', item.judge_state && (
      <>
        <Badge cls={judgeBadgeClass(item.judge_state)}>{item.judge_state}</Badge>
        {item.judge_detail && <> <Ts>{item.judge_detail}</Ts></>}
      </>
    )],
    ['Started', item.started_at ? shortTime(item.started_at) : ''],
    ['Ended', item.ended_at ? shortTime(item.ended_at) : ''],
    ['Error', item.last_error && <span className="task-modal-error">{item.last_error}</span>],
  ];
  return rows.filter(([, v]) => v != null && v !== '' && v !== false);
}

// The header's task key: a link to the tracker issue when the tracker handed us its URL
// (trackerHref, https only), else the plain chip it always was.
function Key({ item }: { item: TaskItem }) {
  const id = runDisplayId(item);
  if (!id) return null;
  const href = trackerHref(item);
  if (!href) return <span className="kanban-card-id">{id}</span>;
  return (
    <a className="kanban-card-id kanban-tracker-link" href={href} target="_blank" rel="noopener noreferrer" title={`Open ${id} in Linear`}>
      {id} <icons.external aria-hidden size={12} />
    </a>
  );
}

/** The Review tab: one <details> per round, NEWEST first. `data-ri` is the round's index in
 * review_history (oldest = 0), the round list's jump target: `jump` names the round to scroll
 * to, scrolled once this panel is on screen (it mounts when the tab is chosen). */
function Rounds({ steps, open, setOpen, jump, onJumped }: { steps: Round[]; open: Set<number>; setOpen: (i: number, on: boolean) => void; jump: { ri: number } | null; onJumped: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!jump) return;
    ref.current?.querySelector(`.task-modal-round[data-ri="${jump.ri}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    onJumped(); // once: coming back to the tab later keeps the viewer's own scroll
  }, [jump, onJumped]);
  if (!steps.length) return <div className="task-modal-empty">No review history yet.</div>;
  return (
    <div ref={ref}>
      {steps
        .map((s, i) => (
          <details key={i} className="task-modal-round" data-ri={i} open={open.has(i)} onToggle={(e) => setOpen(i, e.currentTarget.open)}>
            <summary className="task-modal-timeline-head">
              <span className="task-modal-round-no">Round {roundNo(s, i)}</span> <Verdict round={s} />{' '}
              {(s.visible > 0 || s.heldOut > 0) && <Ts>{`${s.visible} survived · ${s.heldOut} held-out`}</Ts>}{' '}
              {s.at && <Ts>{shortTime(s.at)}</Ts>}
            </summary>
            {s.detail && <Markdown className="task-modal-timeline-body md" source={s.detail} />}
          </details>
        ))
        .reverse()}
    </div>
  );
}

/** One open of the modal: remounted per open, so nothing from the last item lingers. */
function TaskBody({ item }: { item: TaskItem }) {
  const steps = timelineSteps(item.review_history);
  const key = item.task_id || item.id || null;
  const [tab, setTab] = useState<TabName>(() => (key && tabByTask.get(key)) || defaultTab(item, steps.length));
  // Only the newest round starts open.
  const [open, setOpenSet] = useState(() => new Set(steps.length ? [steps.length - 1] : []));
  const [jump, setJump] = useState<{ ri: number } | null>(null);
  const clearJump = useCallback(() => setJump(null), []);

  const choose = (v: string) => {
    const t = v === 'review' ? 'review' : 'brief';
    if (key) tabByTask.set(key, t);
    setTab(t);
  };
  const setOpen = (i: number, on: boolean) =>
    setOpenSet((prev) => {
      if (prev.has(i) === on) return prev;
      const next = new Set(prev);
      if (on) next.add(i);
      else next.delete(i);
      return next;
    });

  // A round-list click: switch to Review, open that round's card, scroll it into view.
  const jumpTo = (i: number) => {
    choose('review');
    setOpen(i, true);
    setJump({ ri: i });
  };

  const wf = wfShort(item.workflow_path);
  const brief = briefSource(item);
  const rows = sideRows(item);
  return (
    <div id="task-modal-content">
      <div className="task-modal-head">
        <div className="task-modal-headline">
          {wf && <span className="kanban-wf-chip">{wf}</span>}
          <Key item={item} />
          <Badge cls={runStatusBadgeClass(item.status)}>{statusLabel(item.status)}</Badge>
        </div>
        <h3 className="task-modal-title">
          <Markdown inline source={titleText(item)} />
        </h3>
      </div>
      <div className="task-modal-body">
        <div className="task-modal-main">
          <Tabs
            label="Task"
            value={tab}
            onValueChange={choose}
            classes={{ list: 'task-modal-tabs', tab: 'task-modal-tab' }}
            tabs={[
              {
                value: 'brief',
                label: 'Brief',
                className: 'task-modal-brief md',
                // briefSource() picks brief > body > raw. None of the three (a backlog item, a
                // legacy pre-migration run row) degrades to a plain note, never a blank pane.
                content: brief ? <Markdown source={brief} /> : <div className="task-modal-empty">No brief recorded for this task.</div>,
              },
              {
                value: 'review',
                label: `Review (${steps.length})`,
                className: 'task-modal-review',
                content: <Rounds steps={steps} open={open} setOpen={setOpen} jump={jump} onJumped={clearJump} />,
              },
            ]}
          />
        </div>
        <div className="task-modal-side">
          <div className="task-modal-sub">Details</div>
          {rows.length ? (
            rows.map(([label, v]) => (
              <Row key={label} label={label}>
                {v}
              </Row>
            ))
          ) : (
            <div className="task-modal-empty">No details recorded.</div>
          )}
          {steps.length > 0 && (
            <div className="task-modal-rounds">
              <div className="task-modal-sub">Rounds</div>
              <ol className="task-modal-roundlist">
                {steps
                  .map((s, i) => (
                    <li key={i}>
                      <IconButton className="task-modal-roundlink" label={`Round ${roundNo(s, i)}: ${statusLabel(s.state)}`} onClick={() => jumpTo(i)}>
                        Round {roundNo(s, i)} <Verdict round={s} />
                      </IconButton>
                    </li>
                  ))
                  .reverse()}
              </ol>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// displayTitle() strips a leading bold-span brief down to its concise inner text (display
// only: the PARSED item.title is never touched); the rest renders as inline markdown.
function titleText(item: TaskItem): string {
  return displayTitle(item.title || '(untitled)').slice(0, 300);
}

/** The island: the dialog, open while an item is targeted. */
export function TaskModal() {
  const t = useStore(target);
  return (
    <Dialog open={t !== null} onOpenChange={(o) => !o && closeTaskModal()} title={(t && titleText(t.item).trim()) || 'Task'} hideTitle className="modal-task">
      <DialogClose asChild>
        <IconButton className="task-modal-close" label="Close">
          <icons.close aria-hidden size={18} />
        </IconButton>
      </DialogClose>
      {t && <TaskBody key={t.seq} item={t.item} />}
    </Dialog>
  );
}
