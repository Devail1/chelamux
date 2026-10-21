// Init a repo: seed a starter WORKFLOW.md + TODO.md via POST /api/dispatcher/init (the server's
// starter.seed_repo, which never overwrites). The path field suggests the launcher's known
// project dirs. Ported from static/js/dispatcher.js by CMX-231.
import { useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { refreshWork } from '../../../shared/store/work.ts';
import { Button, Dialog, DialogClose, FieldRow, TextInput } from '../../../shared/ui/index.ts';

type Launcher = { favorites?: ({ path?: string } | null)[]; recent?: ({ path?: string } | null)[] } | null | undefined;
type InitResponse = { ok?: boolean; error?: string; path?: string; created?: string[]; skipped?: string[]; is_git?: boolean };

/** The open dialog's path suggestions; null while closed. */
const suggestions = createStore<string[] | null>(null);

/** The launcher's favorites then recent dirs, each once. */
export function launcherPaths(launcher: Launcher): string[] {
  const all = [...(launcher?.favorites || []), ...(launcher?.recent || [])].map((e) => e?.path);
  return [...new Set(all.filter((p): p is string => !!p))];
}

/** Open the dialog (the toolbar's "+ Init a repo"); `launcher` is launcher.js's data. */
export function openInitRepo(launcher?: Launcher) {
  suggestions.set(launcherPaths(launcher));
}

export type InitLine = { kind: 'created' | 'skipped' | 'warn' | 'next'; text: string };

/** What a seed answered, one line each, as the dialog shows it (each kind has its icon). */
export function initReport(res: InitResponse): InitLine[] {
  const lines: InitLine[] = [];
  if (res.created?.length) lines.push({ kind: 'created', text: `Created ${res.created.join(' + ')}` });
  if (res.skipped?.length) lines.push({ kind: 'skipped', text: `Skipped (already exist): ${res.skipped.join(' + ')}` });
  if (!res.is_git) lines.push({ kind: 'warn', text: 'Not a git repo. The dispatcher needs one to branch & open PRs.' });
  lines.push({ kind: 'next', text: `Next: add ${res.path}/WORKFLOW.md to CHELA_DISPATCH_WORKFLOWS and restart the daemon, or run:  chela dispatch ${res.path}/WORKFLOW.md` });
  return lines;
}

const LINE_ICON = { created: icons.done, skipped: icons.skipped, warn: icons.warn, next: null };

type Result = { cls: '' | 'ok' | 'err'; text: string | InitLine[] };

function InitForm({ paths }: { paths: string[] }) {
  const [path, setPath] = useState('');
  const [result, setResult] = useState<Result>({ cls: '', text: '' });
  async function seed() {
    const p = path.trim();
    if (!p) return setResult({ cls: 'err', text: 'Enter a repo path.' });
    setResult({ cls: '', text: 'Seeding…' });
    let res: InitResponse | null;
    try {
      res = (await api('/api/dispatcher/init', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: p }) })) as InitResponse | null;
    } catch {
      return setResult({ cls: 'err', text: 'Request failed.' });
    }
    if (!res || !res.ok) return setResult({ cls: 'err', text: res?.error || 'Failed.' });
    setResult({ cls: 'ok', text: initReport(res) });
    void refreshWork();
  }
  return (
    <>
      <FieldRow label="Repo path">
        <TextInput value={path} onChange={setPath} placeholder="/home/you/projects/my-repo" list="init-suggestions" autoFocus />
      </FieldRow>
      <datalist id="init-suggestions">{paths.map((p) => <option key={p} value={p} />)}</datalist>
      <div className={`init-result ${result.cls}`.trim()} role="status">
        {typeof result.text === 'string'
          ? result.text
          : result.text.map((l) => {
              const Icon = LINE_ICON[l.kind];
              return (
                <div key={l.kind} className={`init-line init-${l.kind}`}>
                  {Icon && <Icon aria-hidden size={12} />} {l.text}
                </div>
              );
            })}
      </div>
      <div className="modal-actions">
        <DialogClose asChild><Button>Cancel</Button></DialogClose>
        <Button variant="primary" onClick={() => void seed()}>Seed files</Button>
      </div>
    </>
  );
}

export function InitRepo() {
  const paths = useStore(suggestions);
  return (
    <Dialog
      open={paths !== null}
      onOpenChange={(open) => !open && suggestions.set(null)}
      title="Init a repo for the dispatcher"
      description={<>Writes a starter <code>WORKFLOW.md</code> + <code>TODO.md</code> into the repo (existing files are left untouched). Then point chela at it.</>}
    >
      {/* remounted per open: a fresh path and no stale result */}
      {paths && <InitForm paths={paths} />}
    </Dialog>
  );
}
