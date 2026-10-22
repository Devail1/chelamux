// What every Settings tab is built from: a section card, a save message, a switch whose env
// var may pin it, and a one-shot fetch that re-runs on every open.
import { useEffect, useState, type ReactNode } from 'react';
import { useStore } from '../../../shared/store/index.ts';
import { FieldRow, SectionHeader, Toggle } from '../../../shared/ui/index.ts';
import { switchState, type SwitchKey } from '../model.ts';
import { config, postConfig, settings } from './state.ts';

/** One card. `keywords` are words a search matches beyond its heading and help text. */
export function Section({ title, id, keywords, children }: { title: string; id?: string; keywords?: string; children?: ReactNode }) {
  return (
    <section className="settings-section" id={id} data-keywords={keywords}>
      <SectionHeader title={title} />
      {children}
    </section>
  );
}

export type Msg = { cls: '' | 'ok' | 'err'; text: string } | null;

export function SaveMsg({ id, msg }: { id?: string; msg: Msg }) {
  return <div id={id} className={msg?.cls ? `s-savemsg ${msg.cls}` : 's-savemsg'}>{msg?.text ?? ''}</div>;
}

/** `load` on mount and again on every open of the modal; `null` until it lands. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []): [T | null, (v: T | null) => void, () => void] {
  const { epoch } = useStore(settings);
  const [value, setValue] = useState<T | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    load().then((v) => { if (live) setValue(v); }, () => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` is a fresh closure per render
  }, [epoch, n, ...deps]);
  return [value, setValue, () => setN((x) => x + 1)];
}

/**
 * An env-wins switch (Remote Control, Guest typing, File drop): the effective value, disabled
 * while its env var is set (an edit would be silently discarded), and a line naming the source.
 * A toggle POSTs at once; a failed or refused POST says so and re-reads what IS stored.
 */
export function ConfigSwitch({ id, field, env, label, keywords, saved }: { id: string; field: SwitchKey; env: string; label: string; keywords: string; saved: (on: boolean) => string }) {
  const cfg = useStore(config);
  const [msg, setMsg] = useState<Msg>(null);
  const st = switchState(cfg, field, env);
  const loading = !cfg || !!cfg.error;
  const toggle = async (on: boolean) => {
    setMsg({ cls: '', text: 'Saving…' });
    const r = await postConfig({ [field]: on });
    if (r.failed) setMsg({ cls: 'err', text: r.failed === 'request' ? 'Save failed. Unchanged.' : 'Rejected. Unchanged.' });
    else setMsg({ cls: 'ok', text: saved(!!r.cfg?.[field]) });
  };
  return (
    <>
      <FieldRow id={id} label={label} keywords={keywords}>
        <Toggle value={st.on} onChange={(on) => void toggle(on)} disabled={loading} lockedBy={st.locked ? st.env : undefined} />
      </FieldRow>
      <SaveMsg id={`${id.replace(/-toggle$/, '')}-msg`} msg={msg} />
      <p className="s-desc" id={`${id.replace(/-toggle$/, '')}-source`}>
        {cfg && !cfg.error && (
          <>In effect: <strong>{st.on ? 'On' : 'Off'}</strong> ({st.locked ? <>set by <code>{st.env}</code>; env wins, unset it to edit here</> : st.from}).</>
        )}
      </p>
    </>
  );
}
