// CMX-426: "chela was updated, Reload". Ported from static/js/version.js by CMX-219 (CMX-212 P06).
//
// A deploy restarts chela-dashboard, but an open page keeps running the code it loaded. Static
// assets are served under a versioned path, so a RELOAD picks up the new build; this tells an
// open page that one exists. Never reloads by itself: a share or a half-typed draft may be in
// progress. It offers.
//
// CMX-129: the same banner carries "N services run old code, Restart". One banner, never two
// stacked, each fact with its own button.
import { ASSET_VERSION, BASE_PATH, api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Button, IconButton } from '../../../shared/ui/index.ts';
import { followUpdate, staleOffer, type FollowDeps, type StaleOffer, type UpdateStatus } from '../flow.ts';

type Progress = { text: string; cls: '' | 'ok' | 'err'; done?: boolean };
type State = {
  /** The server version a reload is offered for; '' when none. */
  reload: string;
  /** The server version the banner was closed for. */
  dismissed: string;
  stale: StaleOffer | null;
  /** The stale set (names) the banner was closed for. */
  dismissedStale: string;
  /** While a restart is followed, then its outcome. */
  progress: Progress | null;
};

export const banner = createStore<State>({ reload: '', dismissed: '', stale: null, dismissedStale: '', progress: null });
const patch = (p: Partial<State>) => banner.set({ ...banner.get(), ...p });

/** Offer a reload only when BOTH versions are known and they differ. An unknown side never
 * offers (a banner nobody can act on correctly is noise); a match stays silent. */
export function reloadOffered(pageVersion: string, serverVersion: string): boolean {
  if (!pageVersion || !serverVersion) return false;
  return pageVersion !== serverVersion;
}

/** What the banner shows for `s`. */
export function view(s: State) {
  const showReload = !!s.reload && s.reload !== s.dismissed;
  const busy = !!s.progress && !s.progress.done;
  const showStale = !!s.stale && s.stale.key !== s.dismissedStale && !busy;
  const parts: string[] = [];
  if (showReload) parts.push('chela was updated');
  if (s.progress) parts.push(s.progress.text);
  else if (showStale && s.stale) parts.push(s.stale.text);
  return { show: showReload || showStale || !!s.progress, showReload, showStale, text: parts.join(' · ') };
}

/** Set the reload half for this server version. Returns whether it is shown. */
export function applyVersion(serverVersion: string, pageVersion = ASSET_VERSION): boolean {
  patch({ reload: reloadOffered(pageVersion, serverVersion) ? serverVersion : '' });
  return view(banner.get()).showReload;
}

/** The stale half for an /api/update/status payload; unknown offers nothing. */
export function applyStale(status: UpdateStatus | null): boolean {
  patch({ stale: staleOffer(status) });
  return view(banner.get()).showStale;
}

export function dismiss() {
  const s = banner.get();
  patch({ dismissed: s.reload, dismissedStale: s.stale?.key ?? '', progress: s.progress?.done ? null : s.progress });
}

/** Restart the stale services (the one existing route, /api/update/apply), after a confirm. */
export async function restartStale(deps: FollowDeps = {}): Promise<Progress | null> {
  const { stale: offer, progress } = banner.get();
  if (!offer || (progress && !progress.done)) return null;
  if (!window.confirm(`${offer.label}: ${offer.names.join(', ')}? Running agents and judges keep running; the daemon restarts between ticks.`)) return null;
  patch({ progress: { text: 'Starting…', cls: '' } });
  let resp: { ok?: boolean; error?: string; started?: boolean; detail?: string; run?: unknown; boot?: unknown } | null;
  try {
    const res = await fetch(`${BASE_PATH}/api/update/apply`, { method: 'POST' });
    resp = (await res.json()) as typeof resp;
  } catch {
    resp = { ok: false, error: 'request failed, the dashboard may be restarting' };
  }
  const end = (p: Progress) => {
    patch({ progress: p });
    return p;
  };
  if (!resp || resp.ok === false || resp.error) return end({ text: resp?.error || 'Restart refused.', cls: 'err', done: true });
  if (!resp.started) return end({ text: resp.detail || 'Nothing to restart.', cls: 'ok', done: true });
  const step = await followUpdate(resp, (s) => patch({ progress: { text: s.text, cls: '' } }), deps);
  const done = step.state === 'done';
  // a done restart clears the stale offer: the next status read re-decides
  if (done) patch({ stale: null });
  return end({ text: step.text, cls: done ? 'ok' : 'err', done: true });
}

export async function checkStale(): Promise<boolean> {
  let st: UpdateStatus | null = null;
  try {
    st = (await api('/api/update/status')) as UpdateStatus | null;
  } catch {
    st = null;
  }
  return applyStale(st);
}

/** Both reads, independent: a failed one must not hide what the other found. */
export async function checkForUpdate(): Promise<boolean> {
  checkStale().catch(() => {});
  let v = '';
  try {
    v = ((await api('/api/version')) as { version?: string } | null)?.version || '';
  } catch {
    return false;
  }
  return applyVersion(v);
}

export function UpdateBanner() {
  const s = useStore(banner);
  const v = view(s);
  if (!v.show) return null;
  return (
    <div id="update-banner" className="update-banner" role="status">
      <span id="update-banner-msg" className={s.progress?.cls ? `s-savemsg ${s.progress.cls}` : ''}>{v.text}</span>
      {v.showStale && <Button variant="primary" id="update-banner-restart" onClick={() => void restartStale()}>{s.stale?.label}</Button>}
      {v.showReload && <Button variant="primary" id="update-banner-reload" onClick={() => window.location.reload()}>Reload</Button>}
      <IconButton className="icon-btn" id="update-banner-close" label="Dismiss" onClick={dismiss}><icons.close size={14} aria-hidden /></IconButton>
    </div>
  );
}

// CMX-426: a deploy since this page loaded? On boot it waits for the active view's first
// paint: /api/update/status is the slowest boot read (~0.8 s) and only contends with the Wall.
registerLane('update', (t) => (t.boot ? t.viewPainted.then(() => checkForUpdate()) : checkForUpdate()), 'detached');
