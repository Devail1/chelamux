// CMX-240: the UNSANDBOXED override's confirmation (CMX-403/419), shared by minting a share
// (ShareDialog) and upgrading a live one (SharesSheet): how long it lasts, and the window name
// typed to arm it, typed AGAIN past 4 h.
import { FieldRow, Select, TextInput } from '../../../shared/ui/index.ts';
import { durLabel, isLong, type ShareDraft, type ShareOffer } from './model.ts';

export type ConfirmDraft = Omit<ShareDraft, 'mode'>;

export function UnsafeConfirm({ offer, draft, onChange, className }: { offer: ShareOffer; draft: ConfirmDraft; onChange: (d: ConfirmDraft) => void; className: string }) {
  const set = (patch: Partial<ConfirmDraft>) => onChange({ ...draft, ...patch });
  return (
    <div className={className}>
      <FieldRow label="Lasts">
        <Select label="How long full access lasts" value={String(draft.minutes)} onChange={(v) => set({ minutes: Number(v) })} options={offer.choices.map((n) => ({ value: String(n), label: durLabel(n) }))} />
      </FieldRow>
      <FieldRow label={`Type ${offer.name} to confirm`}>
        <TextInput plain value={draft.confirm} onChange={(v) => set({ confirm: v })} placeholder={offer.name} />
      </FieldRow>
      {isLong(offer, draft.minutes) && (
        <FieldRow label={`Longer than 4 h: type ${offer.name} again`}>
          <TextInput plain value={draft.confirmLong} onChange={(v) => set({ confirmLong: v })} placeholder={offer.name} />
        </FieldRow>
      )}
    </div>
  );
}
