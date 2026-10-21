// CMX-240: a fake share server for the sheet, pill and action tests: the endpoints the Active
// shares sheet reads and writes, with the state a test sets and every POST body recorded.
import { act } from '@testing-library/react';
import { closeSharesSheet, shares, type ShareMode, type ShareOptions } from '../index.ts';
import { stubFetch } from './setup.ts';

export type Live = { mode: ShareMode; expires_at?: number | null };

export type Server = {
  /** What /api/term/shared lists (the server's truth); `throw` makes the read fail. */
  shared: Record<string, Live> | 'throw';
  options: ShareOptions;
  info: { join_url?: string; pairing_code?: string; share_epoch?: number } | 'throw';
  /** POST /share-mode answers {ok:false, error: refuse}. */
  refuse?: string;
  /** The mode the server reports back after a change (it is the truth). */
  serverMode?: ShareMode;
  /** The expiry a change to UNSANDBOXED answers with. */
  grantExpires?: number;
  /** POST /share {on:false}: 'ok' (default), 'refuse' ({ok:false}) or 'throw'. */
  stop?: 'ok' | 'refuse' | 'throw';
  /** Never answer the per-row reads (share-info, share-options). */
  hangRows?: boolean;
  modePosts: Record<string, unknown>[];
  stopPosts: string[];
  mintPosts: Record<string, unknown>[];
};

export const INFO = { join_url: 'https://relay.test/j/abc', pairing_code: 'PAIR1' };

export function serve(over: Partial<Server> = {}): Server {
  const s: Server = { shared: { '@1': { mode: 'view' } }, options: {}, info: INFO, modePosts: [], stopPosts: [], mintPosts: [], ...over };
  const hang = new Promise(() => {});
  stubFetch((url, init) => {
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    const wid = decodeURIComponent(/\/api\/term\/([^/]+)\//.exec(url)?.[1] ?? '');
    if (url.endsWith('/api/term/shared')) {
      if (s.shared === 'throw') throw new Error('offline');
      return s.shared;
    }
    if (url.endsWith('/share-info')) {
      if (s.hangRows) return hang;
      if (s.info === 'throw') throw new Error('offline');
      return s.info;
    }
    if (url.endsWith('/share-options')) return s.hangRows ? hang : s.options;
    if (url.endsWith('/share-mode')) {
      s.modePosts.push(body);
      if (s.refuse) return { ok: false, error: s.refuse };
      const mode = s.serverMode ?? (body.mode as ShareMode);
      const expires_at = mode === 'unsandboxed' ? (s.grantExpires ?? 1e9) : null;
      if (s.shared !== 'throw') s.shared = { ...s.shared, [wid]: { mode, expires_at } };
      return { ok: true, shared: true, mode, expires_at };
    }
    if (url.endsWith('/share') && body.on === false) {
      s.stopPosts.push(wid);
      if (s.stop === 'throw') throw new Error('offline');
      if (s.stop === 'refuse') return { ok: false, error: 'nope' };
      if (s.shared !== 'throw') s.shared = Object.fromEntries(Object.entries(s.shared).filter(([w]) => w !== wid));
      return { ok: true, shared: false };
    }
    if (url.endsWith('/share')) {
      s.mintPosts.push(body);
      return { ok: true, shared: true, ...INFO, mode: body.mode, expires_at: null };
    }
    return {};
  });
  return s;
}

/** Let every pending fetch chain and React commit land. */
export async function settle() {
  await act(async () => {
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
  });
}

export function resetShares() {
  closeSharesSheet();
  shares.set(new Map());
}
