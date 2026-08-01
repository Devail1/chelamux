// ---------------------------------------------------------------------------
// WORK — Dispatch + Kanban + Schedules, consolidated into ONE view.
//
// They were never three views over three datasets. dispatcher.js and kanban.js
// polled THE SAME /api/dispatcher endpoint and merely shaped it differently (a
// per-workflow card vs. seven kanban buckets), and nav.js's WORK badges were a
// THIRD independent poller of it — so on the Kanban view that one endpoint was
// being fetched by two timers at once, and by three on any tick that also
// refreshed the sidebar.
//
// This module owns the single poll. `pollWork()` fetches /api/dispatcher ONCE and
// hands the same payload to every renderer that needs it:
//
//     /api/dispatcher ──> workBadgeCounts()  (sidebar badges — always, every view)
//                    ├──> renderKanban(data) (the Board segment, when Work is up)
//                    └──> renderDispatcher(data) (the Runs segment, when Work is up)
//
// The timer is APP-level, not view-level: the badges are visible from every view,
// so the poll has to run everywhere — which is exactly why it must be one poll
// and not three. Schedules keeps its own endpoint (/api/schedules — a different
// dataset), fetched only while its segment is showing.
// ---------------------------------------------------------------------------
import { $$, BASE_PATH, api, currentTab } from './util.js';
import { renderDispatcher } from './dispatcher.js';
import { renderKanban } from './kanban.js';
import { refreshSchedules } from './schedules.js';

const WORK_REFRESH_MS = 30000;

// The segment is a rendering of the same work, not a different view: board (the
// kanban), runs (the per-workflow dispatch tables), schedules (the pokes). Saved
// per browser so a reload lands you back where you were.
const SEGMENTS = ['board', 'runs', 'schedules'];
const SEG_KEY = 'chela_work_segment';

function _loadSegment() {
    try {
        const v = localStorage.getItem(SEG_KEY);
        return SEGMENTS.includes(v) ? v : 'board';
    } catch (e) { return 'board'; }
}
let _segment = _loadSegment();
let _workTimer = null;
let _lastData = null;   // the last /api/dispatcher payload — a segment switch redraws from it

// Counts for the sidebar WORK badges, off the payload the board already fetched.
// Pure: the same numbers the Kanban's Running / Awaiting Review columns show.
function workBadgeCounts(data) {
    let runs = 0, prs = 0;
    for (const wf of ((data && data.workflows) || [])) {
        runs += (wf.active_runs || []).length;
        prs += (wf.awaiting_review_runs || []).length;
    }
    return { runs, prs };
}

// The runs still awaiting YOUR review, by TASK id. `null` until the first poll lands —
// "not known yet" is not "there are none", and the Feed's graveyard leans on that
// difference (an unknown review is shown, never buried).
//
// This is a READ of the payload the badges already fetched, not a second poll: the Feed
// needs run STATUS (a dead window can still owe you a PR — CMX-62) and the log cannot
// tell it, so it reads it here. The one-poller rule (this module) stands.
let _awaiting = null;

export function awaitingReviewIds() { return _awaiting; }

function _readAwaiting(data) {
    const ids = [];
    for (const wf of ((data && data.workflows) || [])) {
        for (const r of (wf.awaiting_review_runs || [])) {
            if (r && r.task_id) ids.push(r.task_id);
        }
    }
    _awaiting = ids;
}

function _renderWorkBadges(data) {
    const { runs, prs } = workBadgeCounts(data);
    const runsEl = document.getElementById('side-runs-count');
    const prEl = document.getElementById('side-pr-count');
    if (runsEl) runsEl.textContent = String(runs);
    if (prEl) prEl.textContent = prs + ' PR';
}

// CMX-206: `chela dispatch --pause` stops new claims — genuinely operational, but until
// now reachable only over SSH. This renders the button's state off `dispatch_hold` in
// the SAME /api/dispatcher payload the board already polls (no second endpoint), and
// stays hidden until the first poll lands so it never flashes "Pause" over a queue that
// is actually held.
function _renderDispatchHold(data) {
    const btn = document.getElementById('dispatch-hold-btn');
    const hint = document.getElementById('dispatch-hold-hint');
    if (!btn || !data) return;
    btn.style.display = '';
    const held = data.dispatch_hold;
    if (held) {
        btn.textContent = 'Resume dispatch';
        btn.classList.add('btn-warn');
        btn.classList.remove('btn-accent');
        if (hint) hint.textContent = held.summary || 'Dispatch is paused.';
    } else {
        btn.textContent = 'Pause dispatch';
        btn.classList.add('btn-accent');
        btn.classList.remove('btn-warn');
        if (hint) hint.textContent = '';
    }
}

// Pause takes the hold (30m default TTL, same as the CLI's --pause with no --ttl);
// resume releases it unconditionally. Reads current state off `_lastData` — the same
// payload the button was just rendered from — rather than a fresh fetch, so there's no
// race between "what the button shows" and "what it acts on".
async function toggleDispatchHold() {
    const btn = document.getElementById('dispatch-hold-btn');
    const held = _lastData && _lastData.dispatch_hold;
    if (!held && !confirm('Pause dispatch — no new task will be claimed until you resume?')) {
        return;
    }
    if (btn) btn.disabled = true;
    try {
        const path = held ? '/api/dispatcher/resume' : '/api/dispatcher/pause';
        const resp = await api(path, { method: 'POST' });
        if (!resp || resp.ok === false) {
            alert((resp && resp.error) || 'That failed — dispatch state is unchanged.');
        }
    } catch (e) {
        alert('Request failed — dispatch state is unchanged.');
    } finally {
        if (btn) btn.disabled = false;
    }
    await pollWork();
}

function _applySegment() {
    $$('#panel-work .work-pane').forEach(p => p.classList.toggle('active', p.dataset.seg === _segment));
    $$('#work-seg .work-seg-btn').forEach(b => {
        const on = b.dataset.seg === _segment;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}

function setWorkSegment(seg) {
    _segment = SEGMENTS.includes(seg) ? seg : 'board';
    try { localStorage.setItem(SEG_KEY, _segment); } catch (e) { /* ignore */ }
    _applySegment();
    // Board/runs redraw from the payload we already have — no second fetch for a
    // toggle between two renderings of the same data.
    if (_lastData) _renderWorkPanes(_lastData);
    if (_segment === 'schedules') refreshSchedules();
}

function _renderWorkPanes(data) {
    if (_segment === 'board') renderKanban(data);
    else if (_segment === 'runs') renderDispatcher(data);
}

// THE fetch. One call, every consumer. Never throws out: a transient failure keeps
// the last render (the next tick retries), same contract as the other pollers.
async function pollWork() {
    let data;
    try {
        data = await api('/api/dispatcher');
    } catch (e) {
        console.error('pollWork', e);
        return;
    }
    _lastData = data;
    _readAwaiting(data);                // the Feed reads this — see awaitingReviewIds()
    _renderWorkBadges(data);            // sidebar — visible from every view
    _renderDispatchHold(data);          // Pause/Resume button — lives in the Work toolbar,
                                         // always in the DOM regardless of the active tab
    if (currentTab !== 'work') return;  // nothing else on screen to draw
    _applySegment();
    _renderWorkPanes(data);
    if (_segment === 'schedules') refreshSchedules();
}

// Started once at boot (main.js). The badges live in the always-visible sidebar,
// so this cannot be a per-view timer — and being app-level is precisely what lets
// the board, the runs table and the badges share it instead of racing each other.
function startWorkPoll() {
    if (_workTimer) return;
    pollWork();                          // fill the badges now, not in 30s
    _workTimer = setInterval(pollWork, WORK_REFRESH_MS);
}

// The delete action was duplicated in dispatcher.js and kanban.js (same endpoint,
// same payload, two copies). One POST, both callers; the confirm UI stays where it
// belongs (a table row vs. a card). Resolves to an error string, or null on success.
async function postWorkDelete(payload) {
    let resp, data = {};
    try {
        resp = await fetch(BASE_PATH + '/api/dispatcher/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        try { data = await resp.json(); } catch (_) { data = {}; }
    } catch (e) {
        return String(e);
    }
    if (!resp.ok || !data.ok) return data.error || `HTTP ${resp.status}`;
    // Idempotent on the server: "already gone" and "just deleted" are the same
    // answer, so let the redraw be the confirmation.
    pollWork();
    return null;
}


// --- Stage 0: ES-module exports ---
export { pollWork, postWorkDelete, startWorkPoll, toggleDispatchHold, workBadgeCounts };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { setWorkSegment, toggleDispatchHold });
