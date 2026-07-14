// ---------------------------------------------------------------------------
// FEED — the event log, in the browser. PART 2: AGENT LANES.
//
// The fleet is the spine; the log is what each agent is SAYING. Rows group under
// the agent that produced them, and the lanes are ordered by ATTENTION rather than
// by time — an agent that needs you sorts to the TOP and wears a `◆ NEEDS YOU`
// badge, so "who wants me" is answerable without reading a single row.
//
// This module owns the plumbing and the DOM. The grouping/sorting/filtering rules
// live in feedmodel.js, which is pure and node-tested (tests/feed.test.mjs) — the
// properties that decide whether this layout works (a dead agent still has a lane;
// an unattributed event is never GUESSED into one; nothing is hidden silently) are
// provable there without a browser.
//
// The contract underneath, unchanged from part 1:
//
//   * ONE reader — GET /api/log, a thin wrapper over event_log.read(), the same
//     call `chela events` makes. No second event source.
//   * A CURSOR — we resume from `next_seq` (never `last_seq`: a bounded read
//     truncates, and last_seq would skip everything past the truncation), pinned
//     to the `boot_id` it came from.
//   * A GAP is rendered, not papered over. If the log rotated past our cursor, or
//     the daemon restarted, the server says so and we SAY so.
//   * It rides the EXISTING EventSource: sse.js pushes a `log` delta carrying the
//     new seq, we fetch. ⛔ No second EventSource. If the stream never connects,
//     the view's 30s tick covers it — the same degrade-to-polling net as every
//     other view.
//
// READ-ONLY, on purpose: the answer path is the phone (CMX-50/54). A second way to
// answer a gate is a second thing to keep correct.
// ---------------------------------------------------------------------------
import { $, api, attrEsc, escHtml, setAgentsCache } from './util.js';
import {
    CLASSES, CLASS_IDS, DEFAULT_CLASSES, LANE_ORDER,
    buildLanes, classOf, drainLog, flatRows, goneSummary, hiddenSummary, laneRank, splitGone,
} from './feedmodel.js';
import { awaitingReviewIds } from './work.js';

const FEED_LIMIT = 500;      // per fetch
const FEED_MAX = 1500;       // events kept in the browser
const CATCHUP_FETCHES = 8;   // bounded drain: /api/log serves the OLDEST rows after the
                             // cursor, so reaching the tail takes a few calls, not one
const LANE_ROWS = 40;        // newest rows drawn per lane; the rest is counted, not lost

let _cursor = null;          // last seq we have seen (null = "give me the tail")
let _boot = null;            // the boot_id that seq belongs to
let _events = [];            // oldest → newest
let _gap = null;             // non-null once the server has told us we missed something
let _inflight = false;       // one fetch at a time — the SSE delta can outpace the network
let _fleet = [];             // /api/agents — the ONE busy/idle/waiting authority
let _collapsed = new Set();  // lanes the viewer has folded shut
let _graveyard = false;      // is the folded GONE group open? (per view, not persisted:
                             // collapsed-by-default IS the fix — a sticky "open" undoes it)

// Persisted UI state: the grouping and the filter survive a reload.
const MODE_KEY = 'chela_feed_mode';
const CLASSES_KEY = 'chela_feed_classes';

function _loadMode() {
    try {
        const v = localStorage.getItem(MODE_KEY);
        return v === 'flat' ? 'flat' : 'lanes';
    } catch (e) { return 'lanes'; }
}

function _loadClasses() {
    try {
        const raw = JSON.parse(localStorage.getItem(CLASSES_KEY) || 'null');
        if (Array.isArray(raw) && raw.length) return raw.filter(c => CLASS_IDS.includes(c));
    } catch (e) { /* fall through to the default */ }
    return DEFAULT_CLASSES.slice();
}

let _mode = _loadMode();
let _classes = _loadClasses();

// --- the reader -------------------------------------------------------------

// A batch worth re-reading the fleet for. The tool-call firehose is 86% of the log and
// /api/agents costs tmux calls, so a status refetch rides ATTENTION events only — which
// is exactly the traffic that can flip a lane to NEEDS YOU.
const ATTENTION = ['gate', 'denied', 'run', 'lifecycle'];

function _attentionInBatch(events) {
    return (events || []).some(e => e && ATTENTION.includes(classOf(e.type)));
}

// One HTTP call: /api/log from a cursor. The DRAIN around it (resume from next_seq,
// never last_seq; stop at the tail; surface a gap) is pure and lives in feedmodel.js,
// where it is tested against a fake log — this half is only the wire.
function _fetchBatch({ after_seq, after_boot, limit }) {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (after_seq != null) qs.set('after_seq', String(after_seq));
    if (after_boot) qs.set('after_boot', after_boot);
    return api('/api/log?' + qs.toString());
}

// Fetch from our cursor and render. `reset` starts over (entering the view).
async function refreshFeed(reset = false) {
    if (reset) { _cursor = null; _boot = null; _events = []; _gap = null; }
    if (_inflight) return;
    _inflight = true;
    let fresh = [];
    try {
        const drained = await drainLog(_fetchBatch, {
            cursor: _cursor, boot: _boot, limit: FEED_LIMIT, maxFetches: CATCHUP_FETCHES,
        });
        if (drained.cleared) _events = [];   // a gap invalidated what we were holding
        if (drained.gap) _gap = drained.gap; // and we keep SAYING so, on screen
        fresh = drained.events;
        _events = _events.concat(fresh);
        _cursor = drained.cursor;
        _boot = drained.boot;
    } finally {
        _inflight = false;
    }
    _events = _events.slice(-FEED_MAX);
    // A gate (or a run/lifecycle turn) can change WHO NEEDS YOU. Re-read the fleet so
    // the lane re-sorts to the top now, rather than at the next 30s tick.
    if (_attentionInBatch(fresh)) await refreshFleet();
    _renderFeed();
}

// The fleet's live status. `waiting` = blocked on a permission/question prompt = it
// wants you; agent_manager.status_by_wid is the ONE authority behind /api/agents and
// this view deliberately adds no second one.
async function refreshFleet() {
    try {
        const agents = await api('/api/agents');
        if (Array.isArray(agents)) {
            _fleet = agents;
            setAgentsCache(agents);          // shared, so this is not a second poller
        }
    } catch (e) { /* transient — lanes still render from the log */ }
}

// Entering the view: a fresh read of both, then draw.
async function enterFeed() {
    await refreshFleet();
    await refreshFeed(true);
}

// The 30s tick: the safety net under the SSE delta (and what keeps a lane's status
// honest when nothing is being logged).
async function tickFeed() {
    await refreshFleet();
    await refreshFeed();
}

// The SSE `log` delta: the log's seq moved. The frame carries no events — it is a
// notification — so we fetch from OUR cursor and a dropped frame costs nothing.
//
// Note what this does NOT do on a fresh boot epoch (a daemon restart, or a
// `chela events rotate`): it does not quietly start over. Resetting the cursor
// ourselves would make the next read cursorless, the server would have nothing to
// object to, and the hole would be papered over by the very code meant to notice it.
// We hand our STALE cursor back instead — event_log.read() compares the boot_id, says
// "events from that window were never appended", and re-anchors the read. Told, not
// guessed: the banner is the server's answer, not our assumption.
function onLogDelta() {
    refreshFeed();
}

// --- the controls -----------------------------------------------------------

function feedSetMode(mode) {
    _mode = mode === 'flat' ? 'flat' : 'lanes';
    try { localStorage.setItem(MODE_KEY, _mode); } catch (e) { /* ignore */ }
    _renderFeed();
}

function feedToggleClass(cls) {
    if (!CLASS_IDS.includes(cls)) return;
    _classes = _classes.includes(cls) ? _classes.filter(c => c !== cls) : _classes.concat(cls);
    try { localStorage.setItem(CLASSES_KEY, JSON.stringify(_classes)); } catch (e) { /* ignore */ }
    _renderFeed();
}

function feedToggleLane(wid) {
    if (_collapsed.has(wid)) _collapsed.delete(wid); else _collapsed.add(wid);
    _renderFeed();
}

function feedToggleGraveyard() {
    _graveyard = !_graveyard;
    _renderFeed();
}

// --- the render -------------------------------------------------------------

function _ts(e) {
    return new Date((e.ts || 0) * 1000).toLocaleTimeString('en-US',
        { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

// A row says what it is in a GLYPH and a WORD before any colour is involved — Liav is
// red-weak, and a state carried by hue alone is a state he cannot read.
function _rowHtml(e, withLane) {
    const meta = CLASSES[e.cls] || CLASSES.other;
    const lane = withLane
        ? `<span class="feed-wid">${escHtml(e.wid || 'chela')}</span>` : '';
    return `<div class="feed-row feed-cls-${escHtml(e.cls)}">
        <span class="feed-seq">${escHtml(String(e.seq))}</span>
        <span class="feed-ts">${escHtml(_ts(e))}</span>
        <span class="feed-glyph" title="${attrEsc(meta.word)}">${escHtml(meta.glyph)} ${escHtml(meta.word)}</span>
        ${lane}
        <span class="feed-summary">${escHtml(e.summary || e.type || '')}</span>
    </div>`;
}

// What the filter is hiding, per lane, said out loud with a way to see it. A
// collapsed row that does not say how many it swallowed is the same silence.
function _hiddenHtml(counts, total) {
    if (!total) return '';
    const firehose = counts.tool ? 'tool' : (Object.keys(counts)[0] || '');
    return `<div class="feed-hidden">⋯ ${total} hidden — ${escHtml(hiddenSummary(counts))}
        ${firehose ? `<button type="button" class="feed-show-btn"
            onclick="chela.feedToggleClass('${attrEsc(firehose)}')">show ${escHtml((CLASSES[firehose] || CLASSES.other).label.toLowerCase())}</button>` : ''}
    </div>`;
}

// The folded graveyard: ONE row that says exactly what it is holding, with a click to
// open it. The lanes behind it are COLLAPSED, not deleted — they are still in the model,
// still in the flat view, and one click away here.
function _goneHtml(group) {
    if (!group.agents) return '';
    return `<div class="feed-hidden feed-graveyard" role="button" tabindex="0"
            onclick="chela.feedToggleGraveyard()">
        <span class="lane-fold">${_graveyard ? '▾' : '▸'}</span>
        <span>✕ gone — ${escHtml(goneSummary(group.agents, group.events))}</span>
        <button type="button" class="feed-show-btn"
            onclick="event.stopPropagation(); chela.feedToggleGraveyard()">
            ${_graveyard ? 'hide' : 'show'} finished agents</button>
    </div>`;
}

function _laneHtml(lane) {
    const collapsed = _collapsed.has(lane.wid);
    const rows = lane.events.slice(-LANE_ROWS);          // chronological, newest at the foot
    const earlier = lane.events.length - rows.length;
    // Two different asks, two different words. `NEEDS YOU` = an agent sitting on a prompt
    // right now; `REVIEW WAITING` = a dead window whose PR is still open (CMX-62) — the
    // reason that lane is not in the graveyard, said in the header so it reads as a
    // GLYPH + WORD and never as a hue (Liav is red-weak).
    const badge = lane.needsYou
        ? '<span class="lane-needs">◆ NEEDS YOU</span>'
        : (lane.openReview ? '<span class="lane-needs lane-review">◆ REVIEW WAITING</span>' : '');
    const status = lane.system ? 'system' : lane.status;
    const project = lane.project ? `<span class="lane-project">${escHtml(lane.project)}</span>` : '';
    const wid = lane.system ? '' : `<span class="lane-wid">${escHtml(lane.wid)}</span>`;
    const body = collapsed ? '' : `<div class="lane-rows">
            ${earlier > 0 ? `<div class="feed-hidden">⋯ ${earlier} earlier rows not drawn</div>` : ''}
            ${rows.map(e => _rowHtml(e, false)).join('')}
            ${_hiddenHtml(lane.hidden, lane.hiddenTotal)}
            ${!rows.length && !lane.hiddenTotal ? '<div class="side-empty">Nothing said yet.</div>' : ''}
        </div>`;
    return `<section class="feed-lane${lane.needsYou || lane.openReview ? ' lane-attention' : ''}${lane.system ? ' lane-system' : ''}"
                     data-status="${attrEsc(status)}">
        <header class="lane-head" onclick="chela.feedToggleLane('${attrEsc(lane.wid)}')">
            <span class="lane-fold">${collapsed ? '▸' : '▾'}</span>
            <span class="lane-name">${escHtml(lane.name)}</span>
            ${wid}
            ${project}
            <span class="lane-status lane-status-${attrEsc(status)}">${escHtml(_statusWord(status))}</span>
            ${badge}
            <span class="lane-count">${lane.total} event${lane.total === 1 ? '' : 's'}</span>
        </header>
        ${body}
    </section>`;
}

// Glyph + word, again: `waiting` is not a colour, it is a sentence.
function _statusWord(status) {
    return ({
        waiting: '◆ waiting on you',
        busy: '▪ busy',
        idle: '· idle',
        gone: '✕ gone',
        system: '■ chela',
    })[status] || status;
}

function _filtersHtml() {
    const chips = CLASS_IDS.map(c => {
        const on = _classes.includes(c);
        return `<button type="button" class="feed-chip${on ? ' active' : ''}"
                aria-pressed="${on ? 'true' : 'false'}"
                onclick="chela.feedToggleClass('${attrEsc(c)}')">
            ${escHtml(CLASSES[c].glyph)} ${escHtml(CLASSES[c].label)}</button>`;
    }).join('');
    return `<div class="feed-chips">${chips}</div>`;
}

function _renderFeed() {
    const host = $('#feed-list');
    if (!host) return;
    const filters = $('#feed-filters');
    if (filters) filters.innerHTML = _filtersHtml();
    document.querySelectorAll('#feed-seg .work-seg-btn').forEach(b => {
        const on = b.dataset.seg === _mode;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });

    // Re-rendering must not yank the ground from under someone mid-read: a new event
    // in another lane re-sorts the list, and an innerHTML swap resets the scroll.
    const canvas = $('#canvas');
    const scroll = canvas ? canvas.scrollTop : 0;

    const gap = _gap ? `<div class="feed-gap">⚠ ${escHtml(_gap.reason)}</div>` : '';

    if (!_events.length) {
        host.innerHTML = gap + `<div class="side-empty">No events yet — try
            <code>chela events emit --type note --summary "hello"</code>.</div>`;
        return;
    }

    if (_mode === 'flat') {
        const rows = flatRows(_events, _classes);
        const hiddenTotal = _events.length - rows.length;
        const counts = {};
        _events.forEach(e => {
            const c = classOf(e.type);
            if (!_classes.includes(c)) counts[c] = (counts[c] || 0) + 1;
        });
        host.innerHTML = gap + _hiddenHtml(counts, hiddenTotal)
            + (rows.length ? rows.map(e => _rowHtml(e, true)).join('')
                : '<div class="side-empty">Every row is filtered out — turn a chip back on.</div>');
    } else {
        const { lanes } = buildLanes(_events, _fleet, _classes);
        // Which runs still owe you a review — read off the payload work.js's app-level
        // poll already has (⛔ NOT a second fetch of /api/dispatcher; it is the one
        // poller). The Feed needs it because a dispatched agent KILLS ITS OWN WINDOW and
        // only then does the run reconcile to `awaiting_review`: the lane holding "go
        // review this PR" is `gone` in tmux while the PR is wide open (CMX-62), and the
        // log cannot tell us — it records the ask and nothing when it is answered. `null`
        // (no poll has landed yet) means UNKNOWN, and splitGone shows those lanes rather
        // than burying them.
        const group = splitGone(lanes, awaitingReviewIds());
        // The graveyard row sits where the gone lanes sat — after the fleet, before
        // `chela itself`. The sort is unchanged (needs-you → busy → idle → gone → chela);
        // this only folds the `gone` bucket, and the lanes it holds render right behind
        // the row when it is open, in exactly the order they had.
        const parts = group.lanes.map(_laneHtml);
        const goneRank = LANE_ORDER.indexOf('gone');
        let at = group.lanes.findIndex(l => laneRank(l) > goneRank);
        if (at < 0) at = parts.length;
        const fold = _goneHtml(group) + (_graveyard ? group.buried.map(_laneHtml).join('') : '');
        parts.splice(at, 0, fold);
        host.innerHTML = gap + parts.join('');
    }
    if (canvas) canvas.scrollTop = scroll;
}


// --- Stage 0: ES-module exports ---
export { enterFeed, onLogDelta, refreshFeed, tickFeed };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { feedSetMode, feedToggleClass, feedToggleGraveyard, feedToggleLane });
