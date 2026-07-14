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

function _renderWorkBadges(data) {
    const { runs, prs } = workBadgeCounts(data);
    const runsEl = document.getElementById('side-runs-count');
    const prEl = document.getElementById('side-pr-count');
    if (runsEl) runsEl.textContent = String(runs);
    if (prEl) prEl.textContent = prs + ' PR';
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
    _renderWorkBadges(data);            // sidebar — visible from every view
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
export { pollWork, postWorkDelete, startWorkPoll, workBadgeCounts };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { setWorkSegment });
