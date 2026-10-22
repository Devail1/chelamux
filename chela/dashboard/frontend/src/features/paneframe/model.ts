// CMX-246 (CMX-212 P31): the pane frame's pure rules. No DOM, no fetch. From terminals.js.

/** A pane's terminal path, the clean URL a joiner reuses verbatim (never carries a cid). */
export function termSrc(wid: string): string {
  return '/term/' + encodeURIComponent(wid) + '/';
}

/** A per-connection id (CMX-175). */
export const newCid = (): string =>
  typeof globalThis.crypto?.randomUUID === 'function' ? globalThis.crypto.randomUUID() : 'c' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);

/** The iframe's src: the clean path plus its `cid`. ttyd's client builds its WebSocket URL from
 * `location.search` verbatim, so the cid reaches /term/<wid>/ws and lets app.py's term_ws match
 * THIS frame's connection to its own tmux client (the ignore-size watch reporting). */
export function frameSrc(wid: string, cid: string): string {
  return termSrc(wid) + '?cid=' + cid;
}

/** /term/<wid>/... → wid, or null. */
export function widOfSrc(src: string): string | null {
  const m = /\/term\/([^/]+)\//.exec(src);
  return m?.[1] ? decodeURIComponent(m[1]) : null;
}

/** ...?cid=<cid> → cid, or null. */
export function cidOfSrc(src: string): string | null {
  const m = /[?&]cid=([^&]+)/.exec(src);
  return m?.[1] ? decodeURIComponent(m[1]) : null;
}

/** text/uri-list: the first line that is neither blank nor a '#' comment (RFC 2483). */
export function uriListToText(list: string): string {
  return String(list).split(/\r?\n/).map((l) => l.trim()).find((l) => l && !l.startsWith('#')) ?? '';
}

/** Ctrl/⌘+K, matched as the page's own palette key is, so a pane's keydown can open it. */
export function isPaletteKey(e: Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey' | 'key'>): boolean {
  return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === 'k' || e.key === 'K');
}
