// A card's actions, shared by the desktop card (its inline buttons) and the compact card and
// Rows row (CMX-315: every action, the branch and the destructive ones, in the card's ⋮).
// Each server action asks first, shows that it is running, and surfaces a failure ON the card
// in the server's own words for 15 s: never swallowed.
import { useEffect, useState, type MouseEvent, type ReactNode } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { Button, IconButton, Menu, type MenuItem } from '../../../shared/ui/index.ts';
import { postWorkDelete, type DeletePayload } from '../../runs/index.ts';
import { prLabel, runCloseAction, runDisplayId, trackerHref } from '../../runs/model.ts';
import { openTaskModal } from '../../taskmodal/index.ts';
import { branchOrLine, mergeControl, type Card as CardData } from '../board.ts';
import { mergeRun, promoteBacklog, requeueRun } from './actions.ts';

export const stop = (e: MouseEvent) => e.stopPropagation();

export const prName = (url: string | null | undefined) => {
  const label = prLabel(url || '');
  return label === 'PR' ? 'this PR' : `PR ${label}`;
};

/** What a source line or a run deletes as; null when there is no target (a parked bullet's
 * title is comment-stripped, so it would never match its line, or match the wrong one). */
export function deletePayload(card: CardData): DeletePayload | null {
  if (card.status === 'backlog' || card.status === 'open') return card.file && card.title ? { kind: 'source-line', file: card.file, text: card.title } : null;
  if (card.status === 'parked') return null;
  return card.task_id ? { kind: 'run', task_id: card.task_id } : null;
}

export type Confirm = 'ask' | 'busy' | { error: string };

/** The delete confirm strip (not window.confirm). On success the refetch drops the card. */
export function DeleteStrip({ confirm, onDelete, onCancel }: { confirm: Confirm; onDelete: () => void; onCancel: () => void }) {
  return (
    <div className="kanban-confirm" onClick={stop}>
      {confirm === 'ask' ? (
        <>
          <span className="kanban-confirm-msg">Delete this?</span>
          <Button variant="danger" size="sm" className="btn-confirm" onClick={onDelete}>Delete</Button>
          <Button size="sm" onClick={onCancel}>Cancel</Button>
        </>
      ) : confirm === 'busy' ? (
        <span className="kanban-confirm-msg">Deleting…</span>
      ) : (
        <>
          <span className="kanban-confirm-msg" style={{ color: 'var(--red)' }}>{confirm.error}</span>
          <Button size="sm" onClick={onCancel}>Close</Button>
        </>
      )}
    </div>
  );
}

/** Delete with its confirm: `ask()` shows the strip, `strip` is it (or null). */
export function useDelete(payload: DeletePayload | null): { confirm: Confirm | null; ask: () => void; strip: ReactNode } {
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  async function remove() {
    if (!payload) return;
    setConfirm('busy');
    const err = await postWorkDelete(payload);
    setConfirm(err ? { error: err } : null);
  }
  const strip = confirm && <DeleteStrip confirm={confirm} onDelete={() => void remove()} onCancel={() => setConfirm(null)} />;
  return { confirm, ask: () => setConfirm('ask'), strip };
}

/** A failure surfaced ON the card with the server's own words, for 15 s: never swallowed. */
export function useCardError(): [{ cls: string; text: string } | null, (cls: string, text: string) => void] {
  const [err, setErr] = useState<{ cls: string; text: string } | null>(null);
  useEffect(() => {
    if (!err) return;
    const t = setTimeout(() => setErr(null), 15000);
    return () => clearTimeout(t);
  }, [err]);
  return [err, (cls, text) => setErr({ cls, text })];
}

const ellipsize = (text: string) => (text.length > 80 ? text.slice(0, 77) + '...' : text);

export const askMerge = (card: CardData) => window.confirm(`Squash-merge ${prName(card.pr_url)} and delete branch?`) && '';
export const askPromote = (text: string) => window.confirm(`Promote backlog item to TODO?\n\n"${ellipsize(text)}"\n\nThis commits to the base branch and pushes.`) && '';
export const askRequeue = (taskId: string) =>
  (window.prompt(`Requeue ${taskId}: why? (recorded in its review history; the old PR is closed and the next tick claims a fresh attempt on a new branch)`) || '').trim() || false;

function openUrl(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer');
}

/** The ⋮ menu's trigger, its items and what the card shows under it (the delete confirm, an
 * action running, a failure). */
export function useCardMenu(card: CardData): { menu: ReactNode; strip: ReactNode } {
  const [err, fail] = useCardError();
  const [running, setRunning] = useState<string | null>(null);
  const del = useDelete(deletePayload(card));

  // The question waits for the menu to close: a blocking confirm or prompt opened while Radix
  // still holds its focus trap and pointer lock can leave the page untappable on a phone.
  function run(busy: string, ask: () => string | false, act: (answer: string) => Promise<string | null>) {
    setTimeout(() => void settle(busy, ask(), act), 0);
  }
  async function settle(busy: string, answer: string | false, act: (answer: string) => Promise<string | null>) {
    if (answer === false) return;
    setRunning(busy);
    const e = await act(answer);
    setRunning(null);
    if (e) fail('kanban-merge-toast', e);
  }

  const taskId = card.task_id || '';
  const tracker = taskId ? trackerHref(card) : null;
  // A BACKLOG.md bullet has no line number to show.
  const line = card.status === 'backlog' ? '' : branchOrLine(card, runDisplayId(card));
  const close = runCloseAction(card);
  const wfPath = card.workflow_path;
  const text = card.title;
  const busy = running !== null || del.confirm !== null;

  const items: MenuItem[] = [{ label: 'Open details', icon: icons.expand, onSelect: () => openTaskModal(card) }];
  if (card.pr_url) {
    const url = card.pr_url;
    items.push({ label: `Open ${prName(url)}`, icon: icons.pullRequest, onSelect: () => openUrl(url) });
  }
  if (tracker) items.push({ label: `Open ${taskId} in Linear`, icon: icons.external, onSelect: () => openUrl(tracker) });
  if (line) {
    items.push({ label: line, icon: icons.branch, title: 'Copy the branch name', className: 'wcard-branch-item',
      onSelect: () => void navigator.clipboard?.writeText(line).catch(() => undefined) });
  }
  if (mergeControl(card) === 'button') {
    items.push({ label: `Merge ${prName(card.pr_url)}`, icon: icons.merged, disabled: busy, onSelect: () => run('Merging…', () => askMerge(card), () => mergeRun(taskId)) });
  }
  if (card.status === 'backlog' && wfPath && text) {
    items.push({ label: 'Promote to TODO', icon: icons.add, disabled: busy, onSelect: () => run('Promoting…', () => askPromote(text), () => promoteBacklog(wfPath, text)) });
  }
  const destructive: MenuItem[] = [];
  if (close) {
    destructive.push({ label: close.label, icon: icons.retry, className: 'wcard-requeue-item', disabled: busy,
      onSelect: () => run('Working…', () => askRequeue(taskId), (reason) => requeueRun(taskId, reason)) });
  }
  if (deletePayload(card)) destructive.push({ label: 'Delete', icon: icons.close, className: 'wcard-delete-item', disabled: busy, onSelect: del.ask });
  if (destructive.length) items.push('separator', ...destructive);

  const menu = (
    <span className="wcard-menu" onClick={stop} onKeyDown={(e) => e.stopPropagation()}>
      <Menu className="wcard-overflow-menu" items={items}
        trigger={
          <IconButton className="wcard-menu-btn" label="Card actions">
            <icons.paneMenu size={18} aria-hidden />
          </IconButton>
        } />
    </span>
  );
  const strip = (
    <>
      {del.strip}
      {running && <div className="wcard-running" role="status">{running}</div>}
      {err && <div className={`wcard-error ${err.cls}`} role="alert">{err.text}</div>}
    </>
  );
  return { menu, strip };
}
