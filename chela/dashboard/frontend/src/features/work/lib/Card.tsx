// One Work board card, and its actions (delete, merge, requeue, promote). Ported from
// static/js/kanban.js's _kCard by CMX-233. A click on the card opens the task modal with the
// SAME object the board holds (from the one /api/dispatcher poll), never a refetch; every
// control inside it stops the click so it never ALSO opens the modal.
import { useEffect, useState, type DragEvent, type MouseEvent, type ReactNode } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { Markdown } from '../../../shared/markdown/index.ts';
import { Button, IconButton, TextButton } from '../../../shared/ui/index.ts';
import { postWorkDelete, type DeletePayload } from '../../runs/index.ts';
import { prLabel, riskChip, runCardNote, runCloseAction, runDisplayId, runHandoffNote, runStallNote, taskProgress, trackerHref } from '../../runs/model.ts';
import { openTaskModal } from '../../taskmodal/index.ts';
import { CI_CHIP, ciKind, displayTitle, wfShort } from '../../taskmodal/model.ts';
import { STATUS_CHIPS, branchOrLine, mergeControl, type Card as CardData } from '../board.ts';
import { runStateBadges } from '../linear.ts';
import { mergeRun, promoteBacklog, requeueRun } from './actions.ts';

const stop = (e: MouseEvent) => e.stopPropagation();
const prName = (url: string | null | undefined) => {
  const label = prLabel(url || '');
  return label === 'PR' ? 'this PR' : `PR ${label}`;
};
const CI_ICON = { passing: icons.ciPassing, failing: icons.ciFailing, pending: icons.ciPending, none: icons.ciNone, unknown: icons.ciUnknown };

/** What a source line or a run deletes as; null when there is no target (a parked bullet's
 * title is comment-stripped, so it would never match its line, or match the wrong one). */
function deletePayload(card: CardData): DeletePayload | null {
  if (card.status === 'backlog' || card.status === 'open') return card.file && card.title ? { kind: 'source-line', file: card.file, text: card.title } : null;
  if (card.status === 'parked') return null;
  return card.task_id ? { kind: 'run', task_id: card.task_id } : null;
}

type Confirm = 'ask' | 'busy' | { error: string };

/** The × and its inline confirm strip (not window.confirm). On success the refetch drops the card. */
function DeleteControl({ payload }: { payload: DeletePayload }) {
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  async function remove() {
    setConfirm('busy');
    const err = await postWorkDelete(payload);
    setConfirm(err ? { error: err } : null);
  }
  return (
    <>
      <IconButton className="kanban-delete-btn" label="Delete this card" style={confirm ? { visibility: 'hidden' } : undefined}
        onClick={(e) => { e.stopPropagation(); setConfirm('ask'); }}>
        <icons.close aria-hidden size={14} />
      </IconButton>
      {confirm && (
        <div className="kanban-confirm" onClick={stop}>
          {confirm === 'ask' ? (
            <>
              <span className="kanban-confirm-msg">Delete this?</span>
              <Button variant="danger" size="sm" className="btn-confirm" onClick={() => void remove()}>Delete</Button>
              <Button size="sm" onClick={() => setConfirm(null)}>Cancel</Button>
            </>
          ) : confirm === 'busy' ? (
            <span className="kanban-confirm-msg">Deleting…</span>
          ) : (
            <>
              <span className="kanban-confirm-msg" style={{ color: 'var(--red)' }}>{confirm.error}</span>
              <Button size="sm" onClick={() => setConfirm(null)}>Close</Button>
            </>
          )}
        </div>
      )}
    </>
  );
}

/** A failure surfaced ON the card with the server's own words, for 15 s: never swallowed. */
function useCardError(): [{ cls: string; text: string } | null, (cls: string, text: string) => void] {
  const [err, setErr] = useState<{ cls: string; text: string } | null>(null);
  useEffect(() => {
    if (!err) return;
    const t = setTimeout(() => setErr(null), 15000);
    return () => clearTimeout(t);
  }, [err]);
  return [err, (cls, text) => setErr({ cls, text })];
}

/** A card action: asks first (`ask` returns false to abort, or the reason text), shows `busy`
 * while it runs, and hands a failure to `fail`. */
function Action({ className, label, busy, ask, act, fail }: { className: string; label: string; busy: string; ask: () => string | false; act: (answer: string) => Promise<string | null>; fail: (text: string) => void }) {
  const [running, setRunning] = useState(false);
  async function click(e: MouseEvent) {
    e.stopPropagation();
    const answer = ask();
    if (answer === false) return;
    setRunning(true);
    const err = await act(answer);
    setRunning(false);
    if (err) fail(err);
  }
  return <TextButton className={className} disabled={running} onClick={(e) => void click(e)}>{running ? busy : label}</TextButton>;
}

function Chip({ className, title, icon: Icon, children }: { className: string; title?: string; icon?: (typeof icons)[keyof typeof icons]; children: ReactNode }) {
  return (
    <span className={className} title={title}>
      {Icon && <Icon aria-hidden size={12} />} {children}
    </span>
  );
}

type Props = { card: CardData; onDragStart: (card: CardData, e: DragEvent) => void; onDragEnd: () => void };

export function Card({ card, onDragStart, onDragEnd }: Props) {
  const [err, fail] = useCardError();
  const del = deletePayload(card);
  const title = <div className="kanban-card-title"><Markdown inline source={displayTitle(card.title || '').slice(0, 200)} /></div>;
  const wf = <span className="kanban-wf-chip">{wfShort(card.workflow_path) || '?'}</span>;
  const shell = (body: ReactNode) => (
    <div className={`kanban-card kanban-card-${card.status}`} data-task-id={card.task_id || undefined} draggable
      onClick={() => openTaskModal(card)} onDragStart={(e) => onDragStart(card, e)} onDragEnd={onDragEnd}>
      {del && <DeleteControl payload={del} />}
      {body}
      {err && <div className={err.cls}>{err.text}</div>}
    </div>
  );
  if (card.status === 'backlog') {
    const wfPath = card.workflow_path;
    const text = card.title;
    return shell(
      <>
        {title}
        <div className="kanban-card-meta">
          {wf}
          {card.section && <span className="kanban-section-chip">{card.section}</span>}
          {wfPath && text && (
            <Action className="kanban-promote-btn" label="Promote" busy="Promoting…" fail={(t) => fail('kanban-promote-toast', t)}
              ask={() => window.confirm(`Promote backlog item to TODO?\n\n"${text.length > 80 ? text.slice(0, 77) + '...' : text}"\n\nThis commits to the base branch and pushes.`) && ''}
              act={() => promoteBacklog(wfPath, text)} />
          )}
        </div>
      </>,
    );
  }
  if (card.status === 'parked') {
    // A parked TODO.md bullet: no Promote (it is already in TODO.md) and no delete (above).
    return shell(
      <>
        {title}
        <div className="kanban-card-meta">
          {wf}
          <Chip className="kanban-parked-reason" title={card.reason || undefined} icon={icons.parked}>{card.reason || 'parked'}</Chip>
        </div>
      </>,
    );
  }
  const taskId = card.task_id || '';
  const displayId = runDisplayId(card);
  const tracker = taskId ? trackerHref(card) : null;
  const chip = STATUS_CHIPS[card.status];
  const risk = riskChip(card);
  const progress = taskProgress(card.tasks);
  const ci = ciKind(card.pr_checks ?? undefined);
  const merge = mergeControl(card);
  const close = runCloseAction(card);
  const note = runCardNote(card);
  const handoff = runHandoffNote(card);
  const stall = runStallNote(card);
  const line = branchOrLine(card, displayId);
  return shell(
    <>
      {title}
      <div className="kanban-card-meta">
        {wf}
        {tracker ? (
          // CMX-5: the URL the Linear adapter fetched, never one built from a guessed slug.
          <a className="kanban-card-id kanban-tracker-link" href={tracker} target="_blank" rel="noopener noreferrer" title={`Open ${taskId} in Linear`} onClick={stop}>
            {taskId} <icons.external aria-hidden size={11} />
          </a>
        ) : (
          <span className="kanban-card-id" title={taskId}>{displayId}</span>
        )}
        {line && <span className="ts">{line}</span>}
        {chip && <Chip className={`kanban-state-chip ${chip.cls}`} icon={icons[chip.icon]}>{chip.label}</Chip>}
        {runStateBadges(card).map((b) => <Chip key={b.cls} className={`kanban-state-chip ${b.cls}`} icon={icons[b.icon]}>{b.label}</Chip>)}
        {risk && <span className={`kanban-risk-chip ${risk.cls}`} title={risk.reason || risk.label}>{risk.label}</span>}
        {progress && <Chip className="task-progress-chip" title={progress.title} icon={icons.tasks}>{progress.label}</Chip>}
        {card.pr_url && (
          <span className="kanban-pr-wrap" onClick={stop}>
            <a className="pr-badge" href={card.pr_url} target="_blank" rel="noopener noreferrer" title={card.pr_url}>{prLabel(card.pr_url)}</a>
          </span>
        )}
        {/* On every card with a PR, merged ones too: the receipt of what its checks said. */}
        {card.pr_url && <Chip className={`kanban-ci-chip ${CI_CHIP[ci].cls}`} title="GitHub's checks on this PR" icon={CI_ICON[ci]}>{CI_CHIP[ci].label}</Chip>}
        {merge === 'merged' && <Chip className="kanban-merged-badge" title="PR already merged" icon={icons.merged}>Merged</Chip>}
        {merge === 'button' && (
          <Action className="kanban-merge-btn" label="Merge" busy="Merging…" fail={(t) => fail('kanban-merge-toast', t)}
            ask={() => window.confirm(`Squash-merge ${prName(card.pr_url)} and delete branch?`) && ''} act={() => mergeRun(taskId)} />
        )}
        {close && (
          <Action className="kanban-requeue-btn" label={close.label} busy="Working…" fail={(t) => fail('kanban-merge-toast', t)}
            ask={() => (window.prompt(`Requeue ${taskId}: why? (recorded in its review history; the old PR is closed and the next tick claims a fresh attempt on a new branch)`) || '').trim() || false}
            act={(reason) => requeueRun(taskId, reason)} />
        )}
      </div>
      {handoff && (
        // CMX-52: the agent's own `task-finished --handoff` note, one line; the full note is the tooltip.
        <div className="kanban-card-handoff" title={handoff}><icons.handoff aria-hidden size={12} /> {handoff.slice(0, 160)}{handoff.length > 160 ? '…' : ''}</div>
      )}
      {note && <div className={note.closed ? 'kanban-card-closed-note' : 'kanban-card-error'} title={note.text}>{note.text.slice(0, 120)}</div>}
      {stall && <div className="kanban-card-stall" title={stall}><icons.warn aria-hidden size={12} /> {stall}</div>}
    </>,
  );
}
