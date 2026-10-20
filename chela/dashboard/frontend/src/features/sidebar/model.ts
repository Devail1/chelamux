// --- The sidebar's grouping model (CMX-35) — pure: no DOM, no storage --------
//
// The sidebar groups sessions the way the Claude desktop app's Code sidebar does: by
// PROJECT FOLDER (the session's cwd), with a quiet header per folder. This module is
// the math behind that — which item lands in which group, what a group is called, which
// rows "Archive all" may hide, what a dispatched run's state reads as — so it can be
// driven by a node test without a browser. nav.js renders what it returns.
//
// An ITEM is one sidebar row. A plain window is one item. A DISPATCHED RUN is one item
// too, however many windows it has: its agent window and its judge window collapse
// into ONE row (`CMX-37 · <title>`); the judge is a state of the run, not a row of its
// own. The run each window belongs to is the server's `a.run` card (app.py
// `_run_cards`, read off the runs table) — never a guess from the window name.
//
// CMX-66 (part 2) adds the VIEW menu's model: which rows the filters keep (Status /
// Environment / Last activity), the other grouping MODES (Date / State / Custom groups /
// None), the sorts (Last activity / Name / Created) and the empty groups. A row's
// `badge` slot stays the renderer's to fill (the PR badge, CMX-67's judge progress).
//
// CMX-276: moved from static/js/sidebarmodel.js to TypeScript, no behaviour change except
// orderGroups (the Dispatched group is always last). Legacy nav.js / terminals.js import it
// directly: they are bundled with it (CMX-217).

/** A run card (app.py `_run_cards`): the run a window belongs to. */
export interface RunCard {
    task_id?: string;
    role?: string;
    status?: string;
    judge_state?: string | null;
    title?: string;
    pr_url?: string | null;
}

/** A detached judge battery (chela/judge.py `battery_for_window`). */
export interface Battery {
    state?: string;
    label?: string;
    done?: number;
    total?: number | null;
    phase?: string | null;
    elapsed?: number | null;
}

/** One /api/agents row, as far as the sidebar reads it. */
export interface AgentRow {
    name?: string;
    window_id?: string;
    cwd?: string | null;
    run?: RunCard | null;
    dispatched?: boolean;
    judge_battery?: Battery | null;
    session_status?: string;
    needs_human?: boolean;
    done?: boolean;
    claude_running?: boolean;
    window_type?: string;
    session_moved?: boolean;
    last_activity?: string | number | null;
    recap_ts?: string | number | null;
    created?: string | number | null;
    ai_title?: string | null;
    recap?: string | null;
    pr?: { url?: string; state?: string; draft?: boolean } | null;
}

/** A closed session's resume record (chela/sidebar_archive.py). */
export interface SessionRecord {
    key: string;
    name?: string;
    session_id?: string;
    cwd?: string;
    archived_at?: number;
}

export type Wants = (a: AgentRow) => boolean;

// CMX-40's battery reading, as static/js/wallmodel.js `batteryState` has it. A copy, so this
// module builds alone (the frontend tree imports nothing outside it); CMX-212 P22 ports
// wallmodel to TypeScript and leaves one.
function batteryState(a: AgentRow | null | undefined): { word: string; cls: string } | null {
    const b = a && a.judge_battery;
    if (!b) return null;
    if (b.state === 'testing' || b.state === 'died') return { word: b.label ?? '', cls: b.state };
    return null;
}

export interface Item {
    kind: 'agent' | 'run' | 'session';
    key: string;
    agent: AgentRow | null;
    windows: AgentRow[];
    run?: RunCard;
    judge?: AgentRow | null;
    record?: SessionRecord;
}

export interface Group {
    key: string;
    label: string;
    cwd: string | null;
    items: Item[];
    rank?: number;
    flat?: boolean;
}

export const OTHER_KEY = '~other';
// The ONE Archived section at the bottom of the sidebar (CMX-75): its open state rides the
// collapsed-groups set as `~archived:open`.
export const ARCHIVED_SECTION_KEY = '~archived';
export const DISPATCHED_KEY = '~dispatched';
// Where the dispatcher puts a run's worktree. A session sitting in one is dispatched
// work, and must never get a group named after the worktree (one group per run).
export const WORKTREE_SEG = '/.chela/worktrees/';

function _norm(p: unknown): string {
    return p ? String(p).replace(/\/+$/, '') : '';
}

function _segments(p: unknown): string[] {
    return _norm(p).split('/').filter(Boolean);
}

// The folder group an agent window belongs to: its cwd, or one of the two catch-alls.
// The home dir is a folder like any other (CMX-72): its group is named after its
// basename, with the same `+` and menu. "Other" is only for a window with no cwd.
export function folderKey(a: AgentRow | null | undefined): string {
    if (!a) return OTHER_KEY;
    if (a.run || a.dispatched) return DISPATCHED_KEY;
    const cwd = _norm(a.cwd);
    if (!cwd) return OTHER_KEY;
    if (cwd.includes(WORKTREE_SEG)) return DISPATCHED_KEY;
    return cwd;
}

// `{key: label}` for a set of folder keys. The label is the folder's basename; two
// folders that share one are told apart the way the desktop does it —
// `python-agent · work` / `python-agent · personal` — by the shortest run of parent
// segments that makes every label unique.
export function folderLabels(keys: string[]): Record<string, string> {
    const out: Record<string, string> = {};
    const paths = keys.filter(k => k !== OTHER_KEY && k !== DISPATCHED_KEY);
    const byBase: Record<string, string[]> = {};
    for (const k of paths) {
        const segs = _segments(k);
        const base = segs[segs.length - 1] || k;
        (byBase[base] = byBase[base] || []).push(k);
    }
    for (const [base, ks] of Object.entries(byBase)) {
        if (ks.length === 1) { out[ks[0] as string] = base; continue; }
        const parents = ks.map(k => _segments(k).slice(0, -1));
        const depth = Math.max(...parents.map(p => p.length));
        let n = 1;
        for (; n <= depth; n++) {
            const tails = parents.map(p => p.slice(-n).join('/'));
            if (new Set(tails).size === tails.length) break;
        }
        ks.forEach((k, i) => {
            const tail = (parents[i] || []).slice(-n).join('/');
            out[k] = tail ? `${base} · ${tail}` : base;
        });
    }
    if (keys.includes(DISPATCHED_KEY)) out[DISPATCHED_KEY] = 'Dispatched';
    if (keys.includes(OTHER_KEY)) out[OTHER_KEY] = 'Other';
    return out;
}

// Windows → rows. Windows carrying the same run card (`a.run.task_id`) fold into ONE
// run item; every other window is its own item. Order follows the input.
export function buildItems(agents: (AgentRow | null | undefined)[] | null | undefined): Item[] {
    const items: Item[] = [];
    const runs = new Map<string, Item>();
    for (const a of agents || []) {
        if (!a) continue;
        const tid = a.run && a.run.task_id;
        if (!tid) {
            items.push({ kind: 'agent', key: `w:${a.window_id || a.name}`, agent: a, windows: [a] });
            continue;
        }
        let it = runs.get(tid);
        if (!it) {
            it = { kind: 'run', key: `run:${tid}`, run: a.run ?? undefined, agent: null, judge: null, windows: [] };
            runs.set(tid, it);
            items.push(it);
        }
        it.windows.push(a);
        if (a.run?.role === 'judge') { if (!it.judge) it.judge = a; }
        else if (!it.agent) { it.agent = a; it.run = a.run ?? undefined; }
    }
    return items;
}

// The window a click on the row opens: the run's agent pane, else (its agent already
// finished and killed its window) the judge's.
export function primaryWindow(item: Item | null | undefined): AgentRow | null {
    if (!item) return null;
    return item.kind === 'run' ? (item.agent || item.judge || item.windows[0] || null) : item.agent;
}

// A dispatched run's state, as a WORD and a SHAPE (the `.term-status-dot` class) —
// the shape carries it without hue, and the word says it outright (Liav is red-weak).
//   working          claimed / running — the agent is on it
//   judging          the judge is running (or its window is up and has no verdict yet)
//   rework           changes_requested — sent back, an agent is going back in
//   awaiting review  a PR waits on a reviewer
//   needs human      the rework loop hit its cap and stopped
// A window of the run blocked on a prompt outranks them all: that is "waiting".
//
// CMX-67: the judge's DETACHED battery (`judge_battery` on the judge window's
// /api/agents record — CMX-40's model, wallmodel.batteryState, not recomputed here)
// is the run's live judge progress. While its pid is alive the row reads the battery's
// own label (`⚖️ testing · 3/6 · 15m` — counts and elapsed only, never the experiment
// running now: it may be a held-out one). A pid that died before a verdict reads
// "judge run died", with the needs-a-human shape — never idle or awaiting review. A
// recorded verdict outranks a leftover dead status file: that run DID finish.
export function runBattery(item: Item | null | undefined): { word: string; cls: string } | null {
    const ws = item ? [item.judge, ...(item.windows || [])].filter(Boolean) : [];
    const all = ws.map(batteryState).filter((b): b is NonNullable<typeof b> => !!b);
    return all.find(b => b.cls === 'testing') || all[0] || null;
}

export interface RunState { word: string; shape: string }

export function runState(item: Item, wants: Wants): RunState {
    const run: RunCard = (item && item.run) || {};
    const st = run.status;
    const judging = { word: 'judging', shape: 'judging' };
    if ((item.windows || []).some(a => wants(a))) return { word: 'waiting', shape: 'waiting' };
    const battery = runBattery(item);
    if (battery && battery.cls === 'testing') return { word: battery.word, shape: 'judging' };
    if (battery && battery.cls === 'died' && (!run.judge_state || run.judge_state === 'running')) {
        return { word: battery.word, shape: 'waiting' };
    }
    if (run.judge_state === 'running') return judging;
    if (st === 'claimed' || st === 'running') return { word: 'working', shape: 'working' };
    if (st === 'changes_requested') return { word: 'rework', shape: 'working' };
    if (st === 'needs_human') return { word: 'needs human', shape: 'waiting' };
    if (st === 'awaiting_review') {
        return (item.judge && !run.judge_state) ? judging : { word: 'awaiting review', shape: 'idle' };
    }
    if (st === 'done') return { word: 'done', shape: 'done' };
    return { word: st ? String(st).replace(/_/g, ' ') : 'unknown', shape: 'idle' };
}

// CMX-137: a dispatched run's status MARK (`.term-status-dot` classes + label): Linear-style
// ◐ working, ◕ judging AND awaiting review (style.css `.dispatched.*`), so in the collapsed
// rail a run never reads as your own working session. Done / waiting keep the shared marks.
export function runDot(item: Item, wants: Wants): { cls: string; label: string } {
    const st = runState(item, wants);
    const review = st.shape === 'idle' && item?.run?.status === 'awaiting_review';
    return { cls: `dispatched ${review ? 'judging' : st.shape}`, label: `Dispatched run: ${st.word}` };
}

// The same mark for ONE window (the Wall's minimized bar); null without a run card.
export function windowRunDot(a: AgentRow | null | undefined, wants: unknown): { cls: string; label: string } | null {
    if (!a || !a.run || !a.run.task_id) return null;
    const item = buildItems([a])[0];
    return item ? runDot(item, () => !!wants) : null;
}

// The run row's label: `CMX-37 · <Linear title>` (truncation is the CSS's job).
export function runLabel(item: Item | null | undefined): string {
    const run: RunCard = (item && item.run) || {};
    const ref = run.task_id || '';
    return run.title ? (ref ? `${ref} · ${run.title}` : run.title) : ref;
}

// Run states that are SETTLED — nothing more will happen without a human starting it.
const RUN_SETTLED = new Set(['done', 'closed', 'failed']);

// May "Archive all" (or the row menu's Archive) archive this row? Refused for anything
// live: a window that is working, blocked on you (waiting / needs_human), the
// orchestrator, or a judge whose battery is still testing. What is left is a finished
// plain session (`done`: idle with output you have not answered), an IDLE one (Claude
// running, waiting for your next message — CMX-81), a window with no Claude in it any
// more, or a run that has settled (done / closed / failed). An unknown or missing status
// is NOT idle: a running session whose state cannot be read is refused.
//
// CMX-75: what archiving then DOES is the server's (chela/sidebar_archive.py): a plain
// session's window is CLOSED (after recording how to resume it — and refused when its
// session id is unknown), a settled run is only HIDDEN (its windows are the dispatcher's).
export function isArchivable(item: Item | null | undefined,
    { wants, orchWid }: { wants?: Wants; orchWid?: string | null } = {}): boolean {
    if (!item || !wants) return false;
    for (const a of item.windows || []) {
        if (!a) return false;
        if (orchWid && a.window_id === orchWid) return false;
        if (wants(a)) return false;
        if (a.session_status === 'busy') return false;
        if (a.judge_battery && a.judge_battery.state === 'testing') return false;
    }
    if (item.kind === 'run') {
        const run: RunCard = item.run || {};
        return RUN_SETTLED.has(run.status ?? '') && run.judge_state !== 'running';
    }
    const a = item.agent;
    return !!a && (!!a.done || !a.claude_running || a.session_status === 'idle');
}

// Group order: the user's persisted order (Move up / Move down), with every group it
// does not mention merged in by its default rank — folders by label, then Other. An
// unstored group goes right after the LAST stored group whose default rank is not above
// its own (before every stored group when none is), so a folder created after the user
// moved anything lands among the folders. The stored order is not rewritten.
// Dispatched is ALWAYS the last group, below every human folder (Liav, 10-19): a stored
// order that puts it higher is ignored for it.
export function orderGroups<G extends { key: string; label: string; rank?: number }>(
    groups: G[], stored: string[] | null | undefined): G[] {
    const last = groups.filter(g => g.key === DISPATCHED_KEY);
    groups = groups.filter(g => g.key !== DISPATCHED_KEY);
    stored = (stored || []).filter(k => k !== DISPATCHED_KEY);
    const pos = new Map(stored.map((k, i) => [k, i]));
    const rank = (g: G) => (g.rank != null ? g.rank : g.key === OTHER_KEY ? 2 : 0);
    const dflt = [...groups].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
    const di = new Map(dflt.map((g, i) => [g.key, i]));
    const kept = groups.filter(g => pos.has(g.key));
    // [slot, tie]: a stored group sits at its stored position; an unstored one just after
    // its anchor (-1 = before them all), in default order among those sharing it.
    const slot = new Map<string, [number, number]>(groups.map(g => {
        const at = pos.get(g.key);
        if (at != null) return [g.key, [at, 0]];
        let anchor = -1;
        for (const s of kept) if (rank(s) <= rank(g)) anchor = Math.max(anchor, pos.get(s.key) ?? -1);
        return [g.key, [anchor, 1 + (di.get(g.key) ?? 0)]];
    }));
    const sorted = [...groups].sort((a, b) => {
        const [sa, ta] = slot.get(a.key) ?? [0, 0];
        const [sb, tb] = slot.get(b.key) ?? [0, 0];
        return sa - sb || ta - tb;
    });
    return [...sorted, ...last];
}

// Move group `key` one step (`dir` = -1 up, +1 down) within the rendered order
// `keys`, and return the order to persist: the new visible order, then whatever the
// stored order held that is not on screen right now (a folder with no session today
// keeps its place for when one comes back).
export function moveGroup(keys: string[], stored: string[] | null | undefined, key: string, dir: number): string[] | null {
    const order = [...keys];
    const i = order.indexOf(key);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= order.length) return null;
    [order[i], order[j]] = [order[j] as string, order[i] as string];
    return [...order, ...(stored || []).filter(k => !order.includes(k))];
}

// --- The VIEW menu (CMX-66) ---------------------------------------------------------

// The choices, their defaults, and what a stored value is allowed to be. A stored view
// that is missing a field, or holds one this build does not know, falls back field by
// field — a viewer's choices survive an upgrade that adds or drops an option.
export const VIEW_STATUS: readonly string[] = ['active', 'all', 'archived'];
// The session kinds the Environment filter selects between. CMX-28's "background" is a
// window whose agent moved to a Claude Code background session.
export const ENV_KINDS: readonly string[] = ['interactive', 'dispatched', 'judge', 'background'];
export const VIEW_ACTIVITY: Readonly<Record<string, number | null>> = { '1d': 1, '7d': 7, '30d': 30, all: null };
export const GROUP_MODES: readonly string[] = ['date', 'folder', 'state', 'custom', 'none'];
export const SORTS: readonly string[] = ['activity', 'name', 'created'];
export interface View {
    status: string;
    env: string[];
    activity: string;
    groupBy: string;
    sort: string;
    showEmpty: boolean;
    showPR: boolean;
}

export const VIEW_DEFAULTS: Readonly<View> = Object.freeze({
    status: 'active',
    // Everything except Judges: a judge window is a dispatched run's reviewer, and the
    // run's own row already says "judging" — the bare judge window is noise by default.
    env: ['interactive', 'dispatched', 'background'],
    activity: '7d',
    groupBy: 'folder',
    sort: 'activity',
    showEmpty: false,
    showPR: true,
});

export function normalizeView(raw: unknown): View {
    const v = ((raw && typeof raw === 'object') ? raw : {}) as Record<string, unknown>;
    const env = v.env;
    const d = VIEW_DEFAULTS;
    return {
        status: VIEW_STATUS.includes(v.status as string) ? v.status as string : d.status,
        env: Array.isArray(env) ? ENV_KINDS.filter(k => env.includes(k)) : [...d.env],
        activity: Object.prototype.hasOwnProperty.call(VIEW_ACTIVITY, v.activity as string) ? v.activity as string : d.activity,
        groupBy: GROUP_MODES.includes(v.groupBy as string) ? v.groupBy as string : d.groupBy,
        sort: SORTS.includes(v.sort as string) ? v.sort as string : d.sort,
        showEmpty: typeof v.showEmpty === 'boolean' ? v.showEmpty : d.showEmpty,
        showPR: typeof v.showPR === 'boolean' ? v.showPR : d.showPR,
    };
}

// One window's session kind. A judge is a judge even before its run card lands (the
// dispatcher names its window `judge-…`); a dispatched worker stays "dispatched" even
// after it moves to a background session.
export function windowKind(a: AgentRow | null | undefined): string {
    if (!a) return 'interactive';
    if ((a.run && a.run.role === 'judge') || String(a.name || '').startsWith('judge-')) return 'judge';
    if (a.run || a.dispatched) return 'dispatched';
    if (a.session_moved) return 'background';
    return 'interactive';
}

// The kinds a ROW is: a run row is a dispatched run whatever windows it has left (its
// agent may have finished and killed its window while the judge works), and also a
// judge's when one of its windows is the judge. A row shows when ANY of its kinds is
// selected — so hiding Judges never hides a run's one row.
export function itemKinds(item: Item): Set<string> {
    const s = new Set((item.windows || []).map(windowKind));
    if (item.kind === 'run') s.add('dispatched');
    return s;
}

function _ts(v: string | number | null | undefined): number | null {
    if (v == null || v === '') return null;
    const t = typeof v === 'number' ? (v < 1e12 ? v * 1000 : v) : Date.parse(v);
    return Number.isFinite(t) ? t : null;
}

// When the row was last active (ms, or null when nothing says): the newest of its
// windows' `last_activity` (the transcript's last write, app.py) and recap time, else
// when it was created.
export function activityTs(item: Item): number | null {
    let best: number | null = null;
    for (const a of item.windows || []) {
        for (const t of [_ts(a.last_activity), _ts(a.recap_ts)]) {
            if (t != null && (best == null || t > best)) best = t;
        }
    }
    return best != null ? best : createdTs(item);
}

// When the row was created (ms, or null): its earliest window's start.
export function createdTs(item: Item): number | null {
    let best: number | null = null;
    for (const a of item.windows || []) {
        const t = _ts(a.created);
        if (t != null && (best == null || t < best)) best = t;
    }
    return best;
}

// Is something HAPPENING on this row right now — working, blocked on you, a judge
// battery running, a run in flight? The Last-activity filter never hides one of these,
// however old its timestamps read.
export function isLive(item: Item, wants?: Wants): boolean {
    for (const a of item.windows || []) {
        if (!a) continue;
        if (a.session_status === 'busy' || (wants && wants(a))) return true;
        if (a.judge_battery && a.judge_battery.state === 'testing') return true;
    }
    if (item.kind === 'run') return ['working', 'judging', 'waiting'].includes(runState(item, wants || (() => false)).shape);
    return false;
}

// The row's State group: needs (blocked on you) / working / completed / idle.
export function itemState(item: Item, wants?: Wants): string {
    const w = wants || (() => false);
    if (item.kind === 'run') {
        const st = runState(item, w);
        if (st.shape === 'waiting') return 'needs';
        if (st.shape === 'working' || st.shape === 'judging') return 'working';
        const run: RunCard = item.run || {};
        if (RUN_SETTLED.has(run.status ?? '') || run.status === 'awaiting_review') return 'completed';
        return 'idle';
    }
    const a: AgentRow = item.agent || {};
    if (w(a)) return 'needs';
    if (a.session_status === 'busy' || (a.judge_battery && a.judge_battery.state === 'testing')) return 'working';
    if (a.done) return 'completed';
    return 'idle';
}

export const STATE_GROUPS = [
    { key: '~state:working', id: 'working', label: 'Working' },
    { key: '~state:needs', id: 'needs', label: 'Needs you' },
    { key: '~state:idle', id: 'idle', label: 'Idle' },
    { key: '~state:completed', id: 'completed', label: 'Completed' },
];

export const DATE_GROUPS = [
    { key: '~date:today', label: 'Today' },
    { key: '~date:yesterday', label: 'Yesterday' },
    { key: '~date:week', label: 'This week' },
    { key: '~date:older', label: 'Older' },
];

// Which Date group a timestamp falls in, by the viewer's calendar days: today,
// yesterday, the five days before that ("This week"), or older. Unknown → Older.
export function dateBucket(ts: number | null, now: number): string {
    if (ts == null) return '~date:older';
    const d = new Date(now);
    const today = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const DAY = 86400000;
    if (ts >= today) return '~date:today';
    if (ts >= today - DAY) return '~date:yesterday';
    if (ts >= today - 6 * DAY) return '~date:week';
    return '~date:older';
}

export const UNGROUPED_KEY = '~ungrouped';
export const FLAT_KEY = '~all';
export function customKey(id: string): string { return `~custom:${id}`; }

function _sortItems(items: Item[], sort: string, labelOf: (it: Item) => string): Item[] {
    // numeric: CMX-100 sorts after CMX-37, not between CMX-10 and CMX-11
    const byName = (a: Item, b: Item) => labelOf(a).localeCompare(labelOf(b), undefined, { numeric: true });
    if (sort === 'name') return [...items].sort(byName);
    if (sort === 'activity' || sort === 'created') {
        const f = sort === 'activity' ? activityTs : createdTs;
        // newest first; a row nothing dates sinks to the bottom, by name
        return [...items].sort((a, b) => {
            const ta = f(a), tb = f(b);
            if (ta == null || tb == null) return Number(ta == null) - Number(tb == null) || byName(a, b);
            return tb - ta || byName(a, b);
        });
    }
    return items;
}

// An archived (CLOSED) session's row (CMX-75): it has no window any more — only the
// server's resume record (chela/sidebar_archive.py) — keyed by its session id.
export function sessionItem(rec: SessionRecord): Item {
    return { kind: 'session', key: rec.key, record: rec, agent: null, windows: [] };
}

// The whole sidebar, as data:
//   needsYou  rows blocked on a human, lifted above the groups ("Needs you") — in the
//             State grouping they are its "Needs you" group instead
//   groups    [{key, label, cwd, items, flat?}] in display order — NEVER an archived row
//   archived  every archived row, for the ONE "Archived (N)" section at the BOTTOM
//             (CMX-75, like the desktop): hidden rows still archivable, then the closed
//             sessions (`sessions`). Not filtered by the view — it is the archive.
//   hidden    keys of hidden rows that are still archivable (archive-set pruning)
// Options: `mode` (folder / date / state / custom / none), `wants` (util.js's
// wantsHuman), `orchWid`, `order` (persisted group order), `archived` (a Set of hidden
// item keys), `sessions` (the server's closed-session records), `status` (active / all
// / archived — `showArchived` is part 1's spelling of 'all'; it decides whether the
// renderer SHOWS the Archived section, and Archived shows nothing else), `env` (the
// session kinds to show; null = all), `activityDays` (null = all), `now`, `sort`
// (activity / name / created), `labelOf` (an item's display label), `showEmpty`,
// `custom` ({groups: [{id, name}], assign: {itemKey: id}}).
//
// The orchestrator (`orchWid`, the decisions-inbox holder) is NOT lifted out of its
// group (CMX-72) — it sits in its folder like any other session.
//
// What the filters NEVER hide: the orchestrator, and a row blocked on you — a filter is
// a way to tidy the list, not a way to miss a human gate.
export interface Custom {
    groups: { id: string; name: string }[];
    assign: Record<string, string>;
}

export interface GroupOptions {
    mode?: string;
    wants: Wants;
    orchWid?: string | null;
    order?: string[];
    archived?: Set<string>;
    showArchived?: boolean;
    status?: string;
    env?: string[] | null;
    activityDays?: number | null;
    now?: number;
    sort?: string;
    labelOf?: (it: Item) => string;
    showEmpty?: boolean;
    custom?: Custom;
    sessions?: SessionRecord[];
}

export interface SidebarModel {
    needsYou: Item[];
    groups: Group[];
    archived: Item[];
    hidden: string[];
}

export function groupSidebar(agents: (AgentRow | null | undefined)[] | null | undefined, opts: GroupOptions): SidebarModel {
    const { mode = 'folder', wants, orchWid = null, order = [], archived = new Set<string>(),
        showArchived = false, env = null, activityDays = null, now = Date.now(),
        sort = 'name', labelOf = (it: Item) => it.key, showEmpty = false,
        custom = { groups: [], assign: {} }, sessions = [] } = opts;
    const status = opts.status || (showArchived ? 'all' : 'active');
    if (!GROUP_MODES.includes(mode)) throw new Error(`unknown sidebar grouping mode: ${mode}`);
    const items = buildItems(agents);
    const envSet = env ? new Set(env) : null;
    const cutoff = activityDays ? now - activityDays * 86400000 : null;
    const needsYou: Item[] = [];
    const hidden: string[] = [];
    const archivedRows: Item[] = [];
    const kept: Item[] = [];
    const allFolders = new Set<string>();
    for (const it of items) {
        const prim = primaryWindow(it);
        if (it.windows.some(a => wants(a))) { needsYou.push(it); continue; }
        if (mode === 'folder') allFolders.add(it.kind === 'run' ? DISPATCHED_KEY : folderKey(prim));
        if (orchWid && it.windows.some(a => a.window_id === orchWid)) { kept.push(it); continue; }
        // Archived only while it is still archivable: a row that wakes up (busy, waiting)
        // is shown again whatever the archive set says. An archived row goes to the ONE
        // Archived section at the bottom — never into a group.
        if (archived.has(it.key) && isArchivable(it, { wants, orchWid })) {
            hidden.push(it.key);
            archivedRows.push(it);
            continue;
        }
        if (status === 'archived') continue;
        if (envSet && ![...itemKinds(it)].some(k => envSet.has(k))) continue;
        if (cutoff != null && !isLive(it, wants)) {
            const t = activityTs(it);
            if (t != null && t < cutoff) continue;
        }
        kept.push(it);
    }
    for (const rec of sessions || []) if (rec && rec.key) archivedRows.push(sessionItem(rec));

    // The candidate groups, in a mode's own fixed order (rank); folders are found.
    type Draft = { key: string; items: Item[]; label?: string; rank?: number; flat?: boolean };
    const byKey = new Map<string, Draft>();
    const ensure = (key: string, extra: Partial<Draft> = {}): Draft => {
        let g = byKey.get(key);
        if (!g) { g = { key, items: [], ...extra }; byKey.set(key, g); }
        return g;
    };
    const customIds = new Set((custom.groups || []).map(g => g.id));
    if (mode === 'state') STATE_GROUPS.forEach((g, i) => ensure(g.key, { label: g.label, rank: i }));
    if (mode === 'date') DATE_GROUPS.forEach((g, i) => ensure(g.key, { label: g.label, rank: i }));
    if (mode === 'custom') {
        (custom.groups || []).forEach((g, i) => ensure(customKey(g.id), { label: g.name, rank: i }));
        ensure(UNGROUPED_KEY, { label: 'Ungrouped', rank: 1e6 });
    }
    if (mode === 'none') ensure(FLAT_KEY, { label: '', rank: 0, flat: true });
    if (mode === 'folder' && showEmpty) {
        for (const k of allFolders) ensure(k);
        for (const k of order) if (typeof k === 'string' && k.startsWith('/')) ensure(k);
    }

    const keyOf = (it: Item): string => {
        if (mode === 'none') return FLAT_KEY;
        if (mode === 'state') return `~state:${itemState(it, wants)}`;
        if (mode === 'date') return isLive(it, wants) ? '~date:today' : dateBucket(activityTs(it), now);
        if (mode === 'custom') {
            // keyed on the row's STABLE identity (window id / run id), never its label
            const id = (custom.assign || {})[it.key];
            return id != null && customIds.has(id) ? customKey(id) : UNGROUPED_KEY;
        }
        return it.kind === 'run' ? DISPATCHED_KEY : folderKey(primaryWindow(it));
    };
    for (const it of kept) ensure(keyOf(it)).items.push(it);
    // In the State grouping, "Needs you" is a group like the others.
    let lifted = needsYou;
    if (mode === 'state') {
        ensure('~state:needs').items.unshift(...needsYou);
        lifted = [];
    }

    const labels = folderLabels([...byKey.values()].filter(g => !g.label && !g.flat).map(g => g.key));
    let groups: Group[] = [...byKey.values()].map(g => {
        // The folder a "+" opens a new session in; Other and Dispatched have none.
        const cwd = mode === 'folder' && g.key !== OTHER_KEY && g.key !== DISPATCHED_KEY ? g.key : null;
        const label = g.label != null ? g.label : (labels[g.key] ?? g.key);
        return { ...g, label, cwd, items: _sortItems(g.items, sort, labelOf) };
    });
    if (!showEmpty) groups = groups.filter(g => g.items.length || g.flat);
    return {
        needsYou: _sortItems(lifted, sort, labelOf),
        groups: orderGroups(groups, order),
        archived: archivedRows,
        hidden,
    };
}

// CMX-165: the window ids in the order the sidebar DRAWS them, read off groupSidebar's
// model: Needs you, then each group in its order (Move up / Move down, custom groups) and
// each group's rows in their sort, then the Archived section. A collapsed group still
// counts (its rows are in the model, just not shown), so the phone's pill strip keeps a
// pill for every session. A run's primary window leads its other windows (the judge).
export function sidebarWids(model: Partial<SidebarModel>): string[] {
    const out: string[] = [];
    const seen = new Set<string>();
    const rows = [...(model.needsYou || []), ...(model.groups || []).flatMap(g => g.items), ...(model.archived || [])];
    for (const it of rows) {
        const prim = primaryWindow(it);
        for (const a of [prim, ...(it.windows || [])]) {
            const wid = a && a.window_id;
            if (wid && !seen.has(wid)) { seen.add(wid); out.push(wid); }
        }
    }
    return out;
}

// `wids` sorted by their place in `order`; a wid `order` does not hold (a window spawned
// since the sidebar last drew) goes to the END, keeping its place among the others like
// it. Never drops one.
export function orderByList(wids: string[], order: string[] | null | undefined): string[] {
    const at = new Map((order || []).map((w, i) => [w, i]));
    const known = wids.filter(w => at.has(w)).sort((a, b) => (at.get(a) ?? 0) - (at.get(b) ?? 0));
    return [...known, ...wids.filter(w => !at.has(w))];
}

// --- The island's input (CMX-276) -----------------------------------------------------
//
// What nav.js publishes to the bridge's `sidebar` store on every render: the rows, the
// server layout, and the page state legacy still owns (labels, the wall, the active row).
// The island draws `sidebarModel(state)`; `sidebarOrder(state)` is the same model with
// the View filters lifted.
export interface SidebarState {
    agents: AgentRow[];
    wants: Wants;
    orchWid: string | null;
    view: View;
    order: string[];
    custom: Custom;
    archived: string[];
    sessions: SessionRecord[];
    labels: Record<string, string>;
    now: number;
    collapsed: string[];
    ctx: Record<string, number>;
    onWall: string[];
    active: string | null;
    altHeld: boolean;
}

// An item's display label: a run reads `CMX-37 · <title>`; a window reads its CMX-62
// label (manual name > Claude's title > window name), which terminals.js resolves.
export function itemLabel(it: Item, labels: Record<string, string>): string {
    if (it.kind === 'run') return runLabel(it);
    const a = it.agent;
    return (a && a.window_id && labels[a.window_id]) || (a && a.name) || '';
}

export function modelOptions(s: SidebarState): GroupOptions {
    return {
        mode: s.view.groupBy, wants: s.wants, orchWid: s.orchWid, order: s.order,
        archived: new Set(s.archived), status: s.view.status, env: s.view.env,
        activityDays: VIEW_ACTIVITY[s.view.activity] ?? null, now: s.now, sort: s.view.sort,
        labelOf: it => itemLabel(it, s.labels), showEmpty: s.view.showEmpty, custom: s.custom,
        sessions: s.sessions,
    };
}

export function sidebarModel(s: SidebarState): SidebarModel {
    return groupSidebar(s.agents, modelOptions(s));
}

// CMX-165: the window ids in the order the sidebar draws them. The View filters (Status /
// Environment / Last activity) are lifted: a row the sidebar filters out still has a pane,
// so it keeps its group's position.
export function sidebarOrder(s: SidebarState | null): string[] {
    if (!s) return [];
    return sidebarWids(groupSidebar(s.agents, { ...modelOptions(s), status: 'all', env: null, activityDays: null }));
}
