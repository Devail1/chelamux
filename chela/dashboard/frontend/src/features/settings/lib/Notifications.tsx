// CMX-221 (CMX-212 P17): the Notifications tab: the review-toast mute, the browser notification
// for approval requests, and how the daemon's needs-input ping is configured (env only).
import { useState } from 'react';
import { FieldRow, Select } from '../../../shared/ui/index.ts';
import { notifyEnabled, setApprovalNotify } from '../../approvals/model.ts';
import { KEYS, setRunToastsMuted } from './prefs.ts';
import { Section } from './parts.tsx';
import { lsRead } from './storage.ts';

export function Notifications() {
  const [muted, setMuted] = useState(() => lsRead(KEYS.muteToasts) === '1');
  const [approvals, setApprovals] = useState(notifyEnabled);
  return (
    <Section title="Needs-input notifications" keywords="ntfy telegram webhook push alert ping">
      <p className="s-desc">Fires a one-shot ping when an agent's pane enters <code>waiting</code> (blocked on a prompt or
        question).</p>
      <FieldRow id="run-toasts-select" label="Review toasts" keywords="notifications popup alert mute awaiting_review">
        <Select value={muted ? 'muted' : 'show'} options={[{ value: 'show', label: 'Show' }, { value: 'muted', label: 'Muted' }]}
          onChange={(v) => { setRunToastsMuted(v === 'muted'); setMuted(v === 'muted'); }} />
      </FieldRow>
      <p className="s-desc">Pop a dashboard toast when a dispatcher run turns <code>awaiting_review</code> (or done / failed),
        so you learn a run needs review without watching the board.</p>
      <FieldRow id="approval-notify-select" label="Approval requests" keywords="notifications browser approval override release pending">
        {/* On asks the browser once; refused (or no API) leaves it off, and the select says so. */}
        <Select value={approvals ? 'on' : 'off'} options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'Browser notification' }]}
          onChange={(v) => { void setApprovalNotify(v).then(setApprovals); }} />
      </FieldRow>
      <p className="s-desc">A browser notification when a new override or release request waits for your approval (the
        approvals pill shows it either way). Off by default; your browser asks for permission once.</p>
      <p className="s-desc">Set on the daemon (env), then restart <code>chela run</code>:</p>
      <div className="s-kv"><code>CHELA_NOTIFY_URL</code><span>ntfy / Telegram / webhook (auto-detected)</span></div>
      <div className="s-examples">
        <div className="s-ex"><span className="s-tag">ntfy</span><code>https://ntfy.sh/your-topic</code></div>
        <div className="s-ex"><span className="s-tag">Telegram</span><code>https://api.telegram.org/bot&lt;token&gt;/sendMessage?chat_id=&lt;id&gt;</code></div>
        <div className="s-ex"><span className="s-tag">webhook</span><span className="s-exnote">any URL, receives JSON <code>{'{title,message,event}'}</code></span></div>
      </div>
    </Section>
  );
}
