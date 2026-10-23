// CMX-278: what the sidebar's state modules share: the page's bridge (looked up at call time,
// so a page whose islands did not load publishes nothing), its JSON POST, and this browser's
// storage, every read and write wrapped (a private window or blocked site data must leave a
// sidebar that still renders, just one that forgets).
import { api } from '../../../shared/api/index.ts';
import type { Bridge } from '../../../shared/bridge/index.ts';

export function publish<K extends keyof Bridge>(name: K, value: ReturnType<Bridge[K]['get']>): void {
  (window.chelaBridge?.[name] as { set(v: unknown): void } | null | undefined)?.set(value);
}

export function bridgeValue<K extends keyof Bridge>(name: K): ReturnType<Bridge[K]['get']> | undefined {
  return window.chelaBridge?.[name]?.get() as ReturnType<Bridge[K]['get']> | undefined;
}

export function postJson(path: string, body: unknown): Promise<unknown> {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

export const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

/** `mem` stands in ONLY while storage throws; when storage works it is the whole truth. */
export function lsRead(key: string, mem: string | null): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return mem;
  }
}

export function lsJson(key: string, mem: unknown = null): unknown {
  try {
    return JSON.parse(lsRead(key, mem == null ? null : JSON.stringify(mem)) || 'null');
  } catch {
    return null;
  }
}

export function lsSet(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value));
  } catch { /* storage unavailable: the in-memory copy carries it for this session */ }
}

export function lsRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch { /* storage blocked */ }
}

/** An alert, where the page has one (a page without it fails quietly, never throws). */
export function say(msg: string): void {
  if (typeof alert === 'function') alert(msg);
}

/** Say what the server refused, once: a refusal is a decision the user should see. */
export function sayRefused(res: unknown): void {
  const r = isObj(res) && Array.isArray(res.refused) ? (res.refused as { error?: string; key?: string }[]) : [];
  if (r.length) say(r.map(x => x.error || x.key).join('\n'));
}
