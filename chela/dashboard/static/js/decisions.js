// ---------------------------------------------------------------------------
// DECISIONS LOG — the durable, owner-independent home for what the decisions
// inbox has ever said. Lives in a topbar popover (index.html `#decisions-menu`,
// anchored off `#btn-decisions`), not gated behind any nav tab (cmx-106 first
// shipped it inside the Personas panel — cmx-107 moved it into an always-visible
// sidebar section — CMX-171 moved it again, out of the sidebar into this popover,
// so the sidebar stops permanently spending a third section on it. It is still
// seeded/ticked exactly the same as before; only the DOM it paints into moved).
//
// This is deliberately NOT a second store. chela/inbox.py::tick() appends every
// event to event_log (chela/event_log.py) whether or not a live session is
// registered as the orchestrator — "the log is owner-independent" is already
// true of the write path (see inbox.tick's unconditional `event_log.from_inbox`
// loop). So this popover is a FILTERED READ of the same /api/log the Feed reads
// (feed.js), narrowed to the kinds chela/inbox.py actually queues/logs, plus an
// owner chip (orchestrator.js) — a decision is never lost here even when the
// chip reads "nobody": that IS this panel being the fallback home.
//
// Wiring (cmx-107, unchanged by CMX-171): main.js seeds it ONCE on page load
// (`enterDecisions()`, unconditional — no tab-open/popover-open needed) and
// keeps it live off the SSE `log`/`orchestrator` deltas (sse.js) continuously,
// plus a `tickDecisions()` fallback poll each refresh() tick — the same pattern
// refreshSidebar() uses for Sessions. Nothing here is gated on `currentTab`.
// The header dot (`#decisions-dot`) mirrors the same state, so the state is
// legible without opening the popover.
//
// The cursor/drain contract is identical to the Feed's (feedmodel.js:
// drainLog) — resume from `next_seq`, never `last_seq`; a rotted cursor comes
// back as a `gap`, rendered, not papered over.
//
// The header ALSO carries an unseen-count badge (`#decisions-unread`,
// decisionsmodel.js) — the dot says "how healthy is the owner", the badge says
// "how many decisions have you not looked at yet", and both are always on:
// neither is gated behind the popover being open. `_lastSeenSeq` persists in
// localStorage (`chela.decisions.lastSeen`) so the badge survives a reload;
// opening the popover is the only thing that advances it.
// ---------------------------------------------------------------------------
import { $, api, escHtml } from './util.js';
import { CLASSES, classOf, drainLog } from './feedmodel.js';
import { formatUnreadCount, maxSeq, seedLastSeen, unreadCount, unreadUrgency } from './decisionsmodel.js';
import { onOrchestratorChange, orchestratorState, refreshOrchestratorStatus } from './orchestrator.js';

const LAST_SEEN_KEY = 'chela.decisions.lastSeen';

function _loadLastSeen() {
    try {
        const raw = localStorage.getItem(LAST_SEEN_KEY);
        if (raw == null) return null;
        const n = Number(raw);
        return Number.isFinite(n) ? n : null;
    } catch (e) { return null; }
}

function _persistLastSeen(n) {
    try { localStorage.setItem(LAST_SEEN_KEY, String(n)); } catch (e) { /* ignore */ }
}

// null = never seeded (fresh browser, or storage unavailable) — seeded lazily
// once the first real batch of events lands (see _render), never eagerly to 0.
let _lastSeenSeq = _loadLastSeen();

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
let _loaded = false;   // true once the first real batch of events has landed

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
        _loaded = true;
    } finally {
        _inflight = false;
    }
    _render();
}

// The one-time page-load seed (main.js) — a fresh read of both the owner and
// the log, from scratch (reset=true). Nothing else calls this with reset=true.
async function enterDecisions() {
    await Promise.all([refreshOrchestratorStatus(), _refreshLog(true)]);
}

// The fallback poll under the SSE deltas — runs every refresh() tick (main.js),
// unconditionally, regardless of which nav tab is on screen.
async function tickDecisions() {
    await Promise.all([refreshOrchestratorStatus(), _refreshLog()]);
}

// The SSE `log` delta: the log's seq moved, so fetch from our own cursor (the
// frame itself carries no events). Fired for every frame, tab-independent —
// this section is always on screen (sse.js).
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

// The header button's dot (#decisions-dot) — the same live/nobody/dangling/
// unverified state the chip conveys, surfaced without opening the popover.
function _renderDot() {
    const dot = $('#decisions-dot');
    if (!dot) return;
    const s = orchestratorState();
    const meta = CHIP_META[s.state] || CHIP_META.unregistered;
    dot.hidden = false;
    dot.className = 'decisions-dot dot-' + meta.cls;
    dot.title = meta.word;
}

// The header button's unread badge (#decisions-unread) — a numeric count,
// distinct from the dot: the dot mirrors orchestrator health, this counts
// decision events with `seq > _lastSeenSeq` that this browser has never had
// the popover open for. Seeded lazily (never eagerly to 0) the first time a
// real batch of events lands, so a fresh browser never badges the backlog.
function _renderBadge() {
    const badge = $('#decisions-unread');
    if (!badge) return;
    if (!_loaded) { badge.hidden = true; return; }
    if (_lastSeenSeq == null) {
        _lastSeenSeq = seedLastSeen(_events);
        _persistLastSeen(_lastSeenSeq);
    }
    const count = unreadCount(_events, _lastSeenSeq);
    badge.hidden = count === 0;
    badge.textContent = formatUnreadCount(count);
    const urgency = unreadUrgency(_events, _lastSeenSeq);
    badge.className = 'decisions-unread' + (count > 0 && urgency !== 'neutral' ? ' decisions-unread-' + urgency : '');
}

// Advance the "seen" cursor to the newest held event — called on popover open
// (never lazily from a render) so a badge only clears because a human actually
// looked, not because a render happened to run.
function _markSeen() {
    _lastSeenSeq = maxSeq(_events);
    _persistLastSeen(_lastSeenSeq);
    _renderBadge();
}

function _render() {
    _renderDot();
    _renderBadge();
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

// --- Header popover: anchored + light-dismiss, same pattern as nav.js's
// openPrimaryMenu/openNewMenu (#primary-menu/#new-menu). Opening marks every
// currently-held event as seen (clearing the unread badge) and ticks the log
// so the popover is never showing a stale read the moment it appears.
function openDecisionsMenu(ev) {
    if (ev) ev.stopPropagation();
    const m = $('#decisions-menu');
    if (!m) return;
    const anchor = (ev && ev.currentTarget) || document.getElementById('btn-decisions');
    // Show it BEFORE measuring: a display:none element has no offsetWidth.
    m.style.display = 'block';
    const r = anchor.getBoundingClientRect();
    m.style.top = (r.bottom + 6) + 'px';
    m.style.left = Math.max(8, r.right - m.offsetWidth) + 'px';
    _markSeen();
    tickDecisions();
    setTimeout(() => document.addEventListener('click', hideDecisionsMenu, { once: true }), 0);
}

function hideDecisionsMenu() {
    const m = $('#decisions-menu');
    if (m) m.style.display = 'none';
}

// --- Stage 0: ES-module exports ---
export { DECISION_TYPES, enterDecisions, hideDecisionsMenu, onDecisionsLogDelta, openDecisionsMenu, tickDecisions };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { hideDecisionsMenu, openDecisionsMenu });
