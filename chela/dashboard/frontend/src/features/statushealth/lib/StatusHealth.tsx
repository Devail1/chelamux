// NATIVE STATUS FEED HEALTH MARKER (CMX-179): the #status-health-warn pill in .safety-float.
// Ported from static/js/statushealth.js by CMX-219 (CMX-212 P06).
//
// `claude agents --json` is the ONE authority for every pane's busy/idle status. It died
// silently for 12 days: every call failed, the cache stayed empty, and every pane kept
// rendering "idle", indistinguishable from a calm fleet. This shows a marker the moment the
// feed itself is not answering. A plain poll on the refresh loop: nothing can push the feed's
// own health (nothing is listening while it is down).
import { api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';

/** The last /api/agents/status_health answer; undefined until the first one lands. */
export const statusHealth = createStore<unknown>(undefined);

/** Never throws out: a failure to reach the dashboard's OWN API keeps the last answer. */
export async function tickStatusHealth(): Promise<void> {
  try {
    const health = await api('/api/agents/status_health');
    // null is a real answer (a malformed reply), unlike "not answered yet"
    statusHealth.set(health ?? null);
  } catch (e) {
    console.error('tickStatusHealth', e);
  }
}

/** Shown on any answered health that is not `ok: true`: a missing or malformed answer is
 * itself evidence something upstream is wrong (fail closed). Glyph + word, never colour only. */
export function StatusHealth() {
  const health = useStore(statusHealth);
  const ok = health === undefined || (typeof health === 'object' && health !== null && (health as { ok?: unknown }).ok === true);
  return (
    <span className="status-health-warn" id="status-health-warn" hidden={ok}
      title="claude agents --json is not answering. Every pane's busy/idle status is stale fleet-wide, not a calm fleet">
      <icons.warn size={14} aria-hidden /><span className="shw-text">agent status unavailable</span><span className="shw-text-narrow">status down</span>
    </span>
  );
}

registerLane('statusHealth', () => tickStatusHealth());
