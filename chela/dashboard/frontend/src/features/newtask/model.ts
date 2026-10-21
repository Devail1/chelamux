// ➕📐 CMX-6: what the New-task form offers, off the /api/dispatcher payload features/work polls.
import type { Workflow, WorkPayload } from '../../shared/store/work.ts';

/** The workflows chela can create issues in: those whose tracker is linear. */
export function linearWorkflows(data: WorkPayload | null | undefined): Workflow[] {
  return (data?.workflows || []).filter((wf) => wf && wf.tracker_kind === 'linear');
}

/** Open issues a new task may be blocked by: the open tasks plus the in-flight runs. */
export function blockerChoices(wf: Workflow | null | undefined): { id: string; title: string }[] {
  if (!wf) return [];
  const out = new Map<string, string>();
  const add = (id: string | null | undefined, title: string | null | undefined) => {
    if (id && !out.has(id)) out.set(id, title || '');
  };
  for (const t of wf.open_tasks || []) add(t.id, t.title);
  for (const r of [...(wf.active_runs || []), ...(wf.awaiting_review_runs || [])]) add(r.task_id, r.title);
  return [...out].map(([id, title]) => ({ id, title }));
}

/** Linear's priorities, in its own order (0 = none). */
export const PRIORITIES = [
  { value: '0', label: 'No priority' },
  { value: '1', label: 'Urgent' },
  { value: '2', label: 'High' },
  { value: '3', label: 'Medium' },
  { value: '4', label: 'Low' },
];
