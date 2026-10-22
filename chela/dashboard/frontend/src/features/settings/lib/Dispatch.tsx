// CMX-221 (CMX-212 P17): the Dispatch tab: how many agents and judges run at once (CMX-164),
// then the dispatcher, judge and critic policy knobs (CMX-220).
import { useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { Badge, Button, FieldRow } from '../../../shared/ui/index.ts';
import { memNote, stepPayload, workflowNote, type Concurrency as State } from '../model.ts';
import { KnobRows } from './Knobs.tsx';
import { Section, useLoad } from './parts.tsx';

async function loadConcurrency(): Promise<State | 'failed'> {
  try {
    const body = (await api('/api/config/concurrency')) as State | null;
    return body && Array.isArray(body.workflows) && body.judges ? body : 'failed';
  } catch {
    return 'failed';
  }
}

/** One "at once" row: − value +, and Reset where an override is stored. */
function Stepper({ id, label, value, note, step, resettable }: { id: string; label: string; value: number; note: string; step: (delta: number) => void; resettable: boolean }) {
  return (
    <>
      <FieldRow id={id} label={label}>
        <span className="s-stepper">
          <Button aria-label="Decrease" onClick={() => step(-1)}>−</Button>
          <span className="s-num">{value}</span>
          <Button aria-label="Increase" onClick={() => step(1)}>+</Button>
          {resettable && <Button onClick={() => step(0)}>Reset</Button>}
        </span>
      </FieldRow>
      <div className="s-desc">{note}</div>
    </>
  );
}

function Concurrency() {
  const [state, setState] = useLoad(loadConcurrency);
  const [msg, setMsg] = useState('');
  // Each click POSTs at once; the server validates (1 to 8) and logs the change.
  const step = async (kind: 'agents' | 'judges', idx: number, delta: number) => {
    if (!state || state === 'failed') return;
    try {
      const body = (await api('/api/config/concurrency', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(stepPayload(state, kind, idx, delta)) })) as (State & { error?: string }) | null;
      if (!body || body.error) {
        setMsg('Not saved: ' + (body?.error || 'rejected'));
        return;
      }
      setMsg('');
      setState(body);
    } catch {
      setMsg('Save failed. Nothing changed.');
    }
  };
  return (
    <Section title="Concurrency" id="settings-concurrency" keywords="agents judges at once concurrency parallel memory">
      <p className="s-desc">How many agents and judges run at once. Applies on the dispatcher's next tick, no restart.
        Lowering never stops a running agent or judge: it only holds new claims until the count drops under the limit.</p>
      <div id="concurrency-rows" className="s-dispatch-rows">
        {state === null && <div className="s-desc">Loading…</div>}
        {state === 'failed' && <div className="s-desc">(unavailable)</div>}
        {state && state !== 'failed' && (
          <>
            {state.workflows.map((w, i) => (
              <Stepper key={w.path} id={`concurrency-agents-${i}`} label={`Agents at once: ${w.name}`} value={w.effective}
                note={workflowNote(state, w)} resettable={w.override != null} step={(d) => void step('agents', i, d)} />
            ))}
            <Stepper id="concurrency-judges" label="Judges at once" value={state.judges.effective}
              note={`Source: ${state.judges.source}. ` + memNote(state, state.judges.effective)}
              resettable={state.judges.source === 'settings'} step={(d) => void step('judges', 0, d)} />
          </>
        )}
      </div>
      <div id="concurrency-msg" className="s-savemsg">{msg}</div>
    </Section>
  );
}

export function Dispatch() {
  return (
    <>
      <Concurrency />
      <Section title="Dispatch" id="settings-dispatch" keywords="judge critic merge workflow concurrency retries">
        <KnobRows path="/api/config/dispatch" kind="dispatch">
          <p className="s-desc">Dispatcher, judge, and critic policy. Blank a field to fall back to its <code>CHELA_*</code> env
            var (or the built-in default, shown as its placeholder). Four of these (<strong>Dispatch workflows</strong>,{' '}
            <strong>Judge</strong>, <strong>Critic</strong>, and <strong>Merge base</strong>, marked <Badge tone="warn">restart</Badge>{' '}
            below) are read once when the daemon/dashboard starts, so a save there takes effect on the{' '}
            <strong>next restart</strong>, not immediately. The rest apply on the next tick. A knob whose env var is currently
            set is disabled here. <strong>env always wins</strong>, so an edit would be silently discarded.</p>
        </KnobRows>
      </Section>
    </>
  );
}
