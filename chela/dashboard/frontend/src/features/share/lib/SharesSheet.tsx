// CMX-240 (CMX-212 P25): the Active shares sheet, the ONE place a share's link + code + Stop
// live (CMX-152), on the kit's Dialog as a sheet (anchored on desktop, a bottom sheet on a
// phone). Each row says who can type and changes it in place (CMX-421); Stop and Stop all are
// the kill switch, so they never wait on a row's reads.
import { useEffect, useRef, useState } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { Button, Dialog } from '../../../shared/ui/index.ts';
import { fetchShareOptions } from './api.ts';
import { useNow } from './clock.ts';
import { closeSharesSheet, fetchShareInfo, paneTitle, setShareMode, sheet, stopAllShares, stopShare } from './actions.ts';
import { fmtShareEnd, shareOffer, SHARE_MODE_UI, shareTimeLeft, upgradeBody, type ShareMode, type ShareOptions } from './model.ts';
import { ShareInfoRows, type ShareInfo } from './ShareInfoRows.tsx';
import { shares, type ShareEntry } from './state.ts';
import { UnsafeConfirm, type ConfirmDraft } from './UnsafeConfirm.tsx';

const MODE_ICON = { view: icons.viewOnly, typing: icons.typing, unsandboxed: icons.unsandboxed } as const;

function ModeBadge({ entry, now }: { entry: ShareEntry; now: number }) {
  if (entry.mode === 'unsandboxed') {
    const left = shareTimeLeft(entry.expiresAt, now);
    return (
      <span className="ss-mode ss-mode-unsafe" title="UNSANDBOXED: guest can type">
        UNSANDBOXED{left && <> · <span className="ss-left">{left}</span></>}
      </span>
    );
  }
  const Icon = MODE_ICON[entry.mode];
  const title = entry.mode === 'typing' ? 'Guest may type (sandboxed session)' : 'View only';
  return (
    <span className="ss-mode" title={title} role="img" aria-label={title}>
      <Icon aria-hidden size={12} />
    </span>
  );
}

/** The live mode as three choices, the current one pressed. Down is always offered; up only
 * where the share dialog would offer it. The server re-checks all of it. */
function ModeControl({ wid, entry, options, now }: { wid: string; entry: ShareEntry; options: ShareOptions; now: number }) {
  const cur = entry.mode;
  const offer = shareOffer(options);
  const offerUnsafe = cur === 'unsandboxed' || offer.offerUnsafe;
  const [arming, setArming] = useState(false);
  const [draft, setDraft] = useState<ConfirmDraft>({ minutes: offer.mins, confirm: '', confirmLong: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const confirmRef = useRef<HTMLDivElement>(null);
  const grant = upgradeBody(offer, draft);

  useEffect(() => {
    if (arming) confirmRef.current?.querySelector<HTMLInputElement>('input')?.focus();
  }, [arming]);

  const apply = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError('');
    const r = await setShareMode(wid, body);
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setArming(false);
    setDraft({ minutes: offer.mins, confirm: '', confirmLong: '' });
  };
  const pick = (m: ShareMode) => {
    if (m === cur || busy) return;
    // UNSANDBOXED needs the typed window name (twice past 4 h), as in the dialog.
    if (m === 'unsandboxed') return setArming(true);
    void apply({ mode: m });
  };
  const enabled = { view: true, typing: offer.typingOk, unsandboxed: true };
  const modes: ShareMode[] = offerUnsafe ? ['view', 'typing', 'unsandboxed'] : ['view', 'typing'];

  return (
    <>
      <div className="ss-mode-ctl" role="group" aria-label="Access for this share">
        {modes.map((m) => {
          const Icon = MODE_ICON[m];
          return (
            <Button key={m} className={m === 'unsandboxed' ? 'ss-mode-opt ss-mode-opt-unsafe' : 'ss-mode-opt'} data-mode={m} aria-pressed={cur === m} disabled={busy || (!enabled[m] && cur !== m)} onClick={() => pick(m)}>
              <Icon aria-hidden size={12} /> {SHARE_MODE_UI[m].label}
            </Button>
          );
        })}
      </div>
      <div className={cur === 'unsandboxed' ? 'ss-mode-desc ss-mode-desc-unsafe' : 'ss-mode-desc'}>
        {SHARE_MODE_UI[cur].desc}
        {cur === 'unsandboxed' && entry.expiresAt && ` Ends ${fmtShareEnd(entry.expiresAt, now)}${shareTimeLeft(entry.expiresAt, now) ? ` (${shareTimeLeft(entry.expiresAt, now)})` : ''}.`}
      </div>
      {!offer.typingOk && cur !== 'typing' && <div className="ss-mode-reason">{offer.reason}</div>}
      {arming && cur !== 'unsandboxed' && (
        <div ref={confirmRef}>
          <UnsafeConfirm className="ss-unsafe-confirm" offer={offer} draft={draft} onChange={setDraft} />
          <Button variant="danger" className="ss-confirm-go" disabled={!grant || busy} onClick={() => grant && void apply(grant)}>
            Grant full access
          </Button>
        </div>
      )}
      {error && (
        <div className="ss-mode-err" role="alert">
          {error}
        </div>
      )}
    </>
  );
}

function ShareRow({ wid, entry, focused, now }: { wid: string; entry: ShareEntry; focused: boolean; now: number }) {
  // undefined while the row's reads are in flight: no control is drawn on a guess.
  const [info, setInfo] = useState<ShareInfo | undefined>(undefined);
  const [options, setOptions] = useState<ShareOptions | undefined>(undefined);
  const [stopping, setStopping] = useState(false);
  const [stopFailed, setStopFailed] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);

  // A row is keyed by its wid, so a late answer can only land on its own row (or on none).
  useEffect(() => {
    void Promise.all([fetchShareInfo(wid), fetchShareOptions(wid)]).then(([i, o]) => {
      setInfo(i);
      setOptions(o);
    });
  }, [wid]);
  useEffect(() => {
    if (focused) rowRef.current?.scrollIntoView({ block: 'nearest' });
  }, [focused]);

  const stop = async () => {
    setStopping(true);
    setStopFailed(false);
    // A stopped share leaves the list (the sheet closes with the last one); a failed one
    // stays, and says so.
    if (await stopShare(wid)) {
      if (!shares.get().size) closeSharesSheet();
      return;
    }
    setStopping(false);
    setStopFailed(true);
  };

  return (
    <div ref={rowRef} className={focused ? 'ss-row ss-row-focus' : 'ss-row'} data-wid={wid}>
      {focused && <div className="ss-already">This session is already shared. Change who can type below. The link and code stay the same.</div>}
      <div className="ss-row-hd">
        <span className="ss-label">
          <ModeBadge entry={entry} now={now} /> {paneTitle(wid)} <span className="ss-wid">{wid}</span>
        </span>
        <Button className="ss-stop" disabled={stopping} onClick={() => void stop()}>
          {stopping ? 'Stopping…' : 'Stop'}
        </Button>
      </div>
      {stopFailed && (
        <div className="ss-mode-err" role="alert">
          Stop failed: this share is still live. Try again.
        </div>
      )}
      {options ? <ModeControl wid={wid} entry={entry} options={options} now={now} /> : <div className="ss-mode-desc">Loading…</div>}
      <div className="ss-row-body">{info && <ShareInfoRows info={info} emptyMsg="Link unavailable. Reopen this sheet to retry." />}</div>
    </div>
  );
}

function StopAll({ wids }: { wids: string[] }) {
  const [stopping, setStopping] = useState(false);
  const [failed, setFailed] = useState(false);
  const stopAll = async () => {
    setStopping(true);
    setFailed(false);
    if (await stopAllShares(wids)) return closeSharesSheet();
    setStopping(false);
    setFailed(true);
  };
  return (
    <>
      {failed && (
        <div className="ss-mode-err" role="alert">
          Some shares did not stop. They are still listed above.
        </div>
      )}
      <Button className="ss-stopall" disabled={stopping} onClick={() => void stopAll()}>
        {stopping ? 'Stopping…' : `Stop all sharing (${wids.length})`}
      </Button>
    </>
  );
}

export function SharesSheet() {
  const target = useStore(sheet);
  const list = useStore(shares);
  const now = useNow([...list.values()].some((e) => e.mode === 'unsandboxed'));
  if (!target) return null;
  const wids = [...list.keys()];
  return (
    <Dialog
      open
      variant="sheet"
      icon={icons.share}
      onOpenChange={(open) => !open && closeSharesSheet()}
      title="Active shares"
      description="Each share says who can type. Changing it keeps the same link and code. Stop a share to revoke it: the link dies and the code rotates."
    >
      <div className="ss-list">
        {wids.length ? (
          wids.map((w) => <ShareRow key={`${target.seq}:${w}`} wid={w} entry={list.get(w) as ShareEntry} focused={w === target.focus} now={now} />)
        ) : (
          <div className="ss-empty">No active shares.</div>
        )}
      </div>
      {wids.length > 0 && <StopAll key={target.seq} wids={wids} />}
    </Dialog>
  );
}
