// Access requests from sandboxed guests (CMX-7): a guest (or its Claude) in a sandboxed session
// can ASK for more access (a host mount, a domain in web mode, a named operation). This is the
// operator's half: a pill in .safety-float while one waits, and a sheet to decide each. Ported
// from static/js/accessreq.js by CMX-219 (CMX-212 P05).
//
// Nothing here decides by itself: approve needs a click, it lasts 60 min unless another
// duration is picked, and a mount is read-only unless the guest asked for write AND "allow
// write" is on. A request the server refuses shows the reason and has no Approve.
import { useState } from 'react';
import { BASE_PATH, api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Button, IconButton, Select, Sheet, Toggle } from '../../../shared/ui/index.ts';
import { durLabel, durationChoices, pendingCount, timeLeft, who, type AccessRequest } from '../model.ts';

type State = { rows: AccessRequest[]; shareTyping: boolean; defaultMinutes: number };
export const accessRequests = createStore<State>({ rows: [], shareTyping: false, defaultMinutes: 60 });
const open = createStore(false);

/** One /api/share-requests read. A failed one keeps the last list (the next tick retries). */
export async function tickAccessRequests(): Promise<void> {
  let data: { requests?: AccessRequest[]; share_typing?: boolean; default_minutes?: number } | null;
  try {
    data = (await api('/api/share-requests')) as typeof data;
  } catch {
    return;
  }
  // an error reply is not "no requests": keep the last list
  if (!Array.isArray(data?.requests)) return;
  accessRequests.set({ rows: data.requests, shareTyping: !!data?.share_typing, defaultMinutes: data?.default_minutes || 60 });
}

export async function openAccessRequests(): Promise<void> {
  await tickAccessRequests();
  open.set(true);
}

export function closeAccessRequests() {
  open.set(false);
}

/** POST a decision; resolves to the reason it was refused, or null. */
async function decide(id: string, action: string, body: object = {}): Promise<string | null> {
  try {
    const res = await fetch(`${BASE_PATH}/api/share-requests/${encodeURIComponent(id)}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const resp = (await res.json()) as { ok?: boolean; error?: string } | null;
    return resp?.ok ? null : resp?.error || `Could not ${action} the request`;
  } catch {
    return `Could not ${action} the request`;
  }
}

export function AccessRequestsPill() {
  const n = pendingCount(useStore(accessRequests).rows);
  return (
    <IconButton id="btn-access-req" className="shares-indicator access-req-indicator" hidden={n === 0}
      label={`${n} sandboxed-session access request${n === 1 ? '' : 's'} waiting, tap to decide`} onClick={() => void openAccessRequests()}>
      <span className="ar-pill-text"><icons.accessRequest size={14} aria-hidden /> {n} access request{n === 1 ? '' : 's'}</span>
    </IconButton>
  );
}

function Row({ r, shareTyping, defaultMinutes }: { r: AccessRequest; shareTyping: boolean; defaultMinutes: number }) {
  const [minutes, setMinutes] = useState(String(defaultMinutes));
  const [rw, setRw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (action: string, body?: object) => {
    setBusy(true);
    const err = await decide(r.id, action, body);
    setBusy(false);
    if (err) return setError(err);
    await tickAccessRequests();
  };
  const head = (
    <>
      <div className="ar-hd"><span className="ar-who">{who(r)}</span><span className={`ar-status ar-${r.status}`}>{r.status}</span></div>
      <div className="ar-what">{r.description || ''}</div>
      {r.reason && <div className="ar-why">“{r.reason}”</div>}
    </>
  );
  if (r.status === 'approved') {
    const access = r.rw ? ' · read-write' : r.kind === 'mount' ? ' · read-only' : '';
    return (
      <div className="ar-row" data-id={r.id}>
        {head}
        <div className="ar-meta">{timeLeft(r.seconds_left)}{access}</div>
        <Button className="ar-revoke" disabled={busy} onClick={() => void run('revoke')}>Revoke</Button>
        {error && <div className="ar-err" role="alert">{error}</div>}
      </div>
    );
  }
  if (r.status !== 'pending') return <div className="ar-row ar-done" data-id={r.id}>{head}</div>;
  const deny = <Button className="ar-deny" disabled={busy} onClick={() => void run('deny')}>Deny</Button>;
  if (r.refusal) {
    return (
      <div className="ar-row" data-id={r.id}>
        {head}
        <div className="ar-refused"><icons.blocked size={12} aria-hidden /> Can't be approved: {r.refusal}</div>
        {deny}
        {error && <div className="ar-err" role="alert">{error}</div>}
      </div>
    );
  }
  const asksWrite = r.kind === 'mount' && r.access === 'rw';
  return (
    <div className="ar-row" data-id={r.id}>
      {head}
      <div className="ar-ctl">
        <Select label="How long the approval lasts" value={minutes} onChange={setMinutes}
          options={durationChoices(defaultMinutes).map((n) => ({ value: String(n), label: durLabel(n) }))} />
        {asksWrite && (
          <span className="ar-rw">
            <Toggle label="allow write (the guest asked for it; read-only otherwise)" value={rw} onChange={setRw} /> allow write (the guest asked for it; read-only otherwise)
          </span>
        )}
        <Button className="ar-approve" disabled={busy || !shareTyping}
          onClick={() => void run('approve', { minutes: parseInt(minutes, 10) || 60, rw: asksWrite && rw })}>Approve</Button>
        {deny}
      </div>
      {!shareTyping && <div className="ar-refused">Guest typing is off. Turn it on in Settings to approve.</div>}
      {error && <div className="ar-err" role="alert">{error}</div>}
    </div>
  );
}

export function AccessRequestsSheet() {
  const isOpen = useStore(open);
  const { rows, shareTyping, defaultMinutes } = useStore(accessRequests);
  return (
    <Sheet open={isOpen} onOpenChange={open.set} title="Access requests" className="access-req-sheet">
      <div className="ss-sub">A sandboxed guest asked for more access. Nothing changes until you approve. An approved mount restarts that session's container with it (read-only unless you allow write), and a live typing share of it may end. Stopping the share or turning Guest typing off revokes every approval.</div>
      <div className="ss-list">
        {rows.length
          ? rows.map((r) => <Row key={r.id} r={r} shareTyping={shareTyping} defaultMinutes={defaultMinutes} />)
          : <div className="ss-empty">No access requests.</div>}
      </div>
    </Sheet>
  );
}

// CMX-7: sandboxed guests' access requests, a pill on every view.
registerLane('accessRequests', () => tickAccessRequests());
