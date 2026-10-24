// CMX-276 (+ CMX-121's row redesign): what one sidebar row SHOWS, as data. The title gets
// the width; state is the dot's SHAPE (Liav is red-weak), its word only in the dot's
// aria-label and the row's tooltip; metadata is a short list of icon + text + tooltip.
import { headLabel, type HeadAgent } from '../../panehead/model.ts';
import {
  type AgentRow, type Battery, type Item, type SidebarState, itemLabel, primaryWindow, runDot, runState,
} from '../model.ts';

export type MetaKind = 'orchestrator' | 'pr' | 'judging' | 'context' | 'cwd';

export interface Meta {
  kind: MetaKind;
  text: string;
  title: string;
  cls: string;
}

export interface RowView {
  agent: AgentRow;
  label: string;
  /** The window name, when the title is something else (CMX-62). */
  windowKey: string;
  runId: string | null;
  dotCls: string;
  dotLabel: string;
  word: string;
  meta: Meta[];
  title: string;
  onWall: boolean;
}

/** Context % is shown from this much used (CMX-121); the tooltip always has it. */
export const CONTEXT_SHOWN_FROM = 50;

const PR_NUM_RE = /\/pull\/(\d+)/;
const PR_STATES = ['open', 'draft', 'merged', 'closed'];

/** `3m`, `2h`, `5d`: how long ago, for the tooltip. */
export function ago(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

// A human session's state. `done` (issue #475) is the server's word (inbox.is_done) and
// is only ever set on an idle window, so it never fights working / waiting.
function sessionState(a: AgentRow, s: SidebarState): string {
  if (a.done) return 'done';
  if (s.wants(a)) return 'waiting';
  return a.session_status === 'busy' ? 'working' : 'idle';
}

function testingBattery(it: Item): Battery | null {
  const a = [it.judge, ...it.windows].find(w => w?.judge_battery?.state === 'testing');
  return a?.judge_battery ?? null;
}

function prMeta(it: Item): Meta | null {
  const url = it.run?.pr_url;
  const m = url ? PR_NUM_RE.exec(String(url)) : null;
  if (!m) return null;
  const pr = it.windows.map(w => w.pr).find(p => p && p.url === url) ?? null;
  const state = pr ? (pr.draft && pr.state === 'open' ? 'draft' : pr.state ?? '') : '';
  const known = PR_STATES.includes(state);
  return {
    kind: 'pr', text: `#${m[1]}`, cls: known ? `ar-pr ${state}` : 'ar-pr',
    title: `PR #${m[1]}${known ? ` · ${state}` : ''}`,
  };
}

function metaOf(it: Item, a: AgentRow, s: SidebarState): Meta[] {
  const out: Meta[] = [];
  if (a.window_id && a.window_id === s.orchWid) {
    out.push({ kind: 'orchestrator', text: '', cls: 'ar-orch', title: 'Orchestrator: holds the decisions inbox' });
  }
  // CMX-330: a plain shell's cwd, the subtitle its pane header shows beside `Shell`.
  const head = it.kind === 'run' ? null : headLabel(a as HeadAgent, a.window_id ?? '');
  if (head?.cwd && head.subtitle) out.push({ kind: 'cwd', text: head.subtitle, cls: 'ar-cwd', title: `Shell in ${head.subtitle}` });
  const pr = it.kind === 'run' && s.view.showPR ? prMeta(it) : null;
  if (pr) out.push(pr);
  const b = testingBattery(it);
  if (b) {
    const where = typeof b.total === 'number' ? `${b.done ?? 0}/${b.total}` : b.phase || 'setup';
    // the label is `⚖️ testing · 3/6 · 15m`: its words, after the glyph, carry the time
    const label = String(b.label ?? '').replace(/^\S+\s+/, '');
    out.push({ kind: 'judging', text: where, cls: 'ar-judge', title: `Judge ${label || `testing · ${where}`}` });
  }
  const p = a.window_id ? s.ctx[a.window_id] : undefined;
  if (p != null && Math.round(p) >= CONTEXT_SHOWN_FROM) {
    const pct = Math.round(p);
    const tone = pct > 80 ? ' danger' : pct > 60 ? ' warn' : '';
    out.push({ kind: 'context', text: `${pct}%`, cls: `ar-ctx${tone}`, title: `Context ${pct}% used` });
  }
  return out;
}

/** The row an agent or run item draws, or null when it has no window to open. */
export function rowView(it: Item, s: SidebarState): RowView | null {
  const a = primaryWindow(it);
  if (!a) return null;
  const run = it.kind === 'run';
  const label = itemLabel(it, s.labels);
  let word: string, dotCls: string, dotLabel: string;
  if (run) {
    word = runState(it, s.wants).word;
    const dot = runDot(it, s.wants);
    dotCls = dot.cls;
    dotLabel = dot.label;
  } else {
    word = sessionState(a, s);
    dotCls = word;
    dotLabel = `Session: ${word}`;
  }
  const meta = metaOf(it, a, s);
  const windowKey = !run && a.name && label !== a.name ? a.name : '';
  const head = run
    ? `${label}: ${word}`
    : [label + (windowKey ? ` (${windowKey})` : ''), a.window_id].filter(Boolean).join(' · ');
  const extra = run
    ? [it.agent && `agent ${it.agent.name} · ${it.agent.window_id}`,
      it.judge && `judge ${it.judge.name} · ${it.judge.window_id}`].filter(Boolean).join('\n')
    : [a.ai_title, a.recap].filter(Boolean).join(', ');
  const ts = a.recap_ts ? new Date(a.recap_ts).getTime() : NaN;
  const age = Number.isFinite(ts) ? `${ago(Math.max(0, (s.now - ts) / 1000))} ago` : '';
  const onWall = !!a.window_id && s.onWall.includes(a.window_id);
  const facts = [run ? '' : word, ...meta.map(m => m.title)].filter(Boolean).join(' · ');
  const title = [head, facts, extra, age].filter(Boolean).join('\n') + (onWall ? ' (open on the wall)' : '');
  return {
    agent: a, label, windowKey, runId: run ? it.run?.task_id ?? null : null,
    dotCls, dotLabel, word, meta, title, onWall,
  };
}
