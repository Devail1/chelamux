// CMX-240 (CMX-212 P25): the owner-presence parent client (static/collab/presence-owner.js),
// loaded lazily from its versioned static URL and wired once. It alone holds the pairing secret
// and the crypto; the iframe shim never does. A failed load is retried on the next call.
import { staticUrl } from '../../../shared/api/index.ts';
import type * as OwnerPresence from '../../../../../static/collab/presence-owner.js';
import type { ShareAgent } from './state.ts';

type Module = typeof OwnerPresence;

let loading: Promise<Module | null> | null = null;

export function ownerPresence(): Promise<Module | null> {
  loading ??= (import(staticUrl('collab/presence-owner.js')) as Promise<Module>).then(
    (m) => {
      try {
        m.initOwnerPresence();
      } catch {
        // the router is best effort: presence is a nicety, never a gate
      }
      return m;
    },
    (e: unknown) => {
      console.warn('[chela] owner-presence load failed', e);
      loading = null;
      return null;
    },
  );
  return loading;
}

export function startOwnerPresence(wid: string, joinUrl: string | undefined, code: string | undefined, epoch: number | null | undefined) {
  void ownerPresence().then((m) => m?.startOwnerPresence(wid, joinUrl, code, epoch));
}

export function stopOwnerPresence(wid: string) {
  void ownerPresence().then((m) => m?.stopOwnerPresence(wid));
}

/**
 * Re-key (or drop) owner presence when a share was stopped or re-created, possibly from
 * ANOTHER page (CMX-427). Nothing loads it for this: before the module is loaded there is no
 * session to follow, and it is a no-op for an unchanged share_epoch.
 */
export function syncOwnerPresence(agents: readonly ShareAgent[]) {
  void loading?.then((m) => {
    for (const a of agents) if (m && a.window_id) void m.syncOwnerPresence(a.window_id, !!a.shared, a.share_epoch);
  });
}
