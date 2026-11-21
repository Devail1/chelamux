// CMX-434: the sidebar's "Suggest names" dialog. One row per live interactive session (a
// toggle adds dispatched runs and judges): its current name and title, an editable suggested
// name and title, and a toggle saying whether to apply it. Suggestions come from the server
// (one Haiku call over each session's latest recap or last turns); NOTHING is applied until
// Apply. A bad or duplicate name is flagged inline and blocks the apply; each row reports
// what applying it did.
import { useEffect, useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Button, Dialog, DialogClose, TextInput, Toggle } from '../../../shared/ui/index.ts';
import type { AgentRow } from '../../sidebar/model.ts';
import {
  type Outcome, type Row, type Suggestion, NAME_MAX, TITLE_MAX, applyRows, candidateWids, initialRows,
  nameChanges, outcomeFailed, outcomeText, postTitle, rowProblems, titleChanges, type TitleOutcome,
} from '../model.ts';

/** Every open is a fresh load (`seq`), so nothing from the last open lingers. */
const opened = createStore<number>(0);
let seq = 0;

/** Open the dialog (the sidebar VIEW menu's "Suggest names…"). */
export function openSuggestNames() {
  opened.set(++seq);
}

export function closeSuggestNames() {
  opened.set(0);
}

/** A JSON POST through the dashboard's API (its reply body, whatever the status). */
export const post = (path: string, body: unknown) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** CMX-459: one session's title, set by hand (the pane and row menus' "Rename title…"), through
 * the same title write as the dialog's apply. */
export function retitle(wid: string, title: string): Promise<TitleOutcome> {
  return postTitle(wid, title, post);
}

type Load = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'rows'; error: string | null };

function SuggestBody() {
  const windows = useStore(agents) as unknown as AgentRow[];
  const [includeAll, setIncludeAll] = useState(false);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [rows, setRows] = useState<Row[]>([]);
  const [results, setResults] = useState<Record<string, Outcome>>({});
  const [busy, setBusy] = useState(false);

  // The wids are read when the dialog opens or the toggle flips, not on every poll: a
  // suggestion is one model call, never re-run behind the user's edits.
  useEffect(() => {
    let live = true;
    const wids = candidateWids(agents.get() as unknown as AgentRow[], includeAll);
    post('/api/agents/suggest-names', { wids }).then(
      (raw) => {
        if (!live) return;
        const res = raw as { ok?: boolean; rows?: Suggestion[]; error?: string | null } | null;
        if (!res || res.ok !== true || !Array.isArray(res.rows)) {
          setLoad({ kind: 'error', message: res?.error || 'Could not read the sessions.' });
          return;
        }
        setRows(initialRows(res.rows));
        setLoad({ kind: 'rows', error: res.error ?? null });
      },
      () => live && setLoad({ kind: 'error', message: 'Could not read the sessions.' }),
    );
    return () => {
      live = false;
    };
  }, [includeAll]);

  // A flip of the toggle is a fresh load: the old rows and results go with it.
  const flip = (v: boolean) => {
    setLoad({ kind: 'loading' });
    setResults({});
    setIncludeAll(v);
  };
  const problems = rowProblems(rows, windows);
  const edit = (wid: string, patch: Partial<Row>) => setRows(rs => rs.map(r => (r.wid === wid ? { ...r, ...patch } : r)));
  const toApply = (all: boolean) => rows.map(r => (all ? { ...r, checked: true } : r));
  const blocked = (all: boolean) => Object.keys(rowProblems(toApply(all), windows)).length > 0;
  const selected = rows.filter(r => r.checked && (nameChanges(r) || titleChanges(r))).length;

  async function apply(all: boolean) {
    const next = toApply(all);
    setRows(next);
    if (Object.keys(rowProblems(next, windows)).length) return;
    setBusy(true);
    const out = await applyRows(next, post);
    setBusy(false);
    setResults(out);
    // What landed is the new current value; what is pending shows as pending.
    setRows(rs => rs.map(r => {
      const o = out[r.wid];
      if (!o) return r;
      return {
        ...r,
        name: o.name?.ok ? r.newName : r.name,
        title: o.title?.state === 'applied' ? r.newTitle.trim() : r.title,
        pending: o.title?.state === 'pending' ? r.newTitle.trim() : r.pending,
        checked: outcomeFailed(o) ? r.checked : false,
      };
    }));
  }

  return (
    <>
      <div className="sn-toolbar">
        <label className="sn-include">
          <Toggle value={includeAll} onChange={flip} label="Include dispatched runs and judges" />
          <span>Include dispatched runs and judges</span>
        </label>
      </div>
      {load.kind === 'loading' && <div className="sn-empty">Reading each session…</div>}
      {load.kind === 'error' && <div className="sn-empty sn-error" role="alert">{load.message}</div>}
      {load.kind === 'rows' && (
        <>
          {load.error && <div className="sn-error" role="alert">No suggestions: {load.error}. Edit the rows by hand.</div>}
          {rows.length === 0 && <div className="sn-empty">No live sessions to name.</div>}
          {rows.length > 0 && (
            <div className="sn-grid" role="table" aria-label="Suggested names">
              <div className="sn-row sn-head" role="row">
                <span role="columnheader">Apply</span>
                <span role="columnheader">Name</span>
                <span role="columnheader">Title</span>
              </div>
              {rows.map(r => {
                const p = problems[r.wid];
                const res = results[r.wid];
                return (
                  <div className="sn-row" role="row" key={r.wid} data-wid={r.wid}>
                    <span role="cell" className="sn-check">
                      <Toggle value={r.checked} onChange={(checked) => edit(r.wid, { checked })} label={`Apply to ${r.name}`} />
                    </span>
                    <span role="cell" className="sn-cell">
                      <span className="sn-current" title={r.manual ? 'Named by hand' : undefined}>
                        {r.name}{r.manual && <span className="sn-manual"> (manual)</span>} <span className="sn-wid">{r.wid}</span>
                      </span>
                      <TextInput value={r.newName} onChange={(newName) => edit(r.wid, { newName })} label={`New name for ${r.name}`}
                        plain maxLength={NAME_MAX + 10} className={`s-input${p?.name ? ' sn-invalid' : ''}`} />
                      {p?.name && <span className="sn-problem" role="alert">{p.name}</span>}
                    </span>
                    <span role="cell" className="sn-cell">
                      <span className="sn-current">
                        {r.title || <em>no title</em>}
                        {r.pending && <span className="sn-pending" title={`Waits for the session to go idle: ${r.pending}`}> pending</span>}
                      </span>
                      <TextInput value={r.newTitle} onChange={(newTitle) => edit(r.wid, { newTitle })} label={`New title for ${r.name}`}
                        maxLength={TITLE_MAX + 20} className={`s-input${p?.title ? ' sn-invalid' : ''}`} />
                      {p?.title && <span className="sn-problem" role="alert">{p.title}</span>}
                      {r.note && !res && <span className="sn-note">{r.note}</span>}
                    </span>
                    {res && <span role="cell" className={`sn-result${outcomeFailed(res) ? ' sn-failed' : ''}`}>{outcomeText(res)}</span>}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
      <div className="modal-actions">
        <DialogClose asChild><Button>Close</Button></DialogClose>
        <Button disabled={busy || load.kind !== 'rows' || !rows.length || blocked(true)} onClick={() => void apply(true)}>Apply all</Button>
        <Button variant="primary" disabled={busy || load.kind !== 'rows' || !selected || blocked(false)} onClick={() => void apply(false)}>
          {`Apply selected (${selected})`}
        </Button>
      </div>
    </>
  );
}

export function SuggestNames() {
  const n = useStore(opened);
  return (
    <Dialog open={n > 0} onOpenChange={(o) => { if (!o) closeSuggestNames(); }} title="Suggest names" className="modal-suggest-names"
      description="Names and titles proposed from what each session is doing now. Nothing changes until you apply.">
      {n > 0 && <SuggestBody key={n} />}
    </Dialog>
  );
}
