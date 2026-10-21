// PER-SESSION DIFF MODEL (CMX-299; ported by CMX-237): pure helpers for the changed-files
// modal a wall tile's "Files" chip opens. No DOM: lib/DiffModal.tsx renders; this file turns
// the raw /api/agents/<wid>/diff response and a patch's line text into what the modal shows.

/** One changed file, as chela.diffsurface reports it. */
export interface DiffFile {
  path: string;
  status: string;
  additions: number;
  deletions: number;
}

/** The /api/agents/<wid>/diff response. */
export interface DiffState {
  is_git?: boolean;
  has_head?: boolean;
  files?: DiffFile[];
  additions?: number;
  deletions?: number;
}

export interface StatusMeta {
  label: string;
  cls: string;
}

// Mirrors diffsurface.py's `_STATUS_NAMES` plus `untracked`, the one status diffsurface mints
// itself. An unknown status (a future diffsurface addition) falls back to '?', never throws.
const STATUS_META: Record<string, StatusMeta> = {
  added: { label: 'A', cls: 'diff-status-added' },
  modified: { label: 'M', cls: 'diff-status-modified' },
  deleted: { label: 'D', cls: 'diff-status-deleted' },
  untracked: { label: 'U', cls: 'diff-status-untracked' },
  conflicted: { label: '!', cls: 'diff-status-conflicted' },
};

export function statusMeta(status: string): StatusMeta {
  return (Object.hasOwn(STATUS_META, status) && STATUS_META[status]) || { label: '?', cls: 'diff-status-unknown' };
}

/** The files a state lists; anything but an array reads as none. */
export function diffFiles(state: DiffState | null | undefined): DiffFile[] {
  return state && Array.isArray(state.files) ? state.files : [];
}

/** The modal's one-line header. Each branch is a distinct real state, so the header never
 * says "No changes" when the truth is "not a git repo" or "no commits yet". */
export function summaryLabel(state: DiffState | null | undefined): string {
  if (!state || !state.is_git) return 'Not a git repository';
  if (!state.has_head) return 'No commits yet';
  const n = diffFiles(state).length;
  if (!n) return 'No changes';
  return `${n} file${n === 1 ? '' : 's'} changed · +${state.additions ?? 0} −${state.deletions ?? 0}`;
}

/** The CSS class of one patch line. `+++`/`---` file headers start with the same character
 * as added/removed lines, so they are checked first. */
export function patchLineClass(line: unknown): string {
  if (typeof line !== 'string') return 'diff-line-ctx';
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-line-meta';
  if (line.startsWith('@@')) return 'diff-line-hunk';
  if (line.startsWith('+')) return 'diff-line-add';
  if (line.startsWith('-')) return 'diff-line-del';
  return 'diff-line-ctx';
}

/** A patch's lines, without the one trailing newline git ends it with. */
export function patchLines(patch: string): string[] {
  return patch.replace(/\n$/, '').split('\n');
}
