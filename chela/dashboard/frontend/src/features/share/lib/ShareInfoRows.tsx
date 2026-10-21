// CMX-239: one share's join link and pairing code, each readonly with a Copy button. They come
// from /api/term/<wid>/share-info: /api/term/shared deliberately omits them (owner-only secrets,
// see api_term_shared). The Active shares sheet (still legacy until P25) mounts it per row.
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
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

const roots = new WeakMap<Element, Root>();

/** Render the rows into a legacy element, synchronously (legacy reads them straight back). */
export function renderShareInfoRows(el: Element, info: ShareInfo | null, emptyMsg: string): void {
  let r = roots.get(el);
  if (!r) {
    r = createRoot(el);
    roots.set(el, r);
  }
  const root = r;
  flushSync(() => root.render(<ShareInfoRows info={info} emptyMsg={emptyMsg} />));
}
