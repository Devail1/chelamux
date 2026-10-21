// THE /api/dispatcher poll (static/js/work.js until CMX-233). One fetch feeds every consumer:
// the `work` store (the board, the Runs tables, the New-task form) and the sidebar's Work
// badges, which are on screen from every view. So it is an app-level scheduler lane, not a
// view's timer, and an SSE `runs` frame asks for one more read. Schedules keeps its own
// endpoint, read only while its segment shows (WorkView).
import { api } from '../../../shared/api/index.ts';
import { onEvent } from '../../../shared/events/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { setWorkPoll, work, type WorkPayload } from '../../../shared/store/work.ts';
import { RUN_TOAST_KINDS, runToastKind } from '../../../../../static/js/runtoast';
import { workBadgeCounts } from '../board.ts';
import { showToast } from './toasts.ts';

// The badges live in the legacy sidebar's view list (nav.js renders them from views.js).
function renderBadges(data: WorkPayload | null) {
  const { runs, prs } = workBadgeCounts(data);
  const runsEl = document.getElementById('side-runs-count');
  const prEl = document.getElementById('side-pr-count');
  if (runsEl) runsEl.textContent = String(runs);
  if (prEl) prEl.textContent = `${prs} PR`;
}

/** Fetch once and publish. Never throws: a failed read keeps the last payload (the next tick retries). */
export async function pollWork(): Promise<void> {
  let data: WorkPayload | null;
  try {
    data = (await api('/api/dispatcher')) as WorkPayload | null;
  } catch (e) {
    console.error('pollWork', e);
    return;
  }
  work.set(data);
  renderBadges(data);
}

// --- Run-state toasts, off the SSE `runs` frame ---------------------------------------------
// Edge-triggered per run (runtoast.js): a toast fires only on the transition INTO a review
// state, never on a frame that merely carries an unrelated pr_state change. The `hello`
// baseline primes the last-seen status, so a run already in review on (re)connect is not
// toasted again.
type RunFrame = { runs?: ({ task_id?: string; status?: string; label?: string; pr_url?: string } | null)[] } | null;
const lastStatus = new Map<string, string | undefined>();

function muted(): boolean {
  try {
    return localStorage.getItem('chela_mute_run_toasts') === '1';
  } catch {
    return false;
  }
}

function prime(d: RunFrame) {
  for (const r of d?.runs || []) if (r?.task_id) lastStatus.set(r.task_id, r.status);
}

function runToasts(d: RunFrame) {
  for (const r of d?.runs || []) {
    if (!r?.task_id) continue;
    const prev = lastStatus.get(r.task_id);
    lastStatus.set(r.task_id, r.status);
    const kind = runToastKind(prev, r.status) as keyof typeof RUN_TOAST_KINDS | null;
    if (!kind || muted()) continue;
    showToast(`${r.label || r.task_id} → ${RUN_TOAST_KINDS[kind].text}`, r.pr_url || undefined);
  }
}

setWorkPoll(pollWork);
registerLane('work', () => pollWork());
onEvent('hello', (d) => prime(d as RunFrame));
onEvent('runs', (d) => {
  runToasts(d as RunFrame);
  void pollWork();
});
