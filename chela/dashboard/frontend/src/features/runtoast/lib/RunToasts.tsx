// The dispatcher run-state toasts, off the SSE `runs` frame, on every view. Moved out of
// static/js/work.js by CMX-219 (CMX-212 P06). Per-run last-seen status keeps them
// EDGE-TRIGGERED, primed from the `hello` baseline so a run already awaiting review when the
// page loads (or reconnects) is recorded, not re-toasted.
import { useEffect, useState } from 'react';
import { onEvent } from '../../../shared/events/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { Toast, ToastProvider } from '../../../shared/ui/index.ts';
import { MUTE_KEY, RUN_TOAST_KINDS, runToastKind } from '../model.ts';

/** How long a toast stays (Radix pauses it while hovered). */
export const TOAST_MS = 15000;

type RunRow = { task_id?: string; label?: string; status?: string; pr_url?: string | null };
export type RunToast = { id: number; kind: string; label: string; prUrl: string | null };

const prev = new Map<string, string | undefined>();
let nextId = 0;
const listeners = new Set<(t: RunToast) => void>();

function rows(d: unknown): RunRow[] {
  const runs = (d as { runs?: unknown } | null)?.runs;
  return Array.isArray(runs) ? (runs as RunRow[]) : [];
}

function muted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === '1';
  } catch {
    return false;
  }
}

/** Record the baseline (`hello`): what is already in a review state is not news. */
export function primeRunStatus(d: unknown) {
  for (const r of rows(d)) if (r?.task_id) prev.set(r.task_id, r.status);
}

/** The toasts one `runs` frame fires: one per run whose status moved INTO a toasted state. */
export function runStateToasts(d: unknown): RunToast[] {
  const out: RunToast[] = [];
  for (const r of rows(d)) {
    if (!r?.task_id) continue;
    const kind = runToastKind(prev.get(r.task_id), r.status);
    prev.set(r.task_id, r.status);
    if (kind && !muted()) out.push({ id: ++nextId, kind, label: r.label || r.task_id || 'run', prUrl: r.pr_url || null });
  }
  for (const t of out) for (const l of listeners) l(t);
  return out;
}

/** Test seam: a fresh page. */
export function resetRunStatus() {
  prev.clear();
}

function RunToastView({ t, onClose }: { t: RunToast; onClose: () => void }) {
  const meta = RUN_TOAST_KINDS[t.kind];
  const Icon = meta ? icons[meta.icon] : icons.runs;
  return (
    <Toast open onOpenChange={(o) => !o && onClose()} duration={TOAST_MS}
      title={<><Icon size={13} aria-hidden /> <strong>{t.label}</strong> → {meta?.text}{t.prUrl && <> · <a href={t.prUrl} target="_blank" rel="noopener">PR</a></>}</>} />
  );
}

/** The stack, bottom right (#run-toasts-island). */
export function RunToasts() {
  const [list, setList] = useState<RunToast[]>([]);
  useEffect(() => {
    const add = (t: RunToast) => setList((l) => [...l, t]);
    listeners.add(add);
    return () => {
      listeners.delete(add);
    };
  }, []);
  return (
    <ToastProvider>
      {list.map((t) => <RunToastView key={t.id} t={t} onClose={() => setList((l) => l.filter((x) => x.id !== t.id))} />)}
    </ToastProvider>
  );
}

onEvent('hello', primeRunStatus);
onEvent('runs', (d) => {
  runStateToasts(d);
});
