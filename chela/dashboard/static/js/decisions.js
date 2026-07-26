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
//
// CMX-178: two additions on top of the read-only feed above, both purely
// client-side over data already on the wire (no new endpoint, no server
// change):
//   1. SEARCH (`#decisions-search`, setDecisionsQuery) — a substring filter
//      over the events already held, so an OLD decision the short list has
//      scrolled past is still findable.
//   2. CLICK-THROUGH (openDecisionTicket) — a row click resolves the event's
//      task_id against /api/dispatcher (the SAME poll Work's board reads),
//      because the decision payload alone is missing exactly the fields the
//      ticket exists to show (brief/judge_state/pr_checks/body — see
//      decisionsmodel.js's findDispatcherRun doc comment). Found → that
//      authoritative run/task object opens, identical to what a Work card
//      would show for it. Not found (aged out of the dispatcher's bounded
//      recent window) or the fetch fails → falls back to
//      decisionsmodel.js's partialItemFromDecisionPayload, built from the
//      event's own run_*/_window_payload fields (task_id/title/branch_name/
//      pr_url/pr_state/attempt, and on rework kinds reviews/rework_count/
//      last_error — see chela/inbox.py), but visibly marked partial rather
//      than rendering "No brief recorded" as if the run genuinely had none.
// ---------------------------------------------------------------------------
import { $, api, escHtml } from './util.js';
import { CLASSES, classOf, drainLog } from './feedmodel.js';
import {
    filterDecisionEvents, findDispatcherRun, formatUnreadCount, itemFromDecisionPayload,
    maxSeq, partialItemFromDecisionPayload, seedLastSeen, unreadCount, unreadUrgency,
} from './decisionsmodel.js';
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

// The SEARCH box (CMX-178) — find an old decision the short list has scrolled
// past. Client-side over the events already held (not a server query): this
// popover only ever holds DECISIONS_MAX of them, so a substring filter over
// that is instant and needs no new endpoint. Persists across a popover
// close/reopen (the input itself stays in the DOM — only `display` toggles),
// deliberately: closing the popover mid-search should not lose your filter.
let _query = '';

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
//
// A row whose payload names a task_id (itemFromDecisionPayload returns non-
// null — CMX-178) is click-through: it carries `data-seq` and an onclick that
// resolves back to this same event (openDecisionTicket, below) and opens it
// in the task-detail modal Work already uses. A bare window/inbox-plumbing
// event (no task_id — watch_epoch_lost, inbox_undeliverable, …) renders with
// no click affordance: there is no ticket to go to.
function _rowHtml(e) {
    const cls = classOf(e.type);
    const meta = CLASSES[cls] || CLASSES.other;
    const clickable = itemFromDecisionPayload(e) != null;
    const clickAttrs = clickable
        ? ` data-seq="${escHtml(String(e.seq || ''))}" onclick="chela.openDecisionTicket(this)" title="Open this task"`
        : '';
    return `<div class="feed-row feed-cls-${escHtml(cls)}${clickable ? ' feed-row-clickable' : ''}"${clickAttrs}>
        <span class="feed-seq">${escHtml(String(e.seq || ''))}</span>
        <span class="feed-ts">${escHtml(_ts(e))}</span>
        <span class="feed-glyph" title="${escHtml(meta.word)}">${escHtml(meta.glyph)} ${escHtml(meta.word)}</span>
        <span class="feed-wid">${escHtml(e.wid || 'chela')}</span>
        <span class="feed-summary">${escHtml(e.summary || e.type || '')}</span>
    </div>`;
}

// Resolves a clicked row back to its event (by `seq`, unique per the log),
// resolves the event's task_id against the dispatcher's own view of the
// world, and opens the SAME task-detail modal a Kanban card opens
// (taskmodal.js's openTaskModal). Reached via window.chela rather than a
// static import: taskmodal.js sits inside the Work module cluster
// (dispatcher.js → work.js → kanban.js/schedules.js → nav.js → main.js), and
// main.js starts its own `setInterval` refresh loop at import time — pulling
// that whole graph into this popover's otherwise-light module set would drag
// a second poll loop in with it for no reason. window.chela is the exact
// seam index.html's inline onclick handlers already reach through for the
// same kind of cross-module call, so this is that same seam, called from JS
// instead of from markup.
//
// One `/api/dispatcher` fetch per click (not a poll, not a re-import of
// work.js) — the dispatcher object is the complete ticket, so it is tried
// FIRST; the payload-only fallback only ever opens when that lookup comes up
// empty or the fetch itself fails. Never throws, never blocks a dead click:
// every path below still opens something.
async function openDecisionTicket(el) {
    const seq = Number(el && el.dataset && el.dataset.seq);
    const e = _events.find(ev => ev && ev.seq === seq);
    const fallback = e && itemFromDecisionPayload(e);
    if (!fallback) return;   // no task_id on this event — nothing to open
    let item = null;
    try {
        const data = await api('/api/dispatcher');
        item = findDispatcherRun(data, fallback.task_id);
    } catch (err) { /* unreachable dispatcher — fall through to the partial ticket */ }
    const toOpen = item || partialItemFromDecisionPayload(e);
    if (toOpen && window.chela && typeof window.chela.openTaskModal === 'function') {
        window.chela.openTaskModal(toOpen);
    }
}

// Wired to the search box's oninput (index.html #decisions-search).
function setDecisionsQuery(value) {
    _query = value || '';
    _render();
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
    // The search box filters what is RENDERED, not what is held — `_events`
    // itself is untouched, so clearing the query brings the full backlog
    // straight back with no re-fetch.
    const filtered = filterDecisionEvents(_events, _query);
    if (!filtered.length) {
        host.innerHTML = _gapHtml() + `<div class="side-empty">No decisions match "${escHtml(_query)}"</div>`;
        return;
    }
    const rows = filtered.slice().sort((a, b) => (b.seq || 0) - (a.seq || 0));
    // ⛔ Say what was actually searched. This filter runs over the events the
    // popover currently HOLDS (a bounded buffer — `_refreshLog` pulls batches,
    // it does not have the whole log), so a bare filtered list silently implies
    // "these are the only matches in your history" when it means "these are the
    // matches among the N I have". Only shown while a query is active; with an
    // empty box the list is simply everything held and there is nothing to
    // qualify. /api/log has no free-text search — widening it is the only way
    // to make this claim bigger, and that is deliberately not what this does.
    const scope = _query.trim()
        ? `<div class="decisions-scope">${filtered.length} of ${_events.length} loaded</div>`
        : '';
    host.innerHTML = _gapHtml() + scope + rows.map(_rowHtml).join('');
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
export {
    DECISION_TYPES, enterDecisions, hideDecisionsMenu, onDecisionsLogDelta,
    openDecisionsMenu, openDecisionTicket, setDecisionsQuery, tickDecisions,
};

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { hideDecisionsMenu, openDecisionsMenu, openDecisionTicket, setDecisionsQuery });
