// CMX-239: the share dialog's two requests. Both fail CLOSED: an unreadable options read
// offers view only, and a mint that throws or answers without `ok` is an error, never a share.
import { api } from '../../../shared/api/index.ts';
import type { ShareOptions } from './model.ts';

/** What POST /api/term/<wid>/share answers on success. */
export type MintResponse = {
  ok: true;
  mode?: string;
  join_url?: string;
  pairing_code?: string;
  share_epoch?: number;
  expires_at?: number | null;
};

export type MintResult = { ok: true; resp: MintResponse } | { ok: false; error: string };

const termPath = (wid: string) => `/api/term/${encodeURIComponent(wid)}`;

export async function fetchShareOptions(wid: string): Promise<ShareOptions> {
  try {
    const o = await api(`${termPath(wid)}/share-options`);
    return o && typeof o === 'object' ? (o as ShareOptions) : {};
  } catch {
    return {};
  }
}

export async function mintShare(wid: string, body: Record<string, unknown>): Promise<MintResult> {
  let resp: { ok?: unknown; error?: unknown } | null;
  try {
    resp = (await api(`${termPath(wid)}/share`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })) as typeof resp;
  } catch {
    return { ok: false, error: 'Share failed' };
  }
  if (resp?.ok !== true) return { ok: false, error: typeof resp?.error === 'string' && resp.error ? resp.error : 'Share failed' };
  return { ok: true, resp: resp as MintResponse };
}
