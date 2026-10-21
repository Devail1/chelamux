// Pending approvals (CMX-103): override and release requests waiting on the operator, on every
// view. Ported from static/js/approvals.js by CMX-219 (CMX-212 P05).
//
// Nothing here decides by itself. Approve is a deliberate second action: the first click only
// ARMS the button ("Confirm merge"), the second sends it. A request the server already knows
// cannot be approved (`blocked`) shows why and offers only Deny.
import { useEffect, useState } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Button, IconButton, Sheet } from '../../../shared/ui/index.ts';
import { durStr, safeHref, whatText, type Request } from '../model.ts';
import { approvals, decide, tickApprovals } from './state.ts';

/** How long an armed Approve waits for its confirming click. */
export const ARM_MS = 6000;

const open = createStore(false);

export async function openApprovals(): Promise<void> {
  await tickApprovals();
  open.set(true);
}

/**
 * The pill: #btn-approvals floats in .safety-float on every view, #term-approvals sits in the
 * Wall's toolbar row (CMX-135), and style.css hides the floating copy while the Wall shows.
 * A failed refresh with nothing known keeps it up (unknown is not "none"), once the endpoint
 * has answered on this page or reported an error itself.
 */
export function ApprovalsPill({ id }: { id: string }) {
  const { rows, stale, loaded, staleLoud } = useStore(approvals);
  const n = rows.length;
  const showStale = stale && (loaded || staleLoud);
  const unknown = n === 0 && showStale;
  const Icon = unknown ? icons.warn : icons.approvals;
  const label = unknown ? 'couldn’t refresh approvals, tap to review' : `${n} approval request${n === 1 ? '' : 's'} waiting, tap to review`;
  return (
    <IconButton id={id} className="shares-indicator approvals-indicator" hidden={n === 0 && !showStale} label={label} onClick={() => void openApprovals()}>
      <span className="pa-pill-text">
        <Icon size={14} aria-hidden /> {unknown ? 'couldn’t refresh approvals' : `${n} pending`}
        {/* a phone drops the trailing word (CMX-135) so the floating pills clear the "+" */}
        {!unknown && <span className="pa-pill-word">{` approval${n === 1 ? '' : 's'}`}</span>}
      </span>
    </IconButton>
  );
}

function Row({ r, onDecided }: { r: Request; onDecided: (error: string | null) => void }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), ARM_MS);
    return () => clearTimeout(t);
  }, [armed]);
  if (r.kind === 'unreadable') {
    // A request FILE the server could not use: shown, never actionable.
    return (
      <div className="pa-row pa-bad" data-id={r.id}>
        <div className="pa-blocked"><icons.blocked size={12} aria-hidden /> {r.blocked || `unreadable request ${r.id}`}</div>
      </div>
    );
  }
  const run = async (action: 'approve' | 'deny') => {
    setBusy(true);
    const error = await decide(r.id, action);
    // a refused request may stay listed: it comes back un-armed and clickable, as a fresh row
    setBusy(false);
    setArmed(false);
    onDecided(error);
  };
  const href = r.pr_url ? safeHref(r.pr_url) : null;
  return (
    <div className="pa-row" data-id={r.id}>
      <div className="pa-what">{whatText(r)}</div>
      <div className="pa-meta">
        asked by {r.actor || 'unknown'} · {durStr(r.age_s)} ago · expires in {durStr(r.expires_in_s)}
        {r.pr_url && (href
          ? <> · <a href={href} target="_blank" rel="noopener">PR</a></>
          : <> · <span className="pa-prtext">PR: {r.pr_url}</span></>)}
      </div>
      {r.reason && <div className="pa-why">“{r.reason}”</div>}
      <div className="pa-ctl">
        {r.blocked
          ? <div className="pa-blocked"><icons.blocked size={12} aria-hidden /> Can't be approved: {r.blocked}</div>
          : <Button className={armed ? 'pa-approve pa-armed' : 'pa-approve'} disabled={busy}
              onClick={() => (armed ? void run('approve') : setArmed(true))}>{armed ? 'Confirm merge' : 'Approve'}</Button>}
        <Button className="pa-deny" disabled={busy} onClick={() => void run('deny')}>Deny</Button>
      </div>
    </div>
  );
}

/** The sheet: what, who asked, why, age, time to expiry, and Approve / Deny per request. */
export function ApprovalsSheet() {
  const isOpen = useStore(open);
  const { rows, stale } = useStore(approvals);
  // The last refused decision's reason: it outlives the re-render (the request has usually
  // left the list by then), so it rides on the sheet, not on the row.
  const [flash, setFlash] = useState('');
  const decided = async (error: string | null) => {
    setFlash(error ?? '');
    await tickApprovals();
  };
  return (
    <Sheet open={isOpen} onOpenChange={(o) => {
        open.set(o);
        if (!o) setFlash('');
      }} title="Pending approvals" className="approvals-sheet">
      <div className="ss-sub">Approving merges NOW, after re-checking that the head is unchanged, CI is green and GitHub reports the PR mergeable. It skips only the judge, and is recorded. Deny merges nothing.</div>
      <div className="pa-err ss-err" role="alert" hidden={!flash}>{flash}</div>
      <div className="pa-stale ss-err" hidden={!stale}>Couldn’t refresh approvals. Showing the last known list.</div>
      <div className="ss-list">
        {rows.length
          ? rows.map((r) => <Row key={r.id} r={r} onDecided={(e) => void decided(e)} />)
          : <div className="ss-empty">{stale ? 'The approvals list is unavailable.' : 'Nothing is waiting for approval.'}</div>}
      </div>
    </Sheet>
  );
}
