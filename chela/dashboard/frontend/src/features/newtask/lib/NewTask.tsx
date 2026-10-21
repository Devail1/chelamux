// ➕📐 NEW TASK (CMX-6): create a Linear issue from the Work view or the ⌘K palette. Ported
// from static/js/newtask.js by CMX-231 (FRONTEND_PORT_PLAN.md P11).
//
// The form POSTs to the dashboard (/api/dispatcher/linear/issue); the DASHBOARD calls Linear,
// so the API key never reaches this page. Workflows and blockers come off the payload features/work
// already polls. ⛔ A failed submit KEEPS every field and shows why; only a success clears.
import { useState } from 'react';
import { BASE_PATH } from '../../../shared/api/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { refreshWork, work } from '../../../shared/store/work.ts';
import { Button, Dialog, DialogClose, FieldRow, MultiSelect, Select, TextArea, TextInput } from '../../../shared/ui/index.ts';
import { PRIORITIES, blockerChoices, linearWorkflows } from '../model.ts';

const open = createStore(false);

export function openNewTask() {
  open.set(true);
}

type Issue = { identifier: string; url?: string; title?: string };
type Created = { ok?: boolean; error?: string; issue?: Issue; warnings?: string[] };
type Form = { workflow: string; title: string; desc: string; priority: string; blocked: string[] };
const EMPTY: Omit<Form, 'workflow'> = { title: '', desc: '', priority: '0', blocked: [] };

/** POST the form. Resolves to the created response, or the error to show. */
async function create(f: Form): Promise<{ data: Created } | { error: string }> {
  const payload = { workflow_path: f.workflow, title: f.title.trim(), description: f.desc, priority: Number(f.priority) || 0, blocked_by: f.blocked };
  try {
    const resp = await fetch(`${BASE_PATH}/api/dispatcher/linear/issue`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    let data: Created = {};
    try {
      data = ((await resp.json()) as Created | null) ?? {};
    } catch {
      data = {};
    }
    if (!resp.ok || !data.ok) return { error: data.error || `Linear refused it (HTTP ${resp.status}).` };
    return { data };
  } catch (e) {
    return { error: `Request failed. Nothing was created. ${String(e)}` };
  }
}

export function NewTask() {
  const isOpen = useStore(open);
  const wfs = linearWorkflows(useStore(work));
  // The form outlives a close: a brief typed, closed and reopened is still there.
  const [form, setForm] = useState<Form>({ workflow: '', ...EMPTY });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const workflow = wfs.some((w) => w.path === form.workflow) ? form.workflow : (wfs[0]?.path ?? '');
  const choices = blockerChoices(wfs.find((w) => w.path === workflow));
  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));

  async function submit() {
    if (!form.title.trim()) return setError('A title is required.');
    setError('');
    setBusy(true);
    // Only the picks this workflow still offers: a blocker hidden from the list is never sent.
    const res = await create({ ...form, workflow, blocked: form.blocked.filter((id) => choices.some((c) => c.id === id)) });
    setBusy(false);
    if ('error' in res) return setError(res.error);
    setForm((f) => ({ ...f, ...EMPTY }));
    open.set(false);
    setError('');
    const warn = (res.data.warnings || []).join('; ');
    if (warn) window.alert(`${res.data.issue?.identifier} created, but: ${warn}`);
    await refreshWork(); // the new issue lands in the queue on this refresh
  }

  return (
    <Dialog open={isOpen} onOpenChange={(o) => {
        open.set(o);
        if (!o) setError('');
      }} title="New task" className="modal-newtask">
      <FieldRow label="Workflow">
        <Select value={workflow} onChange={(v) => set({ workflow: v, blocked: [] })} options={wfs.map((w) => ({ value: w.path, label: w.project_key || w.path }))} />
      </FieldRow>
      <FieldRow label="Title">
        <TextInput value={form.title} onChange={(title) => set({ title })} autoFocus />
      </FieldRow>
      <FieldRow label="Description" hint="Markdown: the brief the agent reads.">
        <TextArea value={form.desc} onChange={(desc) => set({ desc })} rows={8} />
      </FieldRow>
      <FieldRow label="Priority">
        <Select value={form.priority} onChange={(priority) => set({ priority })} options={PRIORITIES} />
      </FieldRow>
      <FieldRow label="Blocked by" hint="Optional; ctrl/⌘-click for several.">
        <MultiSelect value={form.blocked.filter((id) => choices.some((c) => c.id === id))} onChange={(blocked) => set({ blocked })}
          options={choices.map((c) => ({ value: c.id, label: `${c.id}: ${c.title.slice(0, 80)}` }))} />
      </FieldRow>
      <div className="newtask-error" role="alert">
        {error || (wfs.length ? '' : 'No workflow here uses a Linear tracker (tracker: kind: linear).')}
      </div>
      <div className="modal-actions">
        <DialogClose asChild><Button>Cancel</Button></DialogClose>
        <Button variant="primary" disabled={busy} onClick={() => void submit()}>Create</Button>
      </div>
    </Dialog>
  );
}
