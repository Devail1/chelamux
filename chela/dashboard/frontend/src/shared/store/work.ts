// CMX-231 (CMX-212 P10): the last /api/dispatcher payload. features/work owns the app's ONE
// poll of it (a scheduler lane since CMX-233) and publishes every answer here; the Work view,
// the Runs tables and the New-task form read it, and ask for a fresh poll through
// refreshWork() (after an init, a delete, a create).
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
  tracker_note?: string | null;
  handoff?: string | null;
  closed_run_stall?: string | null;
  tasks?: TaskProgress | null;
  pr_checks?: string | null;
  judge_state?: string | null;
  rework_count?: number | null;
  tracker_state?: string | null;
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
  raw?: string | null;
  body?: string | null;
  closed_run_stall?: string | null;
};

/** A BACKLOG.md bullet (read-only on the board) and a parked TODO.md bullet. */
export type BacklogItem = { text?: string; section?: string; file?: string };
export type ParkedTask = { id?: string; title?: string; file?: string; line_number?: number; raw?: string; reason?: string };

export type Workflow = {
  path: string;
  project_key?: string | null;
  tracker_kind?: string | null;
  error?: string | null;
  open_tasks?: OpenTask[];
  active_runs?: Run[];
  awaiting_review_runs?: Run[];
  recent_runs?: Run[];
  backlog_items?: BacklogItem[];
  parked_tasks?: ParkedTask[];
  /** A Linear team's workflow states, in board order (CMX-23); absent for other trackers. */
  tracker_columns?: unknown;
  /** The server cut `recent_runs` at its cap (CMX-193). */
  recent_runs_capped?: boolean;
};

/** `chela dispatch --pause` (CMX-206): null/absent when dispatch runs. */
export type DispatchHold = { summary?: string } | null;

/** CMX-252: what a `chela dispatch --pause --all` stopped (chela.fleet.snapshot). */
export type FleetPause = {
  since_ago?: string | null;
  by?: string;
  expires_in?: string | null;
  agents?: { task: string; wid?: string | null; state?: string | null; finishing?: string | null }[];
  judges?: { task: string; frozen_for?: string | null; processes?: number }[];
} | null;

export type WorkPayload = { configured?: boolean; workflows?: Workflow[]; dispatch_hold?: DispatchHold; fleet_pause?: FleetPause };

/** null until the first poll lands. */
export const work = createStore<WorkPayload | null>(null);

let poll: () => Promise<void> = () => Promise.resolve();

/** features/work registers its poll here at import. */
export function setWorkPoll(fn: () => Promise<void>) {
  poll = fn;
}

/** Refetch /api/dispatcher now; resolves once it has been published. */
export function refreshWork(): Promise<void> {
  return poll();
}
