// CMX-240 (CMX-212 P25): what changes a share from this page: adopt-or-mint, Stop (the kill
// switch), a live mode change, and opening the Active shares sheet. The pane itself (its title,
// its ttyd iframe) is still legacy, so terminals.js hands those in through configureShareHost.
import { api, BOOT } from '../../../shared/api/index.ts';
import { createStore } from '../../../shared/store/index.ts';
import type { MintResponse } from './api.ts';
import { openShareDialog } from './mount.tsx';
import { ownerPresence, startOwnerPresence, stopOwnerPresence, syncOwnerPresence } from './presence.ts';
import { applyShared, dropShare, isShared, markShared, noteShare, seedFromAgents, type ShareAgent, type SharedMap } from './state.ts';
import type { ShareInfo } from './ShareInfoRows.tsx';

export type ShareHost = {
  /** The pane's display title. */
  paneTitle: (wid: string) => string;
  /** The pane's live ttyd iframes (the wall and single mode may both hold one). */
  paneFrames: (wid: string) => HTMLIFrameElement[];
};

let host: ShareHost = { paneTitle: (wid) => wid, paneFrames: () => [] };

export function configureShareHost(h: ShareHost) {
  host = h;
}

export const paneTitle = (wid: string) => host.paneTitle(wid);

/** Re-serve the pane's ttyd page, so term_http injects the new "shared" flag. */
function reloadPane(wid: string) {
  for (const f of host.paneFrames(wid)) {
    try {
      f.contentWindow?.location.reload();
    } catch {
      f.src = f.getAttribute('src') ?? ''; // a cross-frame refusal: reload by src
    }
  }
}

/** The pane's live terminal size, captured at share time so joiners pin to the host's grid. */
function paneDims(wid: string): { cols: number; rows: number } | null {
  let dims: { cols: number; rows: number } | null = null;
  for (const f of host.paneFrames(wid)) {
    try {
      const t = (f.contentWindow as (Window & { term?: { cols?: number; rows?: number } }) | null)?.term;
      if (t?.cols && t.rows) dims = { cols: t.cols, rows: t.rows };
    } catch {
      // not ready, or cross-frame: the server falls back to the live window size
    }
  }
  return dims;
}

const termPath = (wid: string) => `/api/term/${encodeURIComponent(wid)}`;
const post = (path: string, body: unknown) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** Owner-only: the share's join link + pairing code. An unreadable answer is `{}`. */
export async function fetchShareInfo(wid: string): Promise<ShareInfo & { share_epoch?: number }> {
  try {
    const i = await api(`${termPath(wid)}/share-info`);
    return i && typeof i === 'object' ? (i as ShareInfo) : {};
  } catch {
    return {};
  }
}

/** Adopt the server's truth (/api/term/shared). An unreadable answer changes nothing. */
export async function reconcileShares(): Promise<void> {
  try {
    const s = await api('/api/term/shared');
    if (s && typeof s === 'object') applyShared(s as SharedMap, true);
  } catch {
    // offline or transient: keep the best-effort list
  }
}

/** The agents poll: each row's share flag and mode, then owner presence follows it. */
export function followAgents(agents: readonly ShareAgent[]) {
  seedFromAgents(agents);
  syncOwnerPresence(agents);
}

/**
 * Stop one share: the link dies and the code rotates. Resolves true once the server no longer
 * shares `wid`. A refused or failed Stop fails CLOSED: the share stays listed unless
 * /api/term/shared says it is gone, so the kill list never drops a live share.
 */
export async function stopShare(wid: string): Promise<boolean> {
  let ok = false;
  try {
    ok = ((await post(`${termPath(wid)}/share`, { on: false })) as { ok?: unknown } | null)?.ok === true;
  } catch {
    ok = false;
  }
  if (!ok) {
    await reconcileShares();
    if (isShared(wid)) return false;
  }
  dropShare(wid);
  stopOwnerPresence(wid);
  reloadPane(wid);
  return true;
}

/** Stop every listed share; true when all of them stopped. */
export async function stopAllShares(wids: readonly string[]): Promise<boolean> {
  const done = await Promise.all(wids.map((w) => stopShare(w)));
  return done.every(Boolean);
}

/** Change a LIVE share's mode (CMX-421): same link, same code, guests stay joined. */
export async function setShareMode(wid: string, body: Record<string, unknown>): Promise<{ ok: true } | { ok: false; error: string }> {
  let resp: { ok?: unknown; mode?: unknown; expires_at?: number | null; error?: unknown } | null;
  try {
    resp = (await post(`${termPath(wid)}/share-mode`, body)) as typeof resp;
  } catch {
    resp = null;
  }
  if (resp?.ok !== true) return { ok: false, error: typeof resp?.error === 'string' && resp.error ? resp.error : 'Could not change the mode' };
  // The server's answer is the truth, not what was asked.
  noteShare(wid, resp.mode ?? body.mode, resp.expires_at);
  return { ok: true };
}

/** Which sheet is open, and the share it points at (CMX-421); `seq` makes each open a fresh read. */
export type SheetTarget = { focus: string | null; seq: number } | null;
export const sheet = createStore<SheetTarget>(null);
let seq = 0;

/** Open the Active shares sheet, then reconcile the list against the server. */
export function openSharesSheet(focusWid?: string) {
  sheet.set({ focus: focusWid ?? null, seq: ++seq });
  void reconcileShares();
}

export function closeSharesSheet() {
  sheet.set(null);
}

/** A mint the server accepted: the share, its owner presence, the pane, then the sheet. */
function onShareMinted(wid: string, resp: MintResponse) {
  noteShare(wid, resp.mode, resp.expires_at);
  startOwnerPresence(wid, resp.join_url, resp.pairing_code, resp.share_epoch);
  reloadPane(wid);
  openSharesSheet();
}

/**
 * The pane's Share. ADOPT-FIRST: a share lives on the server and survives a reload, so ask
 * the server before minting and never re-mint a live one (that would rotate the code and orphan
 * the bridge). An existing share opens the sheet pointed at it, with its mode control.
 */
export async function shareSession(wid: string): Promise<void> {
  const info = await fetchShareInfo(wid);
  if (info.pairing_code) {
    markShared(wid);
    startOwnerPresence(wid, info.join_url, info.pairing_code, info.share_epoch);
    openSharesSheet(wid);
    return;
  }
  await openShareDialog({ wid, title: host.paneTitle(wid), dims: () => paneDims(wid), onMinted: (resp) => onShareMinted(wid, resp) });
}

/** Page load: light the pill for shares already live (a forgotten one included), and wire
 * owner presence so a shared pane's shim 'ready' is caught. Only where the Wall exists. */
export function startShares() {
  if (!BOOT.terminalsEnabled) return;
  void ownerPresence();
  void api('/api/term/shared').then(
    (s) => s && typeof s === 'object' && applyShared(s as SharedMap, false),
    () => {},
  );
}
