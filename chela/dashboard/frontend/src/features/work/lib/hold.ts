// CMX-206 / CMX-252: Pause and Resume dispatch, and Pause all agents, from the dashboard. One
// hook for the desktop toolbar's buttons and the phone's ⋯ sheet, so both post the same calls.
import { useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { refreshWork, type DispatchHold, type FleetPause } from '../../../shared/store/work.ts';

export const PAUSE_DISPATCH_ASK = 'Pause dispatch? No new task will be claimed until you resume.';
export const PAUSE_ALL_ASK = 'Pause every dispatched agent? Dispatch is held, each agent is told to start nothing until you resume, and running judges are frozen.';

export type Hold = {
  busy: boolean;
  /** A hold, or a fleet pause outliving its hold (lapsed, released elsewhere): resumable. */
  paused: boolean;
  /** What the toggle reads. */
  label: string;
  /** Pause asks first (`confirm`); Resume releases unconditionally, as the CLI does. */
  toggle: (confirm: (question: string) => boolean) => void;
  /** `--pause --all`; the caller has already asked. */
  pauseAll: () => void;
};

/** Acts on the payload the control was drawn from, so what it shows is what it does. */
export function useDispatchHold(hold: DispatchHold, fleet: FleetPause): Hold {
  const [busy, setBusy] = useState(false);
  async function post(path: string, body?: object) {
    setBusy(true);
    try {
      const opts: RequestInit = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { method: 'POST' };
      const resp = (await api(path, opts)) as { ok?: boolean; error?: string } | null;
      if (!resp || resp.ok === false) window.alert(resp?.error || 'That failed. Dispatch state is unchanged.');
    } catch {
      window.alert('Request failed. Dispatch state is unchanged.');
    } finally {
      setBusy(false);
    }
    await refreshWork();
  }
  const paused = Boolean(hold || fleet);
  return {
    busy,
    paused,
    label: fleet ? 'Resume agents and dispatch' : hold ? 'Resume dispatch' : 'Pause dispatch',
    toggle(confirm) {
      if (!paused && !confirm(PAUSE_DISPATCH_ASK)) return;
      void post(paused ? '/api/dispatcher/resume' : '/api/dispatcher/pause');
    },
    pauseAll: () => void post('/api/dispatcher/pause', { all: true }),
  };
}
