// CMX-221 (CMX-212 P15-P18): the Settings modal's pure half, ported from nav.js's Settings
// region, usage.js, cost.js and concurrencymodel.js. No DOM: the tabs render what these return.
import { humanBytes } from '../resources/model.ts';

export const SETTINGS_TABS = [
  { id: 'general', label: 'General' },
  { id: 'timing', label: 'Timing' },
  { id: 'dispatch', label: 'Dispatch' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'cost', label: 'Cost' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'collaboration', label: 'Collaboration' },
] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number]['id'];
export const isTab = (t: unknown): t is SettingsTab => SETTINGS_TABS.some((x) => x.id === t);

// --- Appearance -------------------------------------------------------------------------
// The theme picker, in order. Each key needs a style.css `body[data-theme=…]` block (except
// `dark`, the :root default) AND a terminal palette in chela/dashboard/term_themes.py:
// tests/test_term_bg_sync.py fails if the three lists drift.
export const THEME_LABELS: Record<string, string> = {
  dark: 'Dark', dim: 'Dim', midnight: 'Midnight', nord: 'Nord',
  gruvbox: 'Gruvbox', solarized: 'Solarized', rose: 'Rosé Pine', warm: 'Warm',
};
// Terminal faces. The keys are stored and mapped to family names by the shim each ttyd page
// gets (app.py _TERM_FONT_PREF_SHIM): keep them in sync with LAT/HEB there. The Latin faces
// are all monospace; of the Hebrew ones only Miriam is (the rest trade the grid for letters).
export const TERM_LATIN_LABELS: Record<string, string> = {
  jetbrains: 'JetBrains Mono',
  firacode: 'Fira Code · ligatures',
  plex: 'IBM Plex Mono',
  source: 'Source Code Pro',
  cascadia: 'Cascadia Code · ligatures',
};
export const TERM_FONT_LABELS: Record<string, string> = {
  miriam: 'Miriam Mono · aligned',
  noto: 'Noto Sans Hebrew · modern',
  heebo: 'Heebo · rounded',
  assistant: 'Assistant · humanist',
  rubik: 'Rubik · rounded',
  frankruhl: 'Frank Ruhl · serif',
  david: 'David Libre · classic',
};
export const TERM_SIZES = ['8', '10', '11', '12', '13', '14', '15', '16', '18'];

/** A label map as Select options, in its order. */
export const optionsOf = (labels: Record<string, string>) => Object.entries(labels).map(([value, label]) => ({ value, label }));

// --- General: agent mode and model --------------------------------------------------------
// The selects list the server's own enums (/api/config), so the UI never offers a value the
// server would reject (it re-validates anyway). Notes only for the documented modes.
const AGENT_MODE_NOTES: Record<string, string> = {
  auto: 'safe ops auto-approved, risky ones gated',
  bypassPermissions: 'no prompts at all',
};

export function agentModeLabel(m: string, dflt: string): string {
  const note = AGENT_MODE_NOTES[m];
  return m + (m === dflt ? ' · built-in default' : '') + (note ? ' · ' + note : '');
}

export function agentModelLabel(m: string, dflt: string): string {
  return m + (m === dflt ? ' · default' : '');
}

/** The /api/config fields the Settings tabs read. */
export type Config = Partial<{
  error: string;
  projects_dir: string;
  projects_dir_effective: string;
  agent_permission_modes: string[];
  agent_permission_mode: string;
  agent_permission_mode_default: string;
  agent_permission_mode_effective: string;
  agent_cmd_overrides: { workflow: string; cmd: string }[];
  agent_models: string[];
  agent_model: string;
  agent_model_default: string;
  agent_model_effective: string;
  upload_max_mb: number;
  telegram_delete_on_close: boolean;
  telegram_archived_topics: number;
  telegram_cleanup_pending: boolean;
  collab_relay: string;
}> & Record<string, unknown>;

export type SwitchKey = 'remote_control' | 'share_typing' | 'file_drop';
export type SwitchState = { on: boolean; locked: boolean; env: string; from: string };

/**
 * One env-wins switch (Remote Control, Guest typing, File drop): its effective value, and
 * whether its env var pins it (then the switch is shown but disabled: an edit would be
 * silently discarded). `from` names the source that won.
 */
export function switchState(cfg: Config | null, key: SwitchKey, envName: string): SwitchState {
  const on = !!cfg?.[key];
  const locked = !!cfg?.[`${key}_env_locked`];
  const env = String(cfg?.[`${key}_env`] || envName);
  const from = locked ? 'env' : cfg?.[`${key}_source`] === 'dashboard' ? 'this setting' : 'the built-in default';
  return { on, locked, env, from };
}

// --- General: the Update card (CMX-199, CMX-56, CMX-95) -------------------------------------
export type UpdateInfo = Partial<{
  ok: boolean;
  error: string;
  note: string;
  behind: number;
  branch: string;
  stale_services: string[];
  unknown_services: string[];
  stale_ok: boolean;
}>;

/** What the Update card shows: the badge, which sentence, and the button (none = hidden). */
export type UpdateView =
  | { kind: 'unknown'; badge: string; detail: string; button: null }
  | { kind: 'behind'; badge: string; branch: string; behind: number; unknown: string[]; button: { mode: 'update'; label: string } }
  | { kind: 'stale'; badge: string; branch: string; stale: string[]; unknown: string[]; button: { mode: 'restart'; label: string } }
  | { kind: 'unreadable'; badge: string; branch: string; button: null }
  | { kind: 'ok'; badge: string; branch: string; unknown: string[]; button: null };

export function updateView(upd: UpdateInfo | null | undefined): UpdateView {
  if (!upd || !upd.ok) {
    return { kind: 'unknown', badge: 'Unknown', detail: upd && (upd.error || upd.note) ? String(upd.error || upd.note) : 'unavailable', button: null };
  }
  const behind = upd.behind || 0;
  const stale = upd.stale_services || [];
  // CMX-56: a service whose start commit could not be read is neither stale nor fresh.
  const unknown = upd.unknown_services || [];
  const branch = upd.branch || '';
  if (behind > 0) return { kind: 'behind', badge: `${behind} behind`, branch, behind, unknown, button: { mode: 'update', label: 'Update now' } };
  // Nothing to pull, but a `git pull` run by hand can leave services on the OLD code. Stale is
  // import-aware (CMX-56): only a service whose own code changed since it started.
  if (stale.length) return { kind: 'stale', badge: `${stale.length} stale`, branch, stale, unknown, button: { mode: 'restart', label: `Restart stale services (${stale.length})` } };
  // CMX-95: which services run old code could not be read: Unknown, never "Up to date".
  if (upd.stale_ok === false) return { kind: 'unreadable', badge: 'Unknown', branch, button: null };
  return { kind: 'ok', badge: 'Up to date', branch, unknown, button: null };
}

/** "runs" / "run", "it" / "they": the stale sentence agrees with its count. */
export function staleWords(n: number): { verb: string; pronoun: string } {
  return n === 1 ? { verb: 'runs', pronoun: 'it' } : { verb: 'run', pronoun: 'they' };
}

// --- Timing and Dispatch: the knob tables (CMX-217, CMX-220) --------------------------------
export type Knob = {
  key: string;
  label: string;
  env?: string;
  unit?: string;
  kind?: 'number' | 'text' | 'size' | 'bool';
  default: unknown;
  stored?: unknown;
  effective: unknown;
  source?: string;
  restart_required?: boolean;
};

/** A knob's stored value as its field shows it ('' = unset: the default shows as placeholder). */
export const storedText = (k: Knob) => (k.stored === '' || k.stored === undefined || k.stored === null ? '' : String(k.stored));

/** The bool select's values. Radix Select has no empty value, so "Default" is a sentinel. */
export const BOOL_DEFAULT = 'default';
export function boolChoice(k: Knob): string {
  if (k.source === 'env') return k.effective === true ? 'true' : k.effective === false ? 'false' : BOOL_DEFAULT;
  if (k.stored === '' || k.stored === undefined || k.stored === null) return BOOL_DEFAULT;
  return k.stored ? 'true' : 'false';
}

/**
 * The batch Save's body: every row the user can edit, keyed by knob. An env-pinned row is
 * never posted, or each Save would persist the env value as a dashboard value nobody chose.
 */
export function knobPayload(knobs: Knob[], drafts: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of knobs) {
    if (k.source === 'env') continue;
    const v = (drafts[k.key] ?? (k.kind === 'bool' ? boolChoice(k) : storedText(k))).trim();
    out[k.key] = v === BOOL_DEFAULT && k.kind === 'bool' ? '' : v;
  }
  return out;
}

/** A rejected batch: every field's error, or "rejected". Nothing in it was saved. */
export function rejection(body: unknown): string {
  const errs = (body as { errors?: Record<string, string> } | null)?.errors;
  return errs ? Object.entries(errs).map(([k, v]) => `${k}: ${v}`).join('; ') : 'rejected';
}

// --- Dispatch: Concurrency (CMX-164) ----------------------------------------------------
export type Concurrency = {
  min: number;
  max: number;
  workflows: { name: string; path: string; effective: number; override: number | null; file_value?: number }[];
  judges: { effective: number; source: string };
  judge_peak_bytes: number;
  available_bytes: number | null;
  available_source?: string;
};

/** The POST body for one stepper click. delta 0 is Reset (clear the override); any other delta
 * steps the effective value, clamped to the server's bounds. */
export function stepPayload(state: Concurrency, kind: 'agents' | 'judges', idx: number, delta: number) {
  const w = state.workflows[idx];
  const cur = kind === 'judges' ? state.judges.effective : (w?.effective ?? 0);
  const value = delta === 0 ? '' : Math.min(state.max, Math.max(state.min, cur + delta));
  return kind === 'judges' ? { judges: value } : { workflow: w?.path, value };
}

/** What N at once means for memory: N x the measured judge peak, next to what is free. */
export function memNote(state: Concurrency, n: number): string {
  const free = state.available_bytes == null ? 'unknown' : `${humanBytes(state.available_bytes)} (${state.available_source})`;
  return `Memory: up to ${n} × ${humanBytes(state.judge_peak_bytes)} judge peak = ${humanBytes(n * state.judge_peak_bytes)}. Available now: ${free}.`;
}

export function workflowNote(state: Concurrency, w: Concurrency['workflows'][number]): string {
  const src = w.override == null ? 'From WORKFLOW.md' : `Override (WORKFLOW.md says ${w.file_value})`;
  return `${src}. ${memNote(state, w.effective)}`;
}

// --- Cost: $ by session (CMX-287) ---------------------------------------------------------
export const COST_WINDOWS = ['live', 'today', '7d', '30d'] as const;
export const COST_VIEWS = ['cost', 'usage'] as const;
export const USAGE_WINDOWS = ['30m', 'today'] as const;

export type CostRow = { name: string; project: string; model?: string | null; cost_usd: number | null | undefined };
export type CostProject = { name: string; agents: CostRow[]; total: number; known: boolean };

export const fmtCost = (v: number | null | undefined) => (v == null ? '-' : '$' + v.toFixed(2));

/** The project the sidebar groups a session under: its cwd's basename. */
export function projectOf(cwd: unknown): string | null {
  if (!cwd) return null;
  const parts = String(cwd).replace(/\/+$/, '').split('/');
  return parts[parts.length - 1] || null;
}

/** /api/cost joined with /api/agents' cwd (which project): the coerced rows. Either payload
 * may be a bare `{}` from a flaky endpoint, never a throw. */
export function costRows(agents: unknown, cost: unknown): CostRow[] {
  const cwd: Record<string, unknown> = {};
  for (const a of Array.isArray(agents) ? (agents as { name: string; cwd?: unknown }[]) : []) cwd[a.name] = a.cwd;
  return (Array.isArray(cost) ? (cost as { name: string; model?: string; cost_usd?: number | null }[]) : []).map((c) => ({
    name: c.name, model: c.model, cost_usd: c.cost_usd, project: projectOf(cwd[c.name]) || '(unknown)',
  }));
}

/** Projects by spend (heaviest first), each with its agents by spend; a null cost is unknown,
 * never $0. `fleet` is null when no row has a known cost. */
export function costTable(rows: CostRow[]): { projects: CostProject[]; fleet: number | null } {
  const by: Record<string, CostRow[]> = {};
  for (const r of rows) (by[r.project] ??= []).push(r);
  const projects = Object.entries(by).map(([name, list]) => {
    const agents = list.slice().sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0));
    return { name, agents, total: agents.reduce((s, a) => s + (a.cost_usd || 0), 0), known: agents.some((a) => a.cost_usd != null) };
  }).sort((a, b) => b.total - a.total);
  const known = rows.filter((r) => r.cost_usd != null);
  return { projects, fleet: known.length ? known.reduce((s, r) => s + (r.cost_usd as number), 0) : null };
}

// --- Cost: tokens by window (CMX-38) ------------------------------------------------------
/** 1234 → "1.2k", 3400000 → "3.4M". */
export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return '-';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

/** How long until `epoch` (seconds): "2h 5m", "3d 4h", "now". */
export function fmtWhen(epoch: number | null | undefined, nowSec = Date.now() / 1000): string {
  if (epoch == null) return '';
  const s = Math.round(epoch - nowSec);
  if (s <= 0) return 'now';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  return h ? `${h}h ${m}m` : `${m}m`;
}

export type Limit = Partial<{ used_pct: number | null; reason: string; resets_at: number; burn_pct_per_h: number | null; hits_100_before_reset: boolean; projected_pct: number }>;

/** One limit bar. `used_pct == null` is UNKNOWN (missing or stale): no fill and the reason,
 * never an empty 0% bar. */
export function limitView(e: Limit | null | undefined) {
  const entry = e || {};
  if (entry.used_pct == null) return { unknown: true as const, reason: entry.reason || 'no data' };
  const pct = Math.max(0, Math.min(100, entry.used_pct));
  return {
    unknown: false as const,
    used: entry.used_pct,
    pct,
    level: pct >= 80 ? 'high' : pct >= 60 ? 'warn' : '',
    burn: entry.burn_pct_per_h == null ? 'burn n/a' : `burn ${entry.burn_pct_per_h.toFixed(1)}%/h`,
    // true: the burn reaches 100% before the reset; false: where it lands; undefined: no projection
    reaches: entry.hits_100_before_reset,
    projected: entry.projected_pct,
    resetsAt: entry.resets_at,
  };
}

/** A sparkline's bars in a 60×16 box (zero draws nothing, any activity at least 1px). */
export function sparkBars(values: unknown): { x: string; y: string; w: string; h: string }[] {
  const v = Array.isArray(values) ? (values as number[]) : [];
  if (!v.length) return [];
  const max = Math.max(...v, 1), w = 60, h = 16, bw = w / v.length;
  return v.map((x, i) => {
    const bh = x > 0 ? Math.max(1, (x / max) * h) : 0;
    return { x: (i * bw).toFixed(2), y: (h - bh).toFixed(2), w: Math.max(1, bw - 1).toFixed(2), h: bh.toFixed(2) };
  });
}

export const hitText = (hit: number | null | undefined) => (hit == null ? '-' : Math.round(hit * 100) + '%');

export const ROOT_SOURCES: Record<string, string> = { env: 'CHELA_USAGE_EXTRA_ROOTS', config: '~/.chela/config.json', default: 'default' };

// --- Search (CMX-396) -----------------------------------------------------------------
export const searchNorm = (s: unknown) => String(s || '').replace(/\s+/g, ' ').toLowerCase();
/** The query's words: every one must match (AND). */
export const searchTerms = (q: string) => searchNorm(q).trim().split(' ').filter(Boolean);
export const searchMatches = (text: string, terms: string[]) => {
  const t = searchNorm(text);
  return terms.every((q) => t.includes(q));
};
export const countText = (n: number, q: string) => (n ? `${n} setting${n === 1 ? '' : 's'}` : `No settings match '${q.trim()}'`);
