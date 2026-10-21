// ---------------------------------------------------------------------------
// ORCHESTRATOR SUBSCRIBE: the pane-title toggle for "receive the decisions
// inbox here", plus the shared state the Decisions inbox's owner chip renders from.
//
// Wraps /api/orchestrator/{subscribe,release,status} (chela/dashboard/app.py),
// which themselves wrap chela.inbox.register/unregister, an ATOMIC take-over
// of the single `orchestrator` slot (chela/inbox.py). Clicking a SECOND pane's
// toggle supersedes the first; the first pane's next status read simply reports
// it is no longer the owner. There is never a moment with two live recipients.
//
// State lives in ONE store (shared/store/orchestrator.ts), not per caller:
// terminals.js (the pane toggle), nav.js (the sidebar row) and the Decisions
// inbox all read it and all react to the SSE `orchestrator` delta (below): one
// fact, rendered three times, never copies that can disagree about who owns the role.
// (static/js/orchestrator.js until CMX-228, CMX-212 P07.)
// ---------------------------------------------------------------------------
import { api } from '../api/index.ts';
import { onEvent } from '../events/index.ts';
import { registerLane } from '../scheduler/index.ts';
import { orchestrator, type OrchestratorStatus } from '../store/orchestrator.ts';

export type { OrchestratorStatus } from '../store/orchestrator.ts';

/** What subscribe/release answer: the new status on success, `error` on a refusal. */
export type OrchestratorResult = Partial<OrchestratorStatus> & { ok: boolean; error?: string };

export function orchestratorState(): OrchestratorStatus {
  return orchestrator.get();
}

// Subscribe to every change of the state. A listener that throws must not stop the others
// from hearing the update.
export function onOrchestratorChange(fn: (s: OrchestratorStatus) => void): () => void {
  return orchestrator.subscribe(() => {
    try {
      fn(orchestrator.get());
    } catch (e) {
      console.error('orchestrator listener', e);
    }
  });
}

function apply(data: unknown): OrchestratorStatus {
  if (data && typeof data === 'object' && !Array.isArray(data)) orchestrator.set(data as OrchestratorStatus);
  return orchestrator.get();
}

export async function refreshOrchestratorStatus(): Promise<OrchestratorStatus> {
  let data: unknown;
  try {
    data = await api('/api/orchestrator/status');
  } catch {
    return orchestrator.get(); // transient: the next poll/SSE delta retries
  }
  return apply(data);
}

async function post(path: string, wid: string | null): Promise<OrchestratorResult> {
  let data: OrchestratorResult | null;
  try {
    data = (await api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wid }),
    })) as OrchestratorResult | null;
  } catch (e) {
    return { ok: false, error: String(e) };
  }
  // A refusal (dead window, not the owner) never overwrites the last-known-good state.
  if (data && data.ok) apply(data);
  return data || { ok: false, error: 'empty response' };
}

export function orchestratorSubscribe(wid: string): Promise<OrchestratorResult> {
  return post('/api/orchestrator/subscribe', wid);
}

export function orchestratorRelease(wid: string | null): Promise<OrchestratorResult> {
  return post('/api/orchestrator/release', wid);
}

// The poll under the SSE deltas: every refresh() tick, whatever view is on screen. Its first
// tick is the boot's seed, so a page that loads after another pane took the role shows it.
registerLane('orchestrator', () => refreshOrchestratorStatus());

// The SSE `orchestrator` frame: who owns the pane-title toggle changed. A NOTIFICATION
// carrying only the new wid, so refetch the full state (state/why/queued), on every view: a
// takeover must repaint the Wall's pane buttons and the decisions chip alike.
onEvent('orchestrator', () => void refreshOrchestratorStatus());
// CMX-139: `hello` opens EVERY connection, reconnects included (a dashboard restart drops the
// stream and the browser re-opens it). The server primes its baseline from the CURRENT pin
// without emitting, so a pin that moved while we were disconnected never arrives as a delta:
// re-fetch here, or the ring stays on the old pane until a reload.
onEvent('hello', () => void refreshOrchestratorStatus());
