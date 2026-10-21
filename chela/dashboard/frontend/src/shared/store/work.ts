// CMX-231 (CMX-212 P10): the last /api/dispatcher payload. work.js owns the app's ONE poll of
// it and publishes every answer here; the Runs tables and the New-task form read it, and ask
// work.js for a fresh poll through refreshWork() (after an init, a delete, a create).
import { createStore } from '../bridge/index.ts';

export type TaskProgress = {
  total: number;
  done: number;
  in_progress?: { subject: string } | null;
  blocked?: { subject: string; blocked_by?: string[] }[];
};

/** One dispatcher run row (chela/dispatcher.py), typed as loosely as the server sends it. */
export type Run = {
  task_id?: string | null;
  title?: string | null;
  status?: string | null;
  project_key?: string | null;
  task_number?: number | null;
  tracker_url?: string | null;
  branch_name?: string | null;
  window_name?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  attempt?: number | null;
  pr_url?: string | null;
  pr_state?: string | null;
  last_error?: string | null;
  close_reason?: string | null;
  handoff?: string | null;
  closed_run_stall?: string | null;
  tasks?: TaskProgress | null;
};

/** An open task: a TODO.md line, or a Linear issue (with `group`, `tracker_state`, `risk`). */
export type OpenTask = {
  id: string;
  title?: string | null;
  file?: string | null;
  line_number?: number | null;
  group?: string | null;
  tracker_state?: string | null;
  tracker_url?: string | null;
  blocked?: boolean;
  risk?: string | null;
  risk_reason?: string | null;
};

export type Workflow = {
  path: string;
  project_key?: string | null;
  tracker_kind?: string | null;
  error?: string | null;
  open_tasks?: OpenTask[];
  active_runs?: Run[];
  awaiting_review_runs?: Run[];
  recent_runs?: Run[];
};

export type WorkPayload = { configured?: boolean; workflows?: Workflow[] };

/** null until work.js's first poll lands. */
export const work = createStore<WorkPayload | null>(null);

let poll: () => Promise<void> = () => Promise.resolve();

/** work.js registers its poll here at import. */
export function setWorkPoll(fn: () => Promise<void>) {
  poll = fn;
}

/** Refetch /api/dispatcher now (work.js's poll); resolves once it has been published. */
export function refreshWork(): Promise<void> {
  return poll();
}
