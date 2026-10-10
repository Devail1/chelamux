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
// Hooks for the later parts (CMX-66 view menu): `groupSidebar` takes the grouping MODE
// and the sort as options (only 'folder' / 'name' exist here), and a row's `badge` slot
// is the renderer's to fill.

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
    const rank = g => (g.key === OTHER_KEY ? 2 : g.key === DISPATCHED_KEY ? 1 : 0);
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

function _sortItems(items, sort, labelOf) {
    // numeric: CMX-100 sorts after CMX-37, not between CMX-10 and CMX-11
    if (sort === 'name') return [...items].sort((a, b) => labelOf(a).localeCompare(labelOf(b), undefined, { numeric: true }));
    return items;
}

// The whole sidebar, as data:
//   pinned    the orchestrator's row (the decisions-inbox holder), lifted to the top
//   needsYou  rows blocked on a human, lifted above the groups ("Needs you")
//   groups    [{key, label, cwd, items, archived}] in display order
//   archived  keys of archived rows that are currently HIDDEN (for the counts)
// Options: `mode` (only 'folder' exists — CMX-66 adds the others), `wants` (util.js's
// wantsHuman), `orchWid`, `order` (persisted group order), `archived` (a Set of item
// keys), `showArchived`, `sort`, `labelOf` (an item's display label, for sorting).
export function groupSidebar(agents, opts = {}) {
    const { mode = 'folder', wants, orchWid = null, order = [], archived = new Set(),
        showArchived = false, sort = 'name', labelOf = it => it.key } = opts;
    if (mode !== 'folder') throw new Error(`unknown sidebar grouping mode: ${mode}`);
    const items = buildItems(agents);
    const pinned = [];
    const needsYou = [];
    const byKey = new Map();
    const hidden = [];
    for (const it of items) {
        const prim = primaryWindow(it);
        if (orchWid && it.windows.some(a => a.window_id === orchWid)) { pinned.push(it); continue; }
        if (it.windows.some(a => wants(a))) { needsYou.push(it); continue; }
        const key = it.kind === 'run' ? DISPATCHED_KEY : folderKey(prim);
        if (!byKey.has(key)) byKey.set(key, { key, items: [], archived: [] });
        const g = byKey.get(key);
        // Hidden only while it is still archivable: a row that wakes up (busy, waiting)
        // is shown again whatever the archive set says.
        if (archived.has(it.key) && isArchivable(it, { wants, orchWid })) {
            g.archived.push(it);
            hidden.push(it.key);
            if (!showArchived) continue;
        }
        g.items.push(it);
    }
    const labels = folderLabels([...byKey.keys()]);
    const groups = [...byKey.values()].map(g => {
        // The folder a "+" opens a new session in. Other has none of its own: use a
        // home-dir session's cwd when there is one.
        let cwd = null;
        if (g.key !== OTHER_KEY && g.key !== DISPATCHED_KEY) cwd = g.key;
        else if (g.key === OTHER_KEY) {
            const home = [...g.items, ...g.archived].map(primaryWindow).find(a => a && a.cwd);
            cwd = home ? home.cwd : null;
        }
        return { ...g, label: labels[g.key], cwd, items: _sortItems(g.items, sort, labelOf) };
    });
    return {
        pinned,
        needsYou: _sortItems(needsYou, sort, labelOf),
        groups: orderGroups(groups, order),
        hidden,
    };
}
