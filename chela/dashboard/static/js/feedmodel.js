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
//      many rows that is, with a button to show them.
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

// The flat/chronological escape hatch: the same rows, ungrouped, NEWEST first.
// Lanes cost you global chronology; this gives it back.
export function flatRows(events, classes) {
    const want = new Set(classes && classes.length ? classes : DEFAULT_CLASSES);
    return (events || [])
        .filter(e => e && want.has(classOf(e.type)))
        .map(e => ({ ...e, cls: classOf(e.type) }))
        .sort((a, b) => (b.seq || 0) - (a.seq || 0));
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
