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

import { batteryState } from './wallmodel.js';

export const OTHER_KEY = '~other';
export const DISPATCHED_KEY = '~dispatched';
// Where the dispatcher puts a run's worktree. A session sitting in one is dispatched
// work, and must never get a group named after the worktree (one group per run).
export const WORKTREE_SEG = '/.chela/worktrees/';

function _norm(p) {
    return p ? String(p).replace(/\/+$/, '') : '';
}

function _segments(p) {
    return _norm(p).split('/').filter(Boolean);
}

// The fallback when the server did not say (`cwd_is_home` is app.py's word, decided
// where the home dir is known): a bare /home/<user>, /Users/<user> or /root.
function _looksHome(p) {
    return /^\/(home|Users)\/[^/]+$/.test(p) || p === '/root';
}

// The folder group an agent window belongs to: its cwd, or one of the two catch-alls.
export function folderKey(a) {
    if (!a) return OTHER_KEY;
    if (a.run || a.dispatched) return DISPATCHED_KEY;
    const cwd = _norm(a.cwd);
    if (!cwd) return OTHER_KEY;
    if (cwd.includes(WORKTREE_SEG)) return DISPATCHED_KEY;
    if (a.cwd_is_home || _looksHome(cwd)) return OTHER_KEY;
    return cwd;
}

// `{key: label}` for a set of folder keys. The label is the folder's basename; two
// folders that share one are told apart the way the desktop does it —
// `python-agent · work` / `python-agent · personal` — by the shortest run of parent
// segments that makes every label unique.
export function folderLabels(keys) {
    const out = {};
    const paths = keys.filter(k => k !== OTHER_KEY && k !== DISPATCHED_KEY);
    const byBase = {};
    for (const k of paths) {
        const segs = _segments(k);
        const base = segs[segs.length - 1] || k;
        (byBase[base] = byBase[base] || []).push(k);
    }
    for (const [base, ks] of Object.entries(byBase)) {
        if (ks.length === 1) { out[ks[0]] = base; continue; }
        const parents = ks.map(k => _segments(k).slice(0, -1));
        const depth = Math.max(...parents.map(p => p.length));
        let n = 1;
        for (; n <= depth; n++) {
            const tails = parents.map(p => p.slice(-n).join('/'));
            if (new Set(tails).size === tails.length) break;
        }
        ks.forEach((k, i) => {
            const tail = parents[i].slice(-n).join('/');
            out[k] = tail ? `${base} · ${tail}` : base;
        });
    }
    if (keys.includes(DISPATCHED_KEY)) out[DISPATCHED_KEY] = 'Dispatched';
    if (keys.includes(OTHER_KEY)) out[OTHER_KEY] = 'Other';
    return out;
}

// Windows → rows. Windows carrying the same run card (`a.run.task_id`) fold into ONE
// run item; every other window is its own item. Order follows the input.
export function buildItems(agents) {
    const items = [];
    const runs = new Map();
    for (const a of agents || []) {
        if (!a) continue;
        const tid = a.run && a.run.task_id;
        if (!tid) {
            items.push({ kind: 'agent', key: `w:${a.window_id || a.name}`, agent: a, windows: [a] });
            continue;
        }
        let it = runs.get(tid);
        if (!it) {
            it = { kind: 'run', key: `run:${tid}`, run: a.run, agent: null, judge: null, windows: [] };
            runs.set(tid, it);
            items.push(it);
        }
        it.windows.push(a);
        if (a.run.role === 'judge') { if (!it.judge) it.judge = a; }
        else if (!it.agent) { it.agent = a; it.run = a.run; }
    }
    return items;
}

// The window a click on the row opens: the run's agent pane, else (its agent already
// finished and killed its window) the judge's.
export function primaryWindow(item) {
    if (!item) return null;
    return item.kind === 'run' ? (item.agent || item.judge || item.windows[0]) : item.agent;
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
export function runBattery(item) {
    const ws = item ? [item.judge, ...(item.windows || [])].filter(Boolean) : [];
    const all = ws.map(batteryState).filter(Boolean);
    return all.find(b => b.cls === 'testing') || all[0] || null;
}

export function runState(item, wants) {
    const run = (item && item.run) || {};
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

// The run row's label: `CMX-37 · <Linear title>` (truncation is the CSS's job).
export function runLabel(item) {
    const run = (item && item.run) || {};
    const ref = run.task_id || '';
    return run.title ? (ref ? `${ref} · ${run.title}` : run.title) : ref;
}

// Run states that are SETTLED — nothing more will happen without a human starting it.
const RUN_SETTLED = new Set(['done', 'closed', 'failed']);

// May "Archive all" hide this row? Archiving only HIDES a row from the sidebar — it
// never kills a window — and even hiding is refused for anything live: a window that is
// working, blocked on you (waiting / needs_human), the orchestrator, or a judge whose
// battery is still testing. What is left is a finished plain session (`done`: idle with
// output you have not answered), a window with no Claude in it any more, or a run that
// has settled (done / closed / failed).
export function isArchivable(item, { wants, orchWid } = {}) {
    if (!item || !wants) return false;
    for (const a of item.windows || []) {
        if (!a) return false;
        if (orchWid && a.window_id === orchWid) return false;
        if (wants(a)) return false;
        if (a.session_status === 'busy') return false;
        if (a.judge_battery && a.judge_battery.state === 'testing') return false;
    }
    if (item.kind === 'run') {
        const run = item.run || {};
        return RUN_SETTLED.has(run.status) && run.judge_state !== 'running';
    }
    const a = item.agent;
    return !!a && (!!a.done || !a.claude_running);
}

// Group order: the user's persisted order first (Move up / Move down), then everything
// it does not mention in the default order — folders by label, then Dispatched, then
// Other at the bottom (where the desktop puts it).
export function orderGroups(groups, stored) {
    const pos = new Map((stored || []).map((k, i) => [k, i]));
    const rank = g => (g.rank != null ? g.rank : g.key === OTHER_KEY ? 2 : g.key === DISPATCHED_KEY ? 1 : 0);
    const dflt = [...groups].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
    const di = new Map(dflt.map((g, i) => [g.key, i]));
    return [...groups].sort((a, b) => {
        const pa = pos.has(a.key) ? pos.get(a.key) : Infinity;
        const pb = pos.has(b.key) ? pos.get(b.key) : Infinity;
        if (pa !== pb) return pa - pb;
        return di.get(a.key) - di.get(b.key);
    });
}

// Move group `key` one step (`dir` = -1 up, +1 down) within the rendered order
// `keys`, and return the order to persist: the new visible order, then whatever the
// stored order held that is not on screen right now (a folder with no session today
// keeps its place for when one comes back).
export function moveGroup(keys, stored, key, dir) {
    const order = [...keys];
    const i = order.indexOf(key);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= order.length) return null;
    [order[i], order[j]] = [order[j], order[i]];
    return [...order, ...(stored || []).filter(k => !order.includes(k))];
}

// --- The VIEW menu (CMX-66) ---------------------------------------------------------

// The choices, their defaults, and what a stored value is allowed to be. A stored view
// that is missing a field, or holds one this build does not know, falls back field by
// field — a viewer's choices survive an upgrade that adds or drops an option.
export const VIEW_STATUS = ['active', 'all', 'archived'];
// The session kinds the Environment filter selects between. CMX-28's "background" is a
// window whose agent moved to a Claude Code background session.
export const ENV_KINDS = ['interactive', 'dispatched', 'judge', 'background'];
export const VIEW_ACTIVITY = { '1d': 1, '7d': 7, '30d': 30, all: null };
export const GROUP_MODES = ['date', 'folder', 'state', 'custom', 'none'];
export const SORTS = ['activity', 'name', 'created'];
export const VIEW_DEFAULTS = Object.freeze({
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

export function normalizeView(raw) {
    const v = (raw && typeof raw === 'object') ? raw : {};
    const d = VIEW_DEFAULTS;
    return {
        status: VIEW_STATUS.includes(v.status) ? v.status : d.status,
        env: Array.isArray(v.env) ? ENV_KINDS.filter(k => v.env.includes(k)) : [...d.env],
        activity: Object.prototype.hasOwnProperty.call(VIEW_ACTIVITY, v.activity) ? v.activity : d.activity,
        groupBy: GROUP_MODES.includes(v.groupBy) ? v.groupBy : d.groupBy,
        sort: SORTS.includes(v.sort) ? v.sort : d.sort,
        showEmpty: typeof v.showEmpty === 'boolean' ? v.showEmpty : d.showEmpty,
        showPR: typeof v.showPR === 'boolean' ? v.showPR : d.showPR,
    };
}

// One window's session kind. A judge is a judge even before its run card lands (the
// dispatcher names its window `judge-…`); a dispatched worker stays "dispatched" even
// after it moves to a background session.
export function windowKind(a) {
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
export function itemKinds(item) {
    const s = new Set((item.windows || []).map(windowKind));
    if (item.kind === 'run') s.add('dispatched');
    return s;
}

function _ts(v) {
    if (v == null || v === '') return null;
    const t = typeof v === 'number' ? (v < 1e12 ? v * 1000 : v) : Date.parse(v);
    return Number.isFinite(t) ? t : null;
}

// When the row was last active (ms, or null when nothing says): the newest of its
// windows' `last_activity` (the transcript's last write, app.py) and recap time, else
// when it was created.
export function activityTs(item) {
    let best = null;
    for (const a of item.windows || []) {
        for (const t of [_ts(a.last_activity), _ts(a.recap_ts)]) {
            if (t != null && (best == null || t > best)) best = t;
        }
    }
    return best != null ? best : createdTs(item);
}

// When the row was created (ms, or null): its earliest window's start.
export function createdTs(item) {
    let best = null;
    for (const a of item.windows || []) {
        const t = _ts(a.created);
        if (t != null && (best == null || t < best)) best = t;
    }
    return best;
}

// Is something HAPPENING on this row right now — working, blocked on you, a judge
// battery running, a run in flight? The Last-activity filter never hides one of these,
// however old its timestamps read.
export function isLive(item, wants) {
    for (const a of item.windows || []) {
        if (!a) continue;
        if (a.session_status === 'busy' || (wants && wants(a))) return true;
        if (a.judge_battery && a.judge_battery.state === 'testing') return true;
    }
    if (item.kind === 'run') return ['working', 'judging', 'waiting'].includes(runState(item, wants || (() => false)).shape);
    return false;
}

// The row's State group: needs (blocked on you) / working / completed / idle.
export function itemState(item, wants) {
    const w = wants || (() => false);
    if (item.kind === 'run') {
        const st = runState(item, w);
        if (st.shape === 'waiting') return 'needs';
        if (st.shape === 'working' || st.shape === 'judging') return 'working';
        const run = item.run || {};
        if (RUN_SETTLED.has(run.status) || run.status === 'awaiting_review') return 'completed';
        return 'idle';
    }
    const a = item.agent || {};
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
export function dateBucket(ts, now) {
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
export function customKey(id) { return `~custom:${id}`; }

function _sortItems(items, sort, labelOf) {
    // numeric: CMX-100 sorts after CMX-37, not between CMX-10 and CMX-11
    const byName = (a, b) => labelOf(a).localeCompare(labelOf(b), undefined, { numeric: true });
    if (sort === 'name') return [...items].sort(byName);
    if (sort === 'activity' || sort === 'created') {
        const f = sort === 'activity' ? activityTs : createdTs;
        // newest first; a row nothing dates sinks to the bottom, by name
        return [...items].sort((a, b) => {
            const ta = f(a), tb = f(b);
            if (ta == null || tb == null) return (ta == null) - (tb == null) || byName(a, b);
            return tb - ta || byName(a, b);
        });
    }
    return items;
}

// The whole sidebar, as data:
//   pinned    the orchestrator's row (the decisions-inbox holder), lifted to the top
//   needsYou  rows blocked on a human, lifted above the groups ("Needs you") — in the
//             State grouping they are its "Needs you" group instead
//   groups    [{key, label, cwd, items, archived, flat?}] in display order
//   hidden    keys of archived rows that are still archivable (archive-set pruning and
//             the "Show archived (N)" count)
// Options: `mode` (folder / date / state / custom / none), `wants` (util.js's
// wantsHuman), `orchWid`, `order` (persisted group order), `archived` (a Set of item
// keys), `status` (active / all / archived — `showArchived` is part 1's spelling of
// 'all'), `env` (the session kinds to show; null = all), `activityDays` (null = all),
// `now`, `sort` (activity / name / created), `labelOf` (an item's display label),
// `showEmpty`, `custom` ({groups: [{id, name}], assign: {itemKey: id}}).
//
// What the filters NEVER hide: the pinned orchestrator, and a row blocked on you — a
// filter is a way to tidy the list, not a way to miss a human gate.
export function groupSidebar(agents, opts = {}) {
    const { mode = 'folder', wants, orchWid = null, order = [], archived = new Set(),
        showArchived = false, env = null, activityDays = null, now = Date.now(),
        sort = 'name', labelOf = it => it.key, showEmpty = false,
        custom = { groups: [], assign: {} } } = opts;
    const status = opts.status || (showArchived ? 'all' : 'active');
    if (!GROUP_MODES.includes(mode)) throw new Error(`unknown sidebar grouping mode: ${mode}`);
    const items = buildItems(agents);
    const envSet = env ? new Set(env) : null;
    const cutoff = activityDays ? now - activityDays * 86400000 : null;
    const pinned = [];
    const needsYou = [];
    const hidden = [];
    const kept = [];          // [item, isArchived]
    const allFolders = new Set();
    for (const it of items) {
        const prim = primaryWindow(it);
        if (orchWid && it.windows.some(a => a.window_id === orchWid)) { pinned.push(it); continue; }
        if (it.windows.some(a => wants(a))) { needsYou.push(it); continue; }
        if (mode === 'folder') allFolders.add(it.kind === 'run' ? DISPATCHED_KEY : folderKey(prim));
        // Archived only while it is still archivable: a row that wakes up (busy, waiting)
        // is shown again whatever the archive set says.
        const isArch = archived.has(it.key) && isArchivable(it, { wants, orchWid });
        if (isArch) hidden.push(it.key);
        if (status === 'archived' && !isArch) continue;
        if (envSet && ![...itemKinds(it)].some(k => envSet.has(k))) continue;
        if (cutoff != null && !isLive(it, wants)) {
            const t = activityTs(it);
            if (t != null && t < cutoff) continue;
        }
        kept.push([it, isArch]);
    }

    // The candidate groups, in a mode's own fixed order (rank); folders are found.
    const byKey = new Map();
    const ensure = (key, extra = {}) => {
        if (!byKey.has(key)) byKey.set(key, { key, items: [], archived: [], ...extra });
        return byKey.get(key);
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

    const keyOf = it => {
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
    for (const [it, isArch] of kept) {
        const g = ensure(keyOf(it));
        if (isArch) g.archived.push(it);
        // Status "Active" hides an archived row, but its group still knows it has one
        // (the group menu's "Unarchive all").
        if (!isArch || status !== 'active') g.items.push(it);
    }
    // In the State grouping, "Needs you" is a group like the others.
    let lifted = needsYou;
    if (mode === 'state') {
        byKey.get('~state:needs').items.unshift(...needsYou);
        lifted = [];
    }

    const labels = folderLabels([...byKey.keys()].filter(k => !byKey.get(k).label && !byKey.get(k).flat));
    let groups = [...byKey.values()].map(g => {
        // The folder a "+" opens a new session in. Other has none of its own: use a
        // home-dir session's cwd when there is one.
        let cwd = null;
        if (mode === 'folder') {
            if (g.key !== OTHER_KEY && g.key !== DISPATCHED_KEY) cwd = g.key;
            else if (g.key === OTHER_KEY) {
                const pool = [...g.items, ...items.filter(it => folderKey(primaryWindow(it)) === OTHER_KEY && it.kind !== 'run')];
                const home = pool.map(primaryWindow).find(a => a && a.cwd);
                cwd = home ? home.cwd : null;
            }
        }
        const label = g.label != null ? g.label : labels[g.key];
        return { ...g, label, cwd, items: _sortItems(g.items, sort, labelOf) };
    });
    if (!showEmpty) groups = groups.filter(g => g.items.length || g.archived.length || g.flat);
    return {
        pinned,
        needsYou: _sortItems(lifted, sort, labelOf),
        groups: orderGroups(groups, order),
        hidden,
    };
}
