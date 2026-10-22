// CMX-221 (CMX-212 P15): the Settings modal's state: open or closed, the tab, the search query,
// and the /api/config payload the tabs read (one GET per open, not one per section).
import { api } from '../../../shared/api/index.ts';
import { createStore } from '../../../shared/store/index.ts';
import { isTab, type Config, type SettingsTab } from '../model.ts';

type State = {
  open: boolean;
  /** The tab shown; Settings reopens on the last one, General on first open. */
  tab: SettingsTab;
  query: string;
  /** Bumped on every open, so each tab re-reads its endpoint. */
  epoch: number;
};

export const settings = createStore<State>({ open: false, tab: 'general', query: '', epoch: 0 });
const patch = (p: Partial<State>) => settings.set({ ...settings.get(), ...p });

/** The last /api/config read or POST reply; null until it lands, `{error}` when it failed. */
export const config = createStore<Config | null>(null);

export async function loadConfig(): Promise<void> {
  try {
    const cfg = (await api('/api/config')) as Config | null;
    config.set(cfg && typeof cfg === 'object' ? cfg : { error: 'unavailable' });
  } catch {
    config.set({ error: 'unavailable' });
  }
}

/**
 * POST `body` to /api/config. Resolves to the reply, or null when it failed or the server
 * refused it (api() resolves on a 4xx: the refusal arrives as a body). A good reply becomes
 * the page's config; a bad one re-reads what IS stored, so nothing shows a value not saved.
 */
export async function postConfig(body: Record<string, unknown>): Promise<{ cfg: Config | null; failed: 'request' | 'rejected' | null }> {
  let cfg: Config | null;
  try {
    cfg = (await api('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })) as Config | null;
  } catch {
    void loadConfig();
    return { cfg: null, failed: 'request' };
  }
  if (!cfg || cfg.error) {
    void loadConfig();
    return { cfg: null, failed: 'rejected' };
  }
  config.set(cfg);
  return { cfg, failed: null };
}

/** Open (or close) Settings. `'notify'` opens on Notifications; a reopen starts with no query
 * (a stale filter would read as settings having vanished). */
export function toggleSettings(focus?: string): void {
  if (settings.get().open) closeSettings();
  else openSettings(focus);
}

export function openSettings(focus?: string): void {
  const s = settings.get();
  const tab = focus === 'notify' ? 'notifications' : s.tab;
  if (s.open) {
    patch({ tab });
    return;
  }
  patch({ open: true, tab, query: '', epoch: s.epoch + 1 });
  void loadConfig();
}

export function closeSettings(): void {
  patch({ open: false, query: '' });
}

/** Picking a tab mid-search means "take me there": the query is dropped. */
export function selectSettingsTab(tab: string): void {
  if (isTab(tab)) patch({ tab, query: '' });
}

export function setSettingsQuery(query: string): void {
  patch({ query });
}
