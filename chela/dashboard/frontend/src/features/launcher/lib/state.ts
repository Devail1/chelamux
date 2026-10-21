// Launcher: Recent + Favorites click-to-launch (CMX-212 P20, from static/js/launcher.js and
// nav.js's new-session actions, by CMX-219).
//
// One tap launches a Claude agent in a project directory. Recent fills itself from past
// launches (server-side MRU); Favorites are pinned. State lives server-side (/api/launcher),
// so every device shows the same lists. A launch into a dir where a live Claude already runs
// focuses that pane instead of spawning a twin.
import { BASE_PATH, BOOT, api } from '../../../shared/api/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { createStore } from '../../../shared/store/index.ts';

export type LaunchEntry = { path: string; label: string; exists?: boolean };
export type LauncherData = { recent: LaunchEntry[]; favorites: LaunchEntry[] };

export const launcher = createStore<LauncherData>({ recent: [], favorites: [] });

/** What a launch does to the legacy page, which owns the Wall and the sidebar. nav.js sets it. */
export type LauncherHost = {
  focusPaneByWid(wid: string): void;
  selectView(view: string): void;
  refreshSidebar(): unknown;
};
let host: LauncherHost = { focusPaneByWid() {}, selectView() {}, refreshSidebar() {} };
export function setLauncherHost(h: LauncherHost) {
  host = h;
}

export async function refreshLauncher(): Promise<void> {
  if (!BOOT.terminalsEnabled) return;
  try {
    const d = (await api('/api/launcher')) as Partial<LauncherData> | null;
    if (d) launcher.set({ recent: d.recent || [], favorites: d.favorites || [] });
  } catch {
    /* transient: keep the last lists */
  }
}

/** Two filesystem paths, ignoring a trailing slash (the server realpaths both sides). */
export function samePath(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const strip = (s: string) => s.replace(/\/+$/, '');
  return strip(a) === strip(b);
}

async function post(path: string, body?: object): Promise<{ ok?: boolean; error?: string } & Partial<LauncherData>> {
  const res = await fetch(BASE_PATH + path, {
    method: 'POST',
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  return ((await res.json()) ?? {}) as { ok?: boolean; error?: string } & Partial<LauncherData>;
}

// A new window: refetch /api/agents so it shows, and surface the Wall it spins up on.
function landed(wall: boolean) {
  agents.set([]);
  if (wall) host.selectView('terminals');
  void host.refreshSidebar();
  void refreshLauncher();
}

/**
 * Launch `claude` (or a plain shell, `shell`) in `path`. A Claude launch into a dir that
 * already has a live Claude focuses that pane; `fresh` (a sidebar group's "+", CMX-35) skips
 * that: it asks for ANOTHER session there. Plain shells always spawn.
 */
export async function launchProject(path: string, opts: { shell?: boolean; fresh?: boolean } = {}): Promise<void> {
  if (!opts.shell && !opts.fresh) {
    const existing = agents.get().find((a) => a.claude_running && samePath(a.cwd as string, path));
    if (existing?.window_id) return host.focusPaneByWid(existing.window_id);
  }
  try {
    const res = await post('/api/agents/spawn', opts.shell ? { cwd: path } : { cwd: path, command: 'claude' });
    if (!res.ok) return window.alert(`Launch failed: ${res.error || 'unknown error'}`);
    landed(true);
  } catch (e) {
    window.alert(`Launch failed: ${String(e)}`);
  }
}

async function edit(route: string, path: string) {
  try {
    const d = await post(route, { path });
    if (d.ok) launcher.set({ recent: d.recent || [], favorites: d.favorites || [] });
  } catch {
    /* transient: keep the last lists */
  }
}
export const pinFav = (path: string) => edit('/api/launcher/pin', path);
export const unpinFav = (path: string) => edit('/api/launcher/unpin', path);
export const forgetRecent = (path: string) => edit('/api/launcher/forget', path);

/**
 * A plain shell window. The spawn endpoint is behind the terminals flag (it was the Wall's
 * spawner): say what it answered rather than fail silently.
 */
export async function newShellWindow(): Promise<void> {
  const fallback = 'Could not spawn a window (enable terminals, or use tmux directly).';
  try {
    const res = await post('/api/agents/spawn');
    if (res.ok) landed(false);
    else window.alert(res.error || fallback);
  } catch {
    window.alert(fallback);
  }
}

/**
 * New session → Sandboxed (CMX-403): a window running the container launcher, the only kind a
 * share guest may type into. Asks for the project directory, pre-filled with the most recent
 * launch target. `web` (CMX-418) opts THIS session into web access (public hosts only, through
 * the filtering egress proxy), confirmed first.
 */
export async function newSandboxedSession(web = false): Promise<void> {
  const { favorites, recent } = launcher.get();
  const guess = [...favorites, ...recent][0]?.path ?? '';
  const label = web ? 'Sandboxed session with WEB ACCESS' : 'Sandboxed session';
  const cwd = (window.prompt(`${label}: project directory (the guest sees ONLY this):`, guess) || '').trim();
  if (!cwd) return;
  if (web && !window.confirm('Allow web access for this session?\n\n'
      + 'It can fetch any PUBLIC web page (never your host or LAN), rate-limited, and every '
      + 'request is logged. A web page can also make it SEND workspace contents to a public '
      + `site, so keep only the guest's own material in ${cwd}.`)) return;
  try {
    const res = await post('/api/agents/spawn-sandboxed', web ? { cwd, web: true } : { cwd });
    if (!res.ok) return window.alert(`Sandboxed session refused: ${res.error || 'unknown error'}`);
    landed(true);
  } catch (e) {
    window.alert(`Sandboxed session failed: ${String(e)}`);
  }
}

registerLane('launcher', () => refreshLauncher());
