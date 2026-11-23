// CMX-242 (CMX-212 P27): what a pane's footer SAYS, as a straight function of its
// /api/agents/context row. No DOM, no fetch. From terminals.js `_applyTermContext`.
import { costView, ctxLevel } from '../../../../static/js/wallmodel.ts';
import type { ContextRow } from '../../shared/store/context.ts';

/** The context bar's reading, or null when there is none (every chip then hides). */
export interface CtxView {
  /** The fill strip's width, `N%` capped at 100. */
  width: string;
  /** `ctx-warn` / `ctx-danger` past 60% / 80% (wallmodel `ctxLevel`), else ''. */
  sev: string;
  /** A transcript estimate, not the statusline's exact count: the fill reads dimmed. */
  estimated: boolean;
  /** The chip's text, `74% · 147.5K/1M`: the number, never colour alone, carries it. */
  chip: string;
  /** The bar's tooltip (mirrored onto the header, a hoverable surface). */
  tip: string;
  model: string | null;
  cost: string | null;
  branch: string | null;
}

/** The bar's tooltip while there is no reading. */
export const NO_CONTEXT = 'Context: --';

/** CMX-475: the tooltip line for a reading whose window was inferred, not stated. */
function estimateNote(c: ContextRow): string {
  return c.window_source === 'usage' ? 'window inferred as 1M from usage past 200K'
    : c.window_source === 'config' ? 'window from the configured default'
    : 'window size estimated';
}

/** A chip with no fill and no severity: the states that carry no %. */
function bare(c: ContextRow, chip: string, tip: string): CtxView {
  const bits = [tip];
  if (c.model) bits.push(c.model);
  if (c.cost_usd != null) bits.push(`$${c.cost_usd}`);
  return {
    width: '0%', sev: '', estimated: false, chip, tip: bits.join(' · '),
    model: c.model || null, cost: costView(c.cost_usd), branch: c.branch || null,
  };
}

export function ctxView(c: ContextRow | null | undefined): CtxView | null {
  if (!c) return null;
  // CMX-475: no turn yet reads "—", never 0%; after /compact the old % is stale and is
  // not shown; an unknown window shows the absolute tokens with no %.
  if (c.state === 'no_turns') return bare(c, '—', 'Context: — (no turn yet)');
  if (c.state === 'compacted') {
    const was = c.pre ? ` (was ${String(c.pre)})` : '';
    return bare(c, 'compacted', `Context: compacted${was}; the real % shows after the next turn`);
  }
  if (c.used_pct == null) {
    if (!c.used) return null;
    return bare(c, String(c.used), `Context: ${String(c.used)} tokens · window size unknown`);
  }
  const pct = c.used_pct;
  const est = !!c.estimated;
  const lvl = ctxLevel(pct);
  const bits = [`Context: ${String(c.used)}/${String(c.total)} (${pct}%${est ? '~' : ''})`];
  if (c.model) bits.push(c.model);
  if (c.cost_usd != null) bits.push(`$${c.cost_usd}`);
  if (est) bits.push(estimateNote(c));
  const counter = c.used && c.total ? ` · ${String(c.used)}/${String(c.total)}` : '';
  return {
    width: `${Math.min(100, pct)}%`,
    sev: lvl === 'bad' ? 'ctx-danger' : lvl === 'warn' ? 'ctx-warn' : '',
    estimated: est,
    chip: `${pct}%${est ? '~' : ''}${counter}`,
    tip: bits.join(' · '),
    model: c.model || null,
    cost: costView(c.cost_usd),
    branch: c.branch || null,
  };
}
