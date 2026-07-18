// ---------------------------------------------------------------------------
// DECISIONS LOG — the durable, owner-independent home for what the decisions
// inbox has ever said. Lives in the Personas panel, next to the persona layer
// it is the operational half of (docs/PERSONA_PATTERN.md).
//
// This is deliberately NOT a second store. chela/inbox.py::tick() appends every
// event to event_log (chela/event_log.py) whether or not a live session is
// registered as the orchestrator — "the log is owner-independent" is already
// true of the write path (see inbox.tick's unconditional `event_log.from_inbox`
// loop). So this panel is a FILTERED READ of the same /api/log the Feed reads
// (feed.js), narrowed to the kinds chela/inbox.py actually queues/logs, plus an
// owner chip (orchestrator.js) — a decision is never lost here even when the
// chip reads "nobody": that IS this panel being the fallback home.
//
// The cursor/drain contract is identical to the Feed's (feedmodel.js:
// drainLog) — resume from `next_seq`, never `last_seq`; a rotted cursor comes
// back as a `gap`, rendered, not papered over.
// ---------------------------------------------------------------------------
import { $, api, escHtml } from './util.js';
import { CLASSES, classOf, drainLog } from './feedmodel.js';
import { onOrchestratorChange, orchestratorState, refreshOrchestratorStatus } from './orchestrator.js';

// Every kind chela/inbox.py ever queues or logs about a dispatch/watch outcome
// (see agent_events/run_events/_gone_event/_epoch_lost_event/_undeliverable).
// Deliberately NOT the Feed's tool-call/prompt firehose — this panel is
// decisions, not the whole log.
const DECISION_TYPES = [
    'run_review', 'run_needs_human', 'run_changes_requested', 'run_failed',
    'finished', 'blocked', 'died', 'gone_unknown', 'completed_gone',
    'watch_epoch_lost', 'inbox_undeliverable', 'inbox_self_healed',
];

const DECISIONS_LIMIT = 300;     // per fetch
const DECISIONS_MAX = 500;       // events kept in the browser
const CATCHUP_FETCHES = 8;       // bounded drain, mirrors feed.js

let _cursor = null;
let _boot = null;
let _events = [];
let _gap = null;
let _inflight = false;

function _fetchBatch({ after_seq, after_boot, limit }) {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (after_seq != null) qs.set('after_seq', String(after_seq));
    if (after_boot) qs.set('after_boot', after_boot);
    DECISION_TYPES.forEach(t => qs.append('type', t));
    return api('/api/log?' + qs.toString());
}

async function _refreshLog(reset = false) {
    if (reset) { _cursor = null; _boot = null; _events = []; _gap = null; }
    if (_inflight) return;
    _inflight = true;
    try {
        const drained = await drainLog(_fetchBatch, {
            cursor: _cursor, boot: _boot, limit: DECISIONS_LIMIT, maxFetches: CATCHUP_FETCHES,
        });
        if (drained.cleared) _events = [];      // a gap invalidated what we were holding
        if (drained.gap) _gap = drained.gap;    // and we keep SAYING so, on screen
        _events = _events.concat(drained.events).slice(-DECISIONS_MAX);
        _cursor = drained.cursor;
        _boot = drained.boot;
    } finally {
        _inflight = false;
    }
    _render();
}

// Entering the panel: a fresh read of both the owner and the log.
async function enterDecisions() {
    await Promise.all([refreshOrchestratorStatus(), _refreshLog(true)]);
}

// The fallback poll under the SSE deltas.
async function tickDecisions() {
    await Promise.all([refreshOrchestratorStatus(), _refreshLog()]);
}

// The SSE `log` delta, while this panel is on screen: the log's seq moved, so
// fetch from our own cursor (the frame itself carries no events).
function onDecisionsLogDelta() {
    _refreshLog();
}

onOrchestratorChange(() => _render());

// --- render -------------------------------------------------------------

const CHIP_META = {
    ok: { glyph: '●', word: 'live', cls: 'ok' },
    unregistered: { glyph: '○', word: 'nobody — logged here', cls: 'none' },
    dangling: { glyph: '✕', word: 'dangling', cls: 'bad' },
    gone: { glyph: '✕', word: 'gone', cls: 'bad' },
    unstamped: { glyph: '◐', word: 'unverified', cls: 'warn' },
};

function _chipHtml() {
    const s = orchestratorState();
    const meta = CHIP_META[s.state] || CHIP_META.unregistered;
    const who = s.wid ? `${escHtml(s.name || s.wid)} (${escHtml(s.wid)})` : 'nobody — this panel is home';
    const queued = s.queued ? `<span class="decisions-chip-queued">${escHtml(String(s.queued))} queued</span>` : '';
    const why = s.why ? `<span class="decisions-chip-why">${escHtml(s.why)}</span>` : '';
    return `<div class="decisions-chip decisions-chip-${escHtml(meta.cls)}">
        <span class="decisions-chip-glyph" title="${escHtml(meta.word)}">${escHtml(meta.glyph)} ${escHtml(meta.word)}</span>
        <span class="decisions-chip-who">${who}</span>
        ${queued}
        ${why}
    </div>`;
}

function _ts(e) {
    return new Date((e.ts || 0) * 1000).toLocaleTimeString('en-US',
        { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

// Same markup/classes as the Feed's row (feed-row/feed-cls-*/feed-glyph/…) so a
// decision reads identically here and there — one visual language, not two.
function _rowHtml(e) {
    const cls = classOf(e.type);
    const meta = CLASSES[cls] || CLASSES.other;
    return `<div class="feed-row feed-cls-${escHtml(cls)}">
        <span class="feed-seq">${escHtml(String(e.seq || ''))}</span>
        <span class="feed-ts">${escHtml(_ts(e))}</span>
        <span class="feed-glyph" title="${escHtml(meta.word)}">${escHtml(meta.glyph)} ${escHtml(meta.word)}</span>
        <span class="feed-wid">${escHtml(e.wid || 'chela')}</span>
        <span class="feed-summary">${escHtml(e.summary || e.type || '')}</span>
    </div>`;
}

function _gapHtml() {
    if (!_gap) return '';
    return `<div class="feed-gap">⚠ ${escHtml(_gap.reason || 'a gap in the decisions log')}</div>`;
}

function _render() {
    const chip = $('#decisions-chip');
    if (chip) chip.innerHTML = _chipHtml();
    const host = $('#decisions-list');
    if (!host) return;
    if (!_events.length) {
        host.innerHTML = _gapHtml() + '<div class="side-empty">No decisions logged yet</div>';
        return;
    }
    const rows = _events.slice().sort((a, b) => (b.seq || 0) - (a.seq || 0));
    host.innerHTML = _gapHtml() + rows.map(_rowHtml).join('');
}

// --- Stage 0: ES-module exports ---
export { DECISION_TYPES, enterDecisions, onDecisionsLogDelta, tickDecisions };
