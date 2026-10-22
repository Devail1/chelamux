// CMX-221 (CMX-212 P17): a batch of dashboard knobs (Timing CMX-217, Dispatch CMX-220): one row
// per knob, its stored value in the field and its default as the placeholder, and ONE Save
// that posts every editable row, which the server validates all-or-nothing.
//
// A knob whose env var is set (`source: 'env'`) shows its effective value, disabled: env always
// wins server-side, so an edit would be silently discarded, and Save never posts it (or every
// Save would persist the env value as a dashboard value nobody chose).
import { useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { Badge, Button, FieldRow, NumberInput, Select, TextInput } from '../../../shared/ui/index.ts';
import { BOOL_DEFAULT, boolChoice, knobPayload, rejection, storedText, type Knob } from '../model.ts';
import { SaveMsg, useLoad, type Msg } from './parts.tsx';

async function loadKnobs(path: string): Promise<Knob[] | 'failed'> {
  try {
    const body = (await api(path)) as { knobs?: Knob[] } | null;
    return body?.knobs ?? 'failed';
  } catch {
    return 'failed';
  }
}

function KnobControl({ k, draft, set }: { k: Knob; draft: string | undefined; set: (v: string) => void }) {
  const env = k.source === 'env' ? k.env || 'its env var' : undefined;
  if (k.kind === 'bool') {
    return (
      <Select label={k.label} value={draft ?? boolChoice(k)} onChange={set} lockedBy={env}
        options={[{ value: BOOL_DEFAULT, label: `Default (${k.default ? 'on' : 'off'})` }, { value: 'true', label: 'On' }, { value: 'false', label: 'Off' }]} />
    );
  }
  const text = env ? String(k.effective) : (draft ?? storedText(k));
  if (k.kind === 'text' || k.kind === 'size') {
    return <TextInput value={text} onChange={set} lockedBy={env} placeholder={String(k.default)} />;
  }
  return (
    <NumberInput value={text === '' ? null : Number(text)} onChange={(n) => set(n == null ? '' : String(n))} lockedBy={env}
      fallback={typeof k.default === 'number' ? k.default : undefined} />
  );
}

export function KnobRows({ path, kind, children }: { path: string; kind: 'timing' | 'dispatch'; children?: React.ReactNode }) {
  const [knobs, setKnobs, reload] = useLoad(() => loadKnobs(path));
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // Bumped when the rows re-read the server, so a field's half-typed text goes with them.
  const [gen, setGen] = useState(0);
  const [msg, setMsg] = useState<Msg>(null);
  const save = async () => {
    if (!Array.isArray(knobs)) return;
    setMsg({ cls: '', text: 'Saving…' });
    let body: { error?: string; knobs?: Knob[] } | null;
    try {
      body = (await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(knobPayload(knobs, drafts)) })) as typeof body;
    } catch {
      setMsg({ cls: 'err', text: 'Save failed. Nothing changed.' });
      return;
    }
    // The batch is atomic server-side: an error means NONE of the fields changed.
    if (!body || body.error) {
      setMsg({ cls: 'err', text: 'Nothing saved: ' + rejection(body) });
      setDrafts({});
      setGen((g) => g + 1);
      reload(); // re-show what is actually stored
      return;
    }
    setMsg({ cls: 'ok', text: 'Saved.' });
    setDrafts({});
    setGen((g) => g + 1);
    if (body.knobs) setKnobs(body.knobs);
  };
  return (
    <>
      {children}
      <div id={`${kind}-rows`} className={`s-${kind}-rows`}>
        {knobs === null && <div className="s-desc">Loading…</div>}
        {knobs === 'failed' && <div className="s-desc">(unavailable)</div>}
        {Array.isArray(knobs) && knobs.map((k) => (
          <FieldRow key={`${gen}:${k.key}`} id={`${kind}-${k.key}`} label={k.label} unit={k.unit || undefined}
            badge={<>
              {k.source === 'env' && <Badge tone="ok" title={`Set by ${k.env}; env wins over the dashboard.`}>env</Badge>}
              {k.restart_required && <Badge tone="warn" title="Takes effect after a daemon/dashboard restart, not the next tick.">restart</Badge>}
            </>}>
            <KnobControl k={k} draft={drafts[k.key]} set={(v) => setDrafts((d) => ({ ...d, [k.key]: v }))} />
          </FieldRow>
        ))}
      </div>
      <div className="s-row"><Button variant="primary" onClick={() => void save()}>Save</Button></div>
      <SaveMsg id={`${kind}-msg`} msg={msg} />
    </>
  );
}
