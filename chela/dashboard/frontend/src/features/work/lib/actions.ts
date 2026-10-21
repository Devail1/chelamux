// A card's server actions (static/js/kanban.js until CMX-233). Each resolves to the error to
// show on the card, or null; on success the board refetches instead of mutating itself, so the
// next poll is what moves the card.
import { BASE_PATH } from '../../../shared/api/index.ts';
import { refreshWork } from '../../../shared/store/work.ts';

async function post(path: string, body?: unknown): Promise<string | null> {
  let resp: Response;
  let data: { ok?: boolean; error?: string } = {};
  try {
    resp = await fetch(BASE_PATH + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    try {
      data = ((await resp.json()) as typeof data | null) ?? {};
    } catch {
      data = {};
    }
  } catch (e) {
    return String(e);
  }
  if (!resp.ok || !data.ok) return data.error || `HTTP ${resp.status}`;
  void refreshWork();
  return null;
}

const run = (taskId: string) => `/api/dispatcher/runs/${encodeURIComponent(taskId)}`;

/** Squash-merge the card's PR (the server re-reads its checks at merge time). */
export function mergeRun(taskId: string): Promise<string | null> {
  return post(`${run(taskId)}/merge`);
}

/** CMX-68: `chela close <run> --requeue --reason …`. Both card labels REQUEUE: a plain close
 * from here would leave the task stalled again. */
export function requeueRun(taskId: string, reason: string): Promise<string | null> {
  return post(`${run(taskId)}/close`, { reason, requeue: true });
}

/** Move a BACKLOG.md bullet to TODO.md's ## Open (one commit pushed to the base branch). */
export function promoteBacklog(workflowPath: string, text: string): Promise<string | null> {
  return post('/api/dispatcher/backlog/promote', { workflow_path: workflowPath, text });
}
