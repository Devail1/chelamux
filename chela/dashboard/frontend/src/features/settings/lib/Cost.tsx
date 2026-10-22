// CMX-221 (CMX-212 P18): the Cost tab, two views. Cost (CMX-287): fleet $ from the cost each
// agent's statusLine reports, by project (the sidebar's grouping: the cwd's basename), for a
// window. Usage (CMX-38): tokens, read from EVERY Claude Code transcript (judges, subagents,
// background sessions, extra roots), so a session with no statusLine still shows, with the 5h
// and 7d limits. Ported from cost.js and usage.js. Each view loads when it is shown.
import { useEffect, useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { TextButton } from '../../../shared/ui/index.ts';
import {
  COST_VIEWS, COST_WINDOWS, ROOT_SOURCES, USAGE_WINDOWS, costRows, costTable, fmtCost, fmtTokens, fmtWhen, hitText, limitView, sparkBars,
  type CostRow, type Limit,
} from '../model.ts';
import { KEYS } from './prefs.ts';
import { Section } from './parts.tsx';
import { settings } from './state.ts';
import { lsChoice, lsSet } from './storage.ts';

/** A segmented control: one pressed button of several, its choice kept per browser. */
function Seg<T extends string>({ id, label, cls, attr, choices, names, value, onChange }: { id: string; label: string; cls: string; attr: string; choices: readonly T[]; names: Record<T, string>; value: T; onChange: (v: T) => void }) {
  return (
    <div className="work-toolbar">
      <div className="work-seg" id={id} role="group" aria-label={label}>
        {choices.map((c) => (
          <TextButton key={c} className={`work-seg-btn ${cls}${c === value ? ' active' : ''}`} {...{ [`data-${attr}`]: c }}
            aria-pressed={c === value} onClick={() => onChange(c)}>{names[c]}</TextButton>
        ))}
      </div>
    </div>
  );
}

function usePersisted<T extends string>(key: string, valid: readonly T[], dflt: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => lsChoice(key, valid, dflt));
  return [v, (next) => { lsSet(key, next); setV(next); }];
}

function CostTable({ rows }: { rows: CostRow[] | 'failed' | null }) {
  if (rows === null) return <div className="s-desc">Loading…</div>;
  if (rows === 'failed') return <div className="side-empty">Cost data unavailable.</div>;
  if (!rows.length) {
    return <div className="side-empty">No cost data yet. Install the statusLine hook (<code>chela install-statusline</code>) so agents report session cost.</div>;
  }
  const { projects, fleet } = costTable(rows);
  return (
    <table className="cost-table">
      <thead><tr><th>Project</th><th>Agent</th><th>Model</th><th className="num">Cost</th></tr></thead>
      {projects.map((p) => (
        <tbody className="cost-project" key={p.name}>
          <tr className="cost-project-row"><td colSpan={3}>{p.name}</td><td className="num">{p.known ? fmtCost(p.total) : '-'}</td></tr>
          {p.agents.map((a, i) => (
            <tr className="cost-agent-row" key={`${a.name}:${i}`}><td></td><td>{a.name}</td><td>{a.model || '-'}</td><td className="num">{fmtCost(a.cost_usd)}</td></tr>
          ))}
        </tbody>
      ))}
      <tfoot><tr className="cost-total-row"><td colSpan={3}>Fleet total</td><td className="num">{fmtCost(fleet)}</td></tr></tfoot>
    </table>
  );
}

function CostView({ shown, active, nonce }: { shown: boolean; active: boolean; nonce: number }) {
  const { epoch } = useStore(settings);
  const [win, setWin] = usePersisted(KEYS.costWindow, COST_WINDOWS, 'live');
  const [rows, setRows] = useState<CostRow[] | 'failed' | null>(null);
  const [again, setAgain] = useState(0);
  useEffect(() => {
    if (!active) return;
    let live = true;
    Promise.all([api('/api/agents'), api(`/api/cost?window=${win}`)]).then(
      ([agents, cost]) => { if (live) setRows(costRows(agents, cost)); },
      () => { if (live) setRows('failed'); },
    );
    return () => { live = false; };
  }, [active, win, epoch, nonce, again]);
  return (
    <div id="cost-pane" hidden={!shown}>
      <p className="s-desc">Fleet spend from the cost each agent's statusLine hook already reports
        (<code>cost.total_cost_usd</code>), with no separate accounting, just a read over data chela ingests anyway. Grouped
        by project, same convention the sidebar uses for "what project is this agent".</p>
      <Seg id="cost-window" label="Cost window" cls="cost-window-btn" attr="win" choices={COST_WINDOWS}
        names={{ live: 'Live', today: 'Today', '7d': '7d', '30d': '30d' }} value={win} onChange={(w) => { setWin(w); setAgain((n) => n + 1); }} />
      <div id="cost-table"><CostTable rows={rows} /></div>
    </div>
  );
}

// One limit bar: an unknown sample says so (no fill, the reason), never an empty 0%.
function LimitBar({ label, e }: { label: string; e: Limit | null | undefined }) {
  const v = limitView(e);
  if (v.unknown) {
    return (
      <div className="usage-limit unknown" data-limit={label}>
        <div className="usage-limit-head"><span>{label}</span><span className="usage-limit-val">unknown</span></div>
        <div className="usage-bar" role="img" aria-label={`${label}: unknown`}><div className="usage-bar-fill" style={{ width: 0 }} /></div>
        <div className="usage-limit-sub">{v.reason}</div>
      </div>
    );
  }
  return (
    <div className={`usage-limit ${v.level}`.trim()} data-limit={label}>
      <div className="usage-limit-head"><span>{label}</span><span className="usage-limit-val">{v.used}%</span></div>
      <div className="usage-bar" role="img" aria-label={`${label}: ${v.used}% used`}><div className="usage-bar-fill" style={{ width: `${v.pct}%` }} /></div>
      <div className="usage-limit-sub">
        <span>resets in {fmtWhen(v.resetsAt)}</span><span>{v.burn}</span>
        {v.reaches === true && <span className="usage-flag">▲ REACHES 100% BEFORE RESET</span>}
        {v.reaches === false && <span>projected {v.projected}% at reset</span>}
      </div>
    </div>
  );
}

type UsageRow = Partial<{ label: string; session_id: string; ai_title: string; model: string; requests: number; input: number; cache_write: number; cache_read: number; output: number; weighted: number; cache_hit: number | null; cache_broken: boolean; spark: number[] }>;
type Usage = { limits: { five_hour?: Limit; seven_day?: Limit }; windows?: Record<string, { rows?: UsageRow[] }>; roots?: { extra?: string[]; source?: string; scanned?: string[] } | null };

function Spark({ values }: { values: unknown }) {
  const bars = sparkBars(values);
  if (!bars.length) return null;
  return (
    <svg className="usage-spark" viewBox="0 0 60 16" width="60" height="16" aria-hidden="true">
      {bars.map((b, i) => <rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} />)}
    </svg>
  );
}

function UsageTable({ rows }: { rows: UsageRow[] | 'failed' | null }) {
  if (rows === null) return <div className="s-desc">Loading…</div>;
  if (rows === 'failed') return <div className="side-empty">Usage data unavailable.</div>;
  if (!rows.length) return <div className="side-empty">No Claude Code requests in this window.</div>;
  return (
    <div className="usage-scroll">
      <table className="cost-table usage-table">
        <thead><tr><th>Session</th><th>Model</th><th className="num">Req</th><th className="num">Input</th>
          <th className="num">Cache write</th><th className="num">Cache read</th><th className="num">Output</th>
          <th className="num" title="input×1 + cache write×1.25 + cache read×0.1 + output×5">Weighted</th>
          <th className="num">Cache hit</th><th>Activity</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.session_id ?? ''}:${i}`} className={r.cache_broken ? 'usage-row broken' : 'usage-row'}>
              <td>{r.label || r.session_id || ''}{r.ai_title && <div className="usage-sub">{r.ai_title}</div>}</td>
              <td>{r.model || '-'}</td>
              <td className="num">{r.requests}</td>
              <td className="num">{fmtTokens(r.input)}</td>
              <td className="num">{fmtTokens(r.cache_write)}</td>
              <td className="num">{fmtTokens(r.cache_read)}</td>
              <td className="num">{fmtTokens(r.output)}</td>
              <td className="num usage-weighted">{fmtTokens(r.weighted)}</td>
              {/* flagged as a WORD plus a SHAPE, not colour alone */}
              <td className="num usage-hit">{hitText(r.cache_hit)}{r.cache_broken && <> <span className="usage-flag" title="cache hit under 50% over ≥10 requests and >1M tokens">▲ CACHE BROKEN</span></>}</td>
              <td><Spark values={r.spark} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

async function loadUsage(): Promise<Usage | 'failed'> {
  try {
    const p = (await api('/api/usage')) as (Usage & { error?: string }) | null;
    return p && typeof p === 'object' && !p.error && p.limits ? p : 'failed';
  } catch {
    return 'failed';
  }
}

function UsageView({ shown, active, nonce }: { shown: boolean; active: boolean; nonce: number }) {
  const { epoch } = useStore(settings);
  const [win, setWin] = usePersisted(KEYS.usageWindow, USAGE_WINDOWS, '30m');
  const [data, setData] = useState<Usage | 'failed' | null>(null);
  useEffect(() => {
    if (!active) return;
    let live = true;
    // a failure drops the last payload: the bars go UNKNOWN, never its stale %
    void loadUsage().then((d) => { if (live) setData(d); });
    return () => { live = false; };
  }, [active, epoch, nonce]);
  const ok = data && data !== 'failed' ? data : null;
  const rows = data === null ? null : !ok ? 'failed' : (ok.windows?.[win]?.rows ?? []);
  const roots = ok?.roots;
  return (
    <div id="usage-pane" hidden={!shown}>
      <p className="s-desc">Tokens, not dollars, read from EVERY Claude Code transcript: judges, subagents, dispatched
        agents, background sessions, and any extra root listed below, so a session with no statusLine still shows up. Limits
        come from the freshest statusLine <code>rate_limits</code>.</p>
      <div className="usage-limits" id="usage-limits">
        <LimitBar label="5h" e={ok?.limits.five_hour} />
        <LimitBar label="7d" e={ok?.limits.seven_day} />
      </div>
      <Seg id="usage-window" label="Usage window" cls="usage-window-btn" attr="win" choices={USAGE_WINDOWS}
        names={{ '30m': 'Last 30 min', today: 'Today (UTC)' }} value={win} onChange={setWin} />
      <div id="usage-table"><UsageTable rows={rows} /></div>
      <p className="s-desc">Transcript roots: this host's own Claude Code projects dir plus the extra roots set by{' '}
        <code>usage_extra_roots</code> in <code>~/.chela/config.json</code> or <code>CHELA_USAGE_EXTRA_ROOTS</code> (absolute
        globs; <code>[]</code> scans none).</p>
      {/* read-only: the roots are a config setting, never written from here */}
      <dl className="usage-roots" id="usage-roots">
        {roots && (
          <>
            <dt>Extra roots{roots.source ? ` (${ROOT_SOURCES[roots.source] || roots.source})` : ''}</dt><dd>{(roots.extra || []).join(', ') || 'none'}</dd>
            <dt>Scanning</dt><dd>{(roots.scanned || []).join(', ') || 'nothing'}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

export function Cost() {
  const s = useStore(settings);
  const [view, setView] = usePersisted(KEYS.costView, COST_VIEWS, 'cost');
  // A click on a segment re-reads its view, the one already shown too.
  const [nonce, setNonce] = useState(0);
  const shown = s.tab === 'cost';
  return (
    <Section title="Cost" id="settings-cost" keywords="spend money usd dollars budget billing tokens usage limit rate cache">
      <Seg id="cost-view" label="Cost or usage" cls="cost-view-btn" attr="view" choices={COST_VIEWS}
        names={{ cost: 'Cost', usage: 'Usage' }} value={view} onChange={(v) => { setView(v); setNonce((n) => n + 1); }} />
      {/* a view loads only while the Cost tab shows it */}
      <CostView shown={view === 'cost'} active={shown && view === 'cost'} nonce={nonce} />
      <UsageView shown={view === 'usage'} active={shown && view === 'usage'} nonce={nonce} />
    </Section>
  );
}
