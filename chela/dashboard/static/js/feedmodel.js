// ---------------------------------------------------------------------------
// THE FEED'S MODEL — agent lanes, pure. No DOM, no fetch, no imports.
//
// The fleet is the spine; the log is what each agent is SAYING. So the log is not
// rendered as one river of rows — it is grouped under the agent that produced it,
// and the lanes are ordered by ATTENTION, not by time: an agent that needs you
// sorts to the top and wears a `◆ NEEDS YOU` badge, so "who wants me" is
// answerable without reading a single row.
//
// Three rules this file exists to keep honest:
//
//   1. A LANE IS KEYED ON `wid`, NEVER ON A NAME. tmux rename is the source of
//      truth for a name and it changes under you; the id does not.
//   2. THE LANE LIST COMES FROM THE LOG, not from the live tmux table. A dead
//      agent still has a lane — 74 of one day's events belonged to windows that
//      no longer existed, and history that vanishes when a window closes is not
//      history. The live fleet is *unioned in* (an agent with nothing to say yet
//      still has a lane), never used as the filter.
//   3. NOTHING IS EVER HIDDEN SILENTLY. `pre/post_tool_use` is ~86% of the log's
//      volume, so it is filtered out by default — and the lane says exactly how
//      many rows that is, with a button to show them. Same rule, one level up:
//      after a day of dispatch the feed was 8 lanes, 5 of them corpses, so the GONE
//      lanes fold into ONE row that states what it is holding (`▸ 5 finished agents
//      · 47 events`) — collapsed, never DROPPED, and never silent (`splitGone`).
//
// ⚠️ COLOUR IS NEVER THE SIGNAL. Every class carries a GLYPH and a WORD (`◆ gate`,
// `■ lifecycle`, `● run`, `✕ denied`, `▸ prompt`); the hue (Okabe-Ito, in the CSS)
// is decoration on top of a label that already reads without it.
// ---------------------------------------------------------------------------

// The classes a row can belong to. `glyph` + `word` are the identity; the colour
// var is applied by the CSS and carries no information of its own.
export const CLASSES = {
    gate: { glyph: '◆', word: 'gate', label: 'Gates' },
    denied: { glyph: '✕', word: 'denied', label: 'Denied' },
    run: { glyph: '●', word: 'run', label: 'Runs' },
    lifecycle: { glyph: '■', word: 'lifecycle', label: 'Lifecycle' },
    prompt: { glyph: '▸', word: 'prompt', label: 'Prompts' },
    tool: { glyph: '⋯', word: 'tool', label: 'Tool calls' },
    other: { glyph: '·', word: 'event', label: 'Other' },
};

export const CLASS_IDS = Object.keys(CLASSES);

// The default filter: gates awaiting you + agent lifecycle (Liav, decided). The
// tool-call firehose and the prompt/answer traffic are behind a toggle — a flat
// render of everything is unreadable within an hour of a busy fleet.
export const DEFAULT_CLASSES = ['gate', 'denied', 'run', 'lifecycle', 'other'];

// type → class. The hook events are namespaced (`hook.*` = an agent told us this);
// the bare ones are chela's own bookkeeping (chela/inbox.py, chela/main.py).
const TYPE_CLASS = {
    'hook.permission_request': 'gate',      // the agent is BLOCKED, asking for you
    'hook.elicitation': 'gate',
    blocked: 'gate',                        // inbox: "is BLOCKED on a prompt"
    'hook.permission_denied': 'denied',
    run_review: 'run',
    run_failed: 'run',
    'hook.user_prompt_submit': 'prompt',
    'hook.pre_tool_use': 'tool',
    'hook.post_tool_use': 'tool',
    'hook.session_start': 'lifecycle',
    'hook.session_end': 'lifecycle',
    'hook.stop': 'lifecycle',
    'hook.subagent_start': 'lifecycle',
    'hook.subagent_stop': 'lifecycle',
    'hook.pre_compact': 'lifecycle',
    'hook.post_compact': 'lifecycle',
    'hook.notification': 'lifecycle',
    daemon_start: 'lifecycle',
    finished: 'lifecycle',
    died: 'lifecycle',
    gone_unknown: 'lifecycle',
    completed_gone: 'lifecycle',
};

// An unknown type is `other` — and `other` is ON by default. A log is allowed to
// grow a type the UI has never heard of (`chela events emit --type note`), and the
// safe default for "I do not recognise this" is to SHOW it, not to swallow it.
export function classOf(type) {
    return TYPE_CLASS[type] || 'other';
}

// The lane a wid belongs to. `null`/absent → chela itself: a legitimate lane for
// genuinely ownerless events (`daemon_start`, and little else), not a dumping
// ground — an event that names a window is attributed at WRITE time (inbox.py::
// wid_for_window_name), and one that does not is honestly chela's.
export const SYSTEM_WID = '__chela__';
export const SYSTEM_LABEL = 'chela itself';

// Lane order = attention. needs-you first, gone last, chela's own lane after the
// fleet. A dead agent keeps its history; it just stops competing for your eyes.
export const LANE_ORDER = ['waiting', 'busy', 'idle', 'gone', 'system'];

export function laneRank(lane) {
    const i = LANE_ORDER.indexOf(lane && lane.status);
    return i < 0 ? LANE_ORDER.length : i;
}

// The name for a lane whose window is GONE: the last name the log saw it under.
// (A live window's name comes from the live table — tmux rename is the truth.)
function _nameFromEvent(e) {
    const p = (e && e.payload) || {};
    return p.window_name || p.branch_name || null;
}

function _projectOf(agent) {
    const cwd = agent && agent.cwd;
    if (!cwd) return '';
    const parts = String(cwd).split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
}

/**
 * Group the log into lanes.
 *
 * @param events  the log, oldest → newest (what /api/log hands back)
 * @param agents  /api/agents — the ONE busy/idle/waiting authority (agent_manager.
 *                status_by_wid behind it). Never a second status source.
 * @param classes the enabled row classes (default: DEFAULT_CLASSES)
 * @returns {lanes, hidden} — lanes sorted needs-you → busy → idle → gone → chela,
 *          each carrying its shown events (chronological), and the count of what
 *          the filter is hiding, per class.
 */
export function buildLanes(events, agents, classes) {
    const want = new Set(classes && classes.length ? classes : DEFAULT_CLASSES);
    const lanes = new Map();
    const hidden = {};   // class → count, across the whole feed

    const laneFor = (wid) => {
        if (!lanes.has(wid)) {
            lanes.set(wid, {
                wid,
                name: wid === SYSTEM_WID ? SYSTEM_LABEL : wid,
                project: '',
                status: wid === SYSTEM_WID ? 'system' : 'gone',
                system: wid === SYSTEM_WID,
                needsYou: false,
                events: [],
                hidden: {},        // class → count, in THIS lane
                hiddenTotal: 0,
                total: 0,
                lastTs: 0,
                // The task ids this lane handed you a PR for. Collected from EVERY
                // event, not from `events` — turning the `run` chip off must not bury
                // a review that is still open (splitGone reads this, not the rows).
                reviewTasks: [],
                openReview: false, // set by splitGone: it is still awaiting YOU
            });
        }
        return lanes.get(wid);
    };

    // The live fleet, unioned in. An agent that has said nothing yet still gets a
    // lane (the fleet is the spine) — but the live table is NOT the lane list, or a
    // closed window would take its history with it.
    (agents || []).forEach(a => {
        if (!a || !a.window_id || a.claude_running === false) return;
        const lane = laneFor(a.window_id);
        lane.name = a.name || lane.name;
        lane.project = _projectOf(a);
        // `waiting` = sitting on a permission/question prompt = it wants YOU. This is
        // the one authority (agent_manager.status_by_wid); a held gate surfaces here.
        lane.status = a.status || a.session_status || 'idle';
        lane.needsYou = lane.status === 'waiting';
    });

    (events || []).forEach(e => {
        if (!e) return;
        const lane = laneFor(e.wid || SYSTEM_WID);
        const cls = classOf(e.type);
        lane.total += 1;
        lane.lastTs = Math.max(lane.lastTs, e.ts || 0);
        if (e.type === 'run_review') {
            // The TASK id, never the wid: tmux recycles `@72`, a task id is minted once.
            // A review with no task id cannot be matched against the runs, so it is not
            // claimed as open — a legacy row must not pin the graveyard open forever.
            const tid = ((e.payload || {}).task_id || '').trim();
            if (tid && !lane.reviewTasks.includes(tid)) lane.reviewTasks.push(tid);
        }
        // A gone window's name is whatever the log last called it — the only handle
        // left once tmux has reaped the id.
        if (lane.status === 'gone') lane.name = _nameFromEvent(e) || lane.name;
        if (want.has(cls)) {
            lane.events.push({ ...e, cls });
        } else {
            lane.hidden[cls] = (lane.hidden[cls] || 0) + 1;
            lane.hiddenTotal += 1;
            hidden[cls] = (hidden[cls] || 0) + 1;
        }
    });

    const out = [...lanes.values()].sort((a, b) =>
        laneRank(a) - laneRank(b) || b.lastTs - a.lastTs || a.wid.localeCompare(b.wid));
    return { lanes: out, hidden };
}

/**
 * Fold the GONE lanes into one group — the default view is the LIVE fleet.
 *
 * Every dispatched agent that finishes leaves its lane behind forever (the lane list
 * comes from the LOG, on purpose — key it on the live fleet and a dead agent's whole
 * history evaporates the moment tmux reaps its window). So after ONE day the feed was
 * 8 lanes, 2 live, 5 corpses, and the lanes you actually care about were buried under
 * a graveyard that only ever grows. The data is right; the default VIEW was wrong.
 *
 * This is PRESENTATION, not a second model: nothing is dropped, nothing is cached, and
 * "gone" is not a flag — it is simply `lane.status`, which comes from the live tmux
 * table (agent_manager.status_by_wid, the one liveness authority) at every render. A
 * recycled `@72` therefore cannot resurrect a dead agent: whatever the table says NOW
 * is what the lane is now.
 *
 * ⚠️ ONE exception, and it is CMX-62's: a run can be `awaiting_review` — wanting you,
 * badly — while its window is ALREADY REAPED, because a dispatched agent kills its own
 * window before the run reconciles. "Window gone" is emphatically not "finished, ignore
 * me". Such a lane is kept OUT of the graveyard and wears a `◆ REVIEW WAITING` badge.
 * It is not hoisted to the top: the lane sort is attention-ordered and a PR waiting on
 * you is not the same interrupt as an agent blocked on a prompt — it keeps its place in
 * the `gone` bucket, visible rather than urgent.
 *
 * @param lanes        buildLanes(...).lanes — sorted, untouched
 * @param openReviews  task ids currently `awaiting_review` (the runs DB, via
 *                     /api/dispatcher). `null` = NOT KNOWN — and an unknown review
 *                     is treated as OPEN, because the failure we refuse is burying one.
 * @returns {lanes, buried, agents, events} — `lanes` to render, `buried` behind the
 *          collapsed row (in the same order), and the counts that row must SAY.
 */
export function splitGone(lanes, openReviews) {
    const known = openReviews == null ? null : new Set(openReviews);
    const shown = [];
    const buried = [];
    (lanes || []).forEach(lane => {
        if (!lane || lane.status !== 'gone') { if (lane) shown.push(lane); return; }
        lane.openReview = known
            ? lane.reviewTasks.some(t => known.has(t))
            : lane.reviewTasks.length > 0;
        (lane.openReview ? shown : buried).push(lane);
    });
    return {
        lanes: shown,
        buried,
        agents: buried.length,
        events: buried.reduce((n, l) => n + l.total, 0),
    };
}

// "5 finished agents · 47 events" — what the collapsed graveyard row is holding, said
// out loud. A row that folds away five agents without naming them is a lie, not a filter.
export function goneSummary(agents, events) {
    return `${agents} finished agent${agents === 1 ? '' : 's'}`
        + ` · ${events} event${events === 1 ? '' : 's'}`;
}

// The flat/chronological escape hatch: the same rows, ungrouped, NEWEST first.
// Lanes cost you global chronology; this gives it back.
export function flatRows(events, classes) {
    const want = new Set(classes && classes.length ? classes : DEFAULT_CLASSES);
    return (events || [])
        .filter(e => e && want.has(classOf(e.type)))
        .map(e => ({ ...e, cls: classOf(e.type) }))
        .sort((a, b) => (b.seq || 0) - (a.seq || 0));
}

// --- the cursor: a bounded read that RESUMES, and never SKIPS -----------------
//
// /api/log hands back the `limit` OLDEST events after the cursor, so on a busy log one
// call does not reach the tail — a reader drains until it does. The whole contract is
// one line of this loop: the cursor advances to `next_seq`, NEVER to `last_seq`.
//
// `last_seq` is the LOG's tail. When a read was truncated by `limit` it sits far past
// the last event we were actually HANDED, so resuming from it drops every event in
// between — invisibly, which is the exact hole event_log's `next_seq`/`gap` design
// exists to prevent (`event_log.read()`: "a bounded read is resumable and can never skip
// an event the caller has not seen"). `last_seq` has one legitimate use, and only one:
// it says whether we have reached the tail yet, i.e. whether to keep draining.
//
// This lives here, DOM-free, because that is what makes the rule provable: the drain is
// tested against a fake log in tests/feed.test.mjs, where a resume-from-last_seq reader
// is run over the same log and SHOWN to skip. (It used to be "asserted" by grepping
// feed.js for the string `batch.last_seq` — which failed the correct code, since the
// tail test legitimately reads it. A grep tests spelling; this tests behaviour.)
//
// `fetchBatch({after_seq, after_boot, limit})` is injected — so no fetch in here either.
export async function drainLog(fetchBatch, { cursor = null, boot = null, limit, maxFetches }) {
    let events = [];
    let gap = null;
    let cleared = false;                    // did a gap invalidate what the caller holds?
    for (let i = 0; i < maxFetches; i++) {
        let batch;
        try {
            batch = await fetchBatch({ after_seq: cursor, after_boot: boot, limit });
        } catch (e) {
            break;                          // transient — the cursor stands, the next tick retries
        }
        if (!batch || !Array.isArray(batch.events)) break;
        if (batch.gap) {
            // Told, not guessed. The server has already re-anchored the read; we hand the
            // notice up so a hole in the record is never invisible — a plausible-looking
            // wrong continuation is worse than an admitted gap.
            gap = batch.gap;
            events = [];
            cleared = true;
        }
        events = events.concat(batch.events);
        cursor = batch.next_seq;
        boot = batch.boot_id;
        if (!batch.events.length || batch.next_seq >= batch.last_seq) break;   // at the tail
    }
    return { events, cursor, boot, gap, cleared };
}

// "212 tool calls · 2 prompts" — what a collapsed row is hiding, said out loud.
// A hidden count that does not name what it hid is the same silence, one step up.
export function hiddenSummary(counts) {
    return CLASS_IDS
        .filter(c => (counts || {})[c])
        .map(c => {
            const n = counts[c];
            const label = CLASSES[c].label.toLowerCase();
            return `${n} ${n === 1 ? label.replace(/s$/, '') : label}`;
        })
        .join(' · ');
}
