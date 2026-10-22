// CMX-221 (CMX-212 P15): the Collaboration tab: the collab display name (per browser; each
// pane's presence picks it up from the `storage` event) and Guest typing (CMX-403).
import { useState } from 'react';
import { useStore } from '../../../shared/store/index.ts';
import { FieldRow, TextInput } from '../../../shared/ui/index.ts';
import { KEYS, setCollabName } from './prefs.ts';
import { ConfigSwitch, Section } from './parts.tsx';
import { config } from './state.ts';
import { lsRead } from './storage.ts';

export function Collaboration() {
  const cfg = useStore(config);
  const [name, setName] = useState(() => lsRead(KEYS.collabName) || '');
  const relay = !cfg ? '…' : cfg.error ? '(unavailable)' : cfg.collab_relay || '(default)';
  return (
    <>
      <Section title="Collaboration">
        <p className="s-desc">Your display name in shared terminals (presence pills + the pane facepile). Leave blank for a
          stable auto-name. Saved per browser, applies live.</p>
        <FieldRow id="collab-name" label="Display name" keywords="nickname username presence share identity">
          <TextInput plain maxLength={24} value={name} placeholder={lsRead(KEYS.collabAuto) || 'auto-assigned'}
            onChange={(v) => { setName(v); setCollabName(v); }} />
        </FieldRow>
        <FieldRow id="collab-relay" label="Relay" keywords="e2e encrypted server worker">
          <code className="s-relay" id="collab-relay">{relay}</code>
        </FieldRow>
        <p className="s-desc">End-to-end encrypted. The relay (<code>CHELA_COLLAB_RELAY</code>) is a zero-knowledge fan-out
          that only ever sees ciphertext (keys are derived in your browser from the pairing code). It does see room names +
          traffic timing (metadata), so run your own relay for full metadata privacy.</p>
      </Section>
      <Section title="Guest typing" id="settings-share-typing">
        <p className="s-desc">Shares are <strong>view only</strong> unless this is on. With it on, a share may let the guest
          type, but only into a <strong>sandboxed session</strong> (New session → Sandboxed: a container that sees only the
          project), checked live on every keystroke. Turning it off stops typing on live shares at once. See{' '}
          <code>docs/SHARE_SANDBOX.md</code> and run its checklist first.</p>
        <ConfigSwitch id="share-typing-toggle" field="share_typing" env="CHELA_SHARE_TYPING"
          label="Guest typing: let share guests type into sandboxed sessions"
          keywords="share typing keyboard input write guest sandbox sandboxed container view only unsandboxed full access"
          saved={(on) => 'Saved · guest typing ' + (on ? 'allowed into sandboxed sessions' : 'off, every share is view only')} />
      </Section>
    </>
  );
}
