// CMX-231: the HTML-string forms of a run's PR link and task-progress chip, for the two legacy
// renderers that still build cards as strings (kanban.js, taskmodal.js). The Runs tables draw
// the same things as React (lib/Runs.tsx). Deleted when those renderers are ported (P09, P12).
import type { TaskProgress } from '../../shared/store/work.ts';
import { prLabel, taskProgress } from './model.ts';

const ESC: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ESC[c] ?? c);

export function runPrCellHtml(prUrl: string | null | undefined): string {
  if (!prUrl) return '';
  const url = esc(String(prUrl));
  return `<a class="pr-badge" href="${url}" target="_blank" rel="noopener noreferrer" title="${url}">${esc(prLabel(String(prUrl)))}</a>`;
}

/** `icon` is the caller's own icon markup (util.js lucideIcon), drawn before the label. */
export function taskProgressChipHtml(tasks: TaskProgress | null | undefined, icon = ''): string {
  const p = taskProgress(tasks);
  return p ? `<span class="task-progress-chip" title="${esc(p.title)}">${icon} ${esc(p.label)}</span>` : '';
}
