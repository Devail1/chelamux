// CMX-239 (CMX-212 P24): the share dialog (CMX-403), on the kit's <Dialog>. Picks the share's
// ACCESS before minting it: "View only" (the default); "Allow typing", enabled only when the
// setting is on AND the window verifies LIVE as sandboxed (else the reason is shown); and,
// only while the setting is on and the window is NOT sandboxed, the trusted-peer "Full access,
// UNSANDBOXED" override, armed only by the window name TYPED (twice past 4 h, CMX-419).
import { useState } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { Button, Dialog, RadioGroup, type RadioOption } from '../../../shared/ui/index.ts';
import { mintShare, type MintResponse } from './api.ts';
import { mintBody, shareOffer, type ShareMode, type ShareOptions } from './model.ts';
import { UnsafeConfirm, type ConfirmDraft } from './UnsafeConfirm.tsx';

export type PaneDims = { cols: number; rows: number };

export type ShareDialogProps = {
  wid: string;
  /** The pane's display title. */
  title: string;
  options: ShareOptions;
  /** The pane's live terminal size, read at mint time: joiners pin to the host's grid. */
  dims?: () => PaneDims | null;
  onClose: () => void;
  /** After a mint the server accepted; the dialog has closed. */
  onMinted: (resp: MintResponse) => void;
};

function OptionText({ icon: Icon, title, desc, reason }: { icon: typeof icons.share; title: string; desc: string; reason?: string }) {
  return (
    <>
      <strong>
        <Icon aria-hidden size={14} /> {title}
      </strong>
      <span className="sd-desc">{desc}</span>
      {reason && <span className="sd-reason">{reason}</span>}
    </>
  );
}

export function ShareDialog({ wid, title, options, dims, onClose, onMinted }: ShareDialogProps) {
  const [offer] = useState(() => shareOffer(options));
  const [mode, setMode] = useState<ShareMode>('view');
  const [draft, setDraft] = useState<ConfirmDraft>({ minutes: offer.mins, confirm: '', confirmLong: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const unsafe = mode === 'unsandboxed';
  const body = mintBody(offer, { mode, ...draft });
  // Once Share is pressed the mint is underway: it cannot be cancelled, only awaited.
  const dismiss = () => {
    if (!busy) onClose();
  };

  const share = async () => {
    if (!body) return; // never: Share is disabled while there is no body, and while busy
    setBusy(true);
    setError('');
    const d = dims?.();
    const r = await mintShare(wid, d ? { ...body, cols: d.cols, rows: d.rows } : body);
    if (!r.ok) {
      // The dialog stays open on its choice, so a retry needs no retyping.
      setBusy(false);
      setError(r.error);
      return;
    }
    onClose();
    onMinted(r.resp);
  };

  const choices: RadioOption[] = [
    { value: 'view', className: 'sd-opt', label: <OptionText icon={icons.viewOnly} title="View only" desc="The guest watches. Nothing they type reaches this machine." /> },
    {
      value: 'typing',
      className: 'sd-opt',
      disabled: !offer.typingOk,
      label: <OptionText icon={icons.typing} title="Allow typing" desc="Into this sandboxed session only: a container that sees just the project, re-verified on every keystroke." reason={offer.reason} />,
    },
  ];
  if (offer.offerUnsafe)
    choices.push({
      value: 'unsandboxed',
      className: 'sd-opt sd-unsafe',
      label: (
        <OptionText
          icon={icons.unsandboxed}
          title="Full access, UNSANDBOXED: the guest can type into a real shell on this machine"
          desc="For a very trusted peer only. Bound to the first guest who joins, ends when the time you pick runs out, audited. Stop the share to revoke it at once."
        />
      ),
    });

  return (
    <Dialog open onOpenChange={(open) => !open && dismiss()} title={`Share ${title}`} className="share-dialog">
      <RadioGroup label="Who can type" value={mode} onChange={(v) => setMode(v as ShareMode)} options={choices} />
      {unsafe && <UnsafeConfirm className="sd-confirm" offer={offer} draft={draft} onChange={setDraft} />}
      {error && (
        <p className="sd-error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions sd-actions">
        <Button onClick={dismiss} disabled={busy}>
          Cancel
        </Button>
        <Button variant={unsafe ? 'danger' : 'primary'} className="sd-share" disabled={!body || busy} onClick={() => void share()}>
          Share
        </Button>
      </div>
    </Dialog>
  );
}
