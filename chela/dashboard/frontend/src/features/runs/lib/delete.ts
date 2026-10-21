// The delete action behind both confirm UIs: the runs table's row and the board card's ×
// (kanban.js). Moved from work.js by CMX-231.
import { BASE_PATH } from '../../../shared/api/index.ts';
import { refreshWork } from '../../../shared/store/work.ts';

export type DeletePayload = { kind: 'run'; task_id: string } | { kind: 'source-line'; file: string; text: string };

/** POST the delete; resolves to an error string, or null on success (then refreshes Work). */
export async function postWorkDelete(payload: DeletePayload): Promise<string | null> {
  let resp: Response;
  let data: { ok?: boolean; error?: string } = {};
  try {
    resp = await fetch(`${BASE_PATH}/api/dispatcher/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
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
  // Idempotent on the server ("already gone" = "just deleted"): the redraw is the confirmation.
  void refreshWork();
  return null;
}
