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
    CLASSES, CLASS_IDS, DEFAULT_CLASSES,
    buildLanes, classOf, flatRows, hiddenSummary,
} from './feedmodel.js';

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

// Fetch from our cursor and render. `reset` starts over (entering the view).
async function refreshFeed(reset = false) {
    if (reset) { _cursor = null; _boot = null; _events = []; _gap = null; }
    if (_inflight) return;
    _inflight = true;
    let fresh = [];
    try {
        for (let i = 0; i < CATCHUP_FETCHES; i++) {
            const qs = new URLSearchParams({ limit: String(FEED_LIMIT) });
            if (_cursor != null) qs.set('after_seq', String(_cursor));
            if (_boot) qs.set('after_boot', _boot);
            let batch;
            try {
                batch = await api('/api/log?' + qs.toString());
            } catch (e) {
                break;                       // transient — the next tick retries
            }
            if (!batch || !Array.isArray(batch.events)) break;
            if (batch.gap) {
                // Told, not guessed. The server has already re-anchored the read for
                // us; we keep the notice on screen so a hole in the record is never
                // invisible — a plausible-looking wrong continuation is worse.
                _gap = batch.gap;
                _events = [];
            }
            _events = _events.concat(batch.events);
            fresh = fresh.concat(batch.events);
            // Resume from next_seq — NOT last_seq. With a limit in play they differ,
            // and last_seq would silently skip every event past the truncation point.
            _cursor = batch.next_seq;
            _boot = batch.boot_id;
            // /api/log hands back the OLDEST events after the cursor, so one call does
            // not reach the tail of a busy log. Keep pulling until it does (bounded).
            if (!batch.events.length || batch.next_seq >= batch.last_seq) break;
        }
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

function _laneHtml(lane) {
    const collapsed = _collapsed.has(lane.wid);
    const rows = lane.events.slice(-LANE_ROWS);          // chronological, newest at the foot
    const earlier = lane.events.length - rows.length;
    const badge = lane.needsYou
        ? '<span class="lane-needs">◆ NEEDS YOU</span>' : '';
    const status = lane.system ? 'system' : lane.status;
    const project = lane.project ? `<span class="lane-project">${escHtml(lane.project)}</span>` : '';
    const wid = lane.system ? '' : `<span class="lane-wid">${escHtml(lane.wid)}</span>`;
    const body = collapsed ? '' : `<div class="lane-rows">
            ${earlier > 0 ? `<div class="feed-hidden">⋯ ${earlier} earlier rows not drawn</div>` : ''}
            ${rows.map(e => _rowHtml(e, false)).join('')}
            ${_hiddenHtml(lane.hidden, lane.hiddenTotal)}
            ${!rows.length && !lane.hiddenTotal ? '<div class="side-empty">Nothing said yet.</div>' : ''}
        </div>`;
    return `<section class="feed-lane${lane.needsYou ? ' lane-attention' : ''}${lane.system ? ' lane-system' : ''}"
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
        host.innerHTML = gap + lanes.map(_laneHtml).join('');
    }
    if (canvas) canvas.scrollTop = scroll;
}


// --- Stage 0: ES-module exports ---
export { enterFeed, onLogDelta, refreshFeed, tickFeed };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { feedSetMode, feedToggleClass, feedToggleLane });
