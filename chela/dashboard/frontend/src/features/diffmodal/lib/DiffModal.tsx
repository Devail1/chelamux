// PER-SESSION DIFF MODAL (CMX-299; ported to React by CMX-237, FRONTEND_PORT_PLAN.md P23):
// every file the session's live pane cwd has changed since its last commit, and a
// click-through unified diff for any one of them. Read-only, backed by chela.diffsurface via
// /api/agents/<wid>/diff and /api/agents/<wid>/diff/patch; no dependency on the dispatcher.
import { Fragment, useEffect, useRef, useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { Dialog, DialogClose } from '../../../shared/ui/index.ts';
import { type DiffState, diffFiles, patchLineClass, patchLines, statusMeta, summaryLabel } from '../model.ts';

/** Which session the modal shows; `seq` makes every open a fresh load, even of the same wid. */
type Target = { wid: string; seq: number } | null;
const target = createStore<Target>(null);
let seq = 0;

/** Open the modal on session `wid` (the Files chip's handler). */
export function openDiffModal(wid: string) {
  if (wid) target.set({ wid, seq: ++seq });
}

export function closeDiffModal() {
  target.set(null);
}

const agentPath = (wid: string) => `/api/agents/${encodeURIComponent(wid)}/diff`;

type Patch = { kind: 'idle' } | { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'text'; text: string };

function Empty({ children }: { children: string }) {
  return <div className="diff-patch-empty">{children}</div>;
}

function PatchView({ patch }: { patch: Patch }) {
  if (patch.kind === 'idle') return <Empty>Select a file to view its diff.</Empty>;
  if (patch.kind === 'loading') return <Empty>Loading…</Empty>;
  if (patch.kind === 'error') return <Empty>{patch.message}</Empty>;
  if (!patch.text) return <Empty>No diff text for this file.</Empty>;
  return (
    <pre className="diff-patch-pre">
      {patchLines(patch.text).map((line, i) => (
        // A newline BETWEEN the lines (not inside them), so a copied patch is the patch.
        <Fragment key={i}>
          {i > 0 && '\n'}
          <span className={`diff-patch-line ${patchLineClass(line)}`}>{line}</span>
        </Fragment>
      ))}
    </pre>
  );
}

/** One open of the modal: remounted per open, so nothing from the last session lingers. */
function DiffBody({ wid }: { wid: string }) {
  // undefined: the /diff read is in flight; null: it failed.
  const [state, setState] = useState<DiffState | null | undefined>(undefined);
  const [picked, setPicked] = useState<string | null>(null);
  const [patch, setPatch] = useState<Patch>({ kind: 'idle' });
  // The patch read the view is waiting on; an older one landing late is dropped.
  const patchReq = useRef(0);

  useEffect(() => {
    let live = true;
    api(agentPath(wid)).then(
      (s) => live && setState((s ?? null) as DiffState | null),
      () => live && setState(null),
    );
    return () => {
      live = false;
      patchReq.current += 1;
    };
  }, [wid]);

  function pick(path: string) {
    const req = ++patchReq.current;
    setPicked(path);
    setPatch({ kind: 'loading' });
    const done = (p: Patch) => req === patchReq.current && setPatch(p);
    api(`${agentPath(wid)}/patch?path=${encodeURIComponent(path)}`).then(
      (raw) => {
        const res = raw as { ok?: boolean; error?: string; patch?: string } | null;
        if (!res || res.ok === false) done({ kind: 'error', message: res?.error || 'Could not load diff.' });
        else done({ kind: 'text', text: res.patch || '' });
      },
      () => done({ kind: 'error', message: 'Could not load diff.' }),
    );
  }

  if (state === undefined) return <Empty>Loading…</Empty>;
  if (state === null) return <Empty>Could not load the changed files.</Empty>;
  const files = diffFiles(state);
  return (
    <>
      <div className="diff-modal-head">
        <div className="diff-modal-summary">{summaryLabel(state)}</div>
      </div>
      <div className="diff-modal-body">
        <div className="diff-file-pane">
          {files.length ? (
            <ul className="diff-file-list">
              {files.map((f) => {
                const meta = statusMeta(f.status);
                return (
                  <li
                    key={f.path}
                    className={`diff-file-row${picked === f.path ? ' active' : ''}`}
                    data-diff-file={f.path}
                    tabIndex={0}
                    onClick={() => pick(f.path)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        pick(f.path);
                      }
                    }}
                  >
                    <span className={`diff-status-chip ${meta.cls}`} title={f.status}>
                      {meta.label}
                    </span>
                    <span className="diff-file-path">{f.path}</span>
                    <span className="diff-file-stat">
                      <span className="diff-add">+{f.additions}</span> <span className="diff-del">−{f.deletions}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="diff-file-list-empty">Nothing to show.</div>
          )}
        </div>
        <div className="diff-patch-view">
          <PatchView patch={patch} />
        </div>
      </div>
    </>
  );
}

/** The island: the dialog, open while a session is targeted. */
export function DiffModal() {
  const t = useStore(target);
  return (
    <Dialog open={t !== null} onOpenChange={(open) => !open && closeDiffModal()} title="Changed files" className="modal-diff">
      <DialogClose asChild>
        <button className="task-modal-close" type="button" aria-label="Close">
          <icons.close aria-hidden size={18} />
        </button>
      </DialogClose>
      {t && <DiffBody key={t.seq} wid={t.wid} />}
    </Dialog>
  );
}
