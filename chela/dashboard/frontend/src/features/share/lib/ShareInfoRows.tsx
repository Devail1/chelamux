// CMX-239: one share's join link and pairing code, each readonly with a Copy button. They come
// from /api/term/<wid>/share-info: /api/term/shared deliberately omits them (owner-only secrets,
// see api_term_shared). The Active shares sheet renders one per row.
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
