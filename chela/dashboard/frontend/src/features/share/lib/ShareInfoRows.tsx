// CMX-239: one share's join link and pairing code, each readonly with a Copy button. They come
// from /api/term/<wid>/share-info: /api/term/shared deliberately omits them (owner-only secrets,
// see api_term_shared). The Active shares sheet (still legacy until P25) mounts it per row.
import { flushSync } from 'react-dom';
import type { Root } from 'react-dom/client';
import { createIslandRoot } from '../../../shared/islands/index.tsx';
import { CopyField, FieldRow } from '../../../shared/ui/index.ts';

export type ShareInfo = { join_url?: string; pairing_code?: string };

export function ShareInfoRows({ info, emptyMsg }: { info: ShareInfo | null; emptyMsg: string }) {
  const url = info?.join_url || '';
  const code = info?.pairing_code || '';
  if (!url && !code) return <div className="tsp-warn">{emptyMsg}</div>;
  return (
    <>
      {url && (
        <FieldRow label="Join link">
          <CopyField value={url} />
        </FieldRow>
      )}
      {code && (
        <FieldRow label="Pairing code" hint="Needed to decrypt">
          <CopyField value={code} />
        </FieldRow>
      )}
    </>
  );
}

const roots = new Map<Element, Root>();

/**
 * Render the rows into a legacy element, synchronously (legacy reads them straight back). The
 * sheet rebuilds its rows on every refresh, so each call first unmounts the roots whose element
 * has left the page.
 */
export function renderShareInfoRows(el: Element, info: ShareInfo | null, emptyMsg: string): void {
  for (const [old, r] of roots)
    if (!old.isConnected) {
      r.unmount();
      roots.delete(old);
    }
  let root = roots.get(el);
  if (!root) {
    root = createIslandRoot(el);
    roots.set(el, root);
  }
  const r = root;
  flushSync(() => r.render(<ShareInfoRows info={info} emptyMsg={emptyMsg} />));
}

/** How many rows are mounted (tests: the sheet's rebuilds must not leak roots). */
export const mountedInfoRows = () => roots.size;
