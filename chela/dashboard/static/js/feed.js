// ---------------------------------------------------------------------------
// FEED — the event log, in the browser. PART 1: the plumbing, not the timeline.
//
// ⚠️ The LAYOUT is deliberately not designed here (that is part 2, from real
// data). What this module owns is the contract underneath it, and that contract
// is the whole point of the slice:
//
//   * ONE reader — GET /api/log, a thin wrapper over event_log.read(), the same
//     call `chela events` makes. No second event source.
//   * A CURSOR — we resume from `next_seq` (never `last_seq`: a bounded read
//     truncates, and last_seq would skip everything past the truncation), pinned
//     to the `boot_id` it came from.
//   * A GAP is rendered, not papered over. If the log rotated past our cursor, or
//     the daemon restarted, the server says so and we SAY so — a plausible-looking
//     wrong continuation is worse than a visible hole.
//   * It rides the EXISTING EventSource: sse.js pushes a `log` delta carrying the
//     new seq, we fetch. If the stream never connects, the view's 30s tick covers
//     it — the same degrade-to-polling safety net as every other view.
// ---------------------------------------------------------------------------
import { api, escHtml } from './util.js';

const FEED_LIMIT = 200;      // per fetch; a busy fleet catches up over a few ticks
const FEED_MAX = 500;        // events kept in the browser

let _cursor = null;          // last seq we have seen (null = "give me the tail")
let _boot = null;            // the boot_id that seq belongs to
let _events = [];            // oldest → newest
let _gap = null;             // non-null once the server has told us we missed something
let _inflight = false;       // one fetch at a time — the SSE delta can outpace the network

// Fetch from our cursor and render. `reset` starts over (entering the view).
async function refreshFeed(reset = false) {
    if (reset) { _cursor = null; _boot = null; _events = []; _gap = null; }
    if (_inflight) return;
    _inflight = true;
    let batch;
    try {
        const qs = new URLSearchParams({ limit: String(FEED_LIMIT) });
        if (_cursor != null) qs.set('after_seq', String(_cursor));
        if (_boot) qs.set('after_boot', _boot);
        batch = await api('/api/log?' + qs.toString());
    } catch (e) {
        return;                              // transient — the next tick retries
    } finally {
        _inflight = false;
    }
    if (!batch || !Array.isArray(batch.events)) return;

    if (batch.gap) {
        // Told, not guessed. The server has already re-anchored the read for us; we
        // keep the notice on screen so a hole in the record is never invisible.
        _gap = batch.gap;
        _events = [];
    }
    _events = _events.concat(batch.events).slice(-FEED_MAX);
    // Resume from next_seq — NOT last_seq. With a limit in play they differ, and
    // last_seq would silently skip every event past the truncation point.
    _cursor = batch.next_seq;
    _boot = batch.boot_id;
    _renderFeed();
}

// The SSE `log` delta: the log's seq moved. The frame carries no events — it is a
// notification — so we fetch from OUR cursor and a dropped frame costs nothing.
function onLogDelta(d) {
    if (d && d.boot_id && _boot && d.boot_id !== _boot) {
        // A fresh boot epoch (daemon restart, or `chela events rotate`): our cursor
        // belongs to a different numbering. Ask from scratch and let /api/log tell
        // us about the gap.
        refreshFeed(true);
        return;
    }
    refreshFeed();
}

function _feedRowHtml(e) {
    const ts = new Date((e.ts || 0) * 1000)
        .toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    return `<div class="feed-row">
        <span class="feed-seq">${escHtml(String(e.seq))}</span>
        <span class="feed-ts">${escHtml(ts)}</span>
        <span class="feed-type">${escHtml(e.type || '?')}</span>
        <span class="feed-wid">${escHtml(e.wid || '')}</span>
        <span class="feed-summary">${escHtml(e.summary || '')}</span>
    </div>`;
}

function _renderFeed() {
    const host = document.getElementById('feed-list');
    if (!host) return;
    const gap = _gap
        ? `<div class="feed-gap">⚠ ${escHtml(_gap.reason)}</div>`
        : '';
    if (!_events.length) {
        host.innerHTML = gap + `<div class="side-empty">No events yet — try
            <code>chela events emit --type note --summary "hello"</code>.</div>`;
        return;
    }
    // Newest first. A deliberately plain list: the timeline is part 2.
    host.innerHTML = gap + _events.slice().reverse().map(_feedRowHtml).join('');
}


// --- Stage 0: ES-module exports ---
export { onLogDelta, refreshFeed };
