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
//                    └──> work.set(data)     (the Runs island and the New-task form)
//
// The timer is APP-level, not view-level: the badges are visible from every view,
// so the poll has to run everywhere — which is exactly why it must be one poll
// and not three. Schedules keeps its own endpoint (/api/schedules — a different
// dataset), fetched only while its segment is showing.
// ---------------------------------------------------------------------------
import { $, $$, api, attrEsc, currentTab, escHtml } from './util';
import { onEvent } from '../../frontend/src/shared/events';
import { RUN_TOAST_KINDS, runToastKind } from './runtoast';
import { _launcherData } from './launcher';
import { renderKanban } from './kanban';
import { openNewTask } from '../../frontend/src/features/newtask';
import { openInitRepo } from '../../frontend/src/features/runs';
import { refreshSchedules } from '../../frontend/src/features/schedules';
import { setWorkPoll, work } from '../../frontend/src/shared/store/work';
import { setNavBadges } from '../../frontend/src/features/sidebar/controller';

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
    // The nav rows are the sidebar island's (CMX-278): it draws the badges by id.
    setNavBadges({ 'side-runs-count': String(runs), 'side-pr-count': prs + ' PR' });
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

// CMX-6: "+ New task" shows only when some workflow's tracker is linear — the only
// tracker chela creates issues in. Hidden until the first poll, like the hold button.
function _renderNewTaskButton(data) {
    const btn = document.getElementById('new-task-btn');
    if (!btn) return;
    const linear = ((data && data.workflows) || []).some(w => w && w.tracker_kind === 'linear');
    btn.style.display = linear ? '' : 'none';
}

// Pause takes the hold (30m default TTL, same as the CLI's --pause with no --ttl);
// resume releases it unconditionally. Reads current state off `_lastData` — the same
// payload the button was just rendered from — rather than a fresh fetch, so there's no
// race between "what the button shows" and "what it acts on".
async function toggleDispatchHold() {
    const btn = document.getElementById('dispatch-hold-btn');
    const held = _lastData && _lastData.dispatch_hold;
    if (!held && !confirm('Pause dispatch? No new task will be claimed until you resume.')) {
        return;
    }
    if (btn) btn.disabled = true;
    try {
        const path = held ? '/api/dispatcher/resume' : '/api/dispatcher/pause';
        const resp = await api(path, { method: 'POST' });
        if (!resp || resp.ok === false) {
            alert((resp && resp.error) || 'That failed. Dispatch state is unchanged.');
        }
    } catch (e) {
        alert('Request failed. Dispatch state is unchanged.');
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

// The Runs segment is a React island reading the `work` store (pollWork publishes every
// payload there), so only the board is drawn from here.
function _renderWorkPanes(data) {
    if (_segment === 'board') renderKanban(data);
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
    work.set(data);                     // the Runs tables and the New-task form read it
    _readAwaiting(data);                // the Feed reads this — see awaitingReviewIds()
    _renderWorkBadges(data);            // sidebar — visible from every view
    _renderDispatchHold(data);          // Pause/Resume button — lives in the Work toolbar,
                                         // always in the DOM regardless of the active tab
    _renderNewTaskButton(data);
    if (currentTab !== 'work') return;  // nothing else on screen to draw
    _applySegment();
    _renderWorkPanes(data);
    if (_segment === 'schedules') refreshSchedules();
}

// Started once at boot (frontend/src/boot.ts). The badges live in the always-visible sidebar,
// so this cannot be a per-view timer — and being app-level is precisely what lets
// the board, the runs table and the badges share it instead of racing each other.
function startWorkPoll() {
    if (_workTimer) return;
    pollWork();                          // fill the badges now, not in 30s
    _workTimer = setInterval(pollWork, WORK_REFRESH_MS);
}

// --- Run-state toasts, off the SSE `runs` frame -----------------------------
// A dispatcher run's status / PR state changed: fire any run-state toasts (whatever the
// view), then redraw. pollWork() refetches /api/dispatcher ONCE and feeds the board, the
// runs tables and the sidebar badges from that one payload.
//
// Per-run last-seen status, so the toast is EDGE-TRIGGERED (fires only on the transition,
// not on every frame that carries an unrelated pr_state change). Primed from the SSE
// `hello` baseline so a run already in awaiting_review when the page loads / reconnects is
// recorded, not re-toasted.
const _prevRunStatus = new Map();

function _primeRunStatus(d) {
    (d && d.runs || []).forEach(r => {
        if (r && r.task_id) _prevRunStatus.set(r.task_id, r.status);
    });
}

function _runToastsMuted() {
    // Hung off the Notifications settings entry; persisted like other prefs.
    try { return localStorage.getItem('chela_mute_run_toasts') === '1'; }
    catch (e) { return false; }
}

function _runStateToasts(d) {
    (d && d.runs || []).forEach(r => {
        if (!r || !r.task_id) return;
        const prev = _prevRunStatus.get(r.task_id);
        _prevRunStatus.set(r.task_id, r.status);
        const kind = runToastKind(prev, r.status);
        if (!kind || _runToastsMuted()) return;
        _runReviewToast(r, kind);
    });
}

function _runReviewToast(r, kind) {
    // Floating, click-to-dismiss toast reusing the .kanban-toast surface, stacked
    // bottom-right via #run-toast-stack so several transitions don't overlap. The
    // PR link (when present) lets the viewer jump straight to review.
    const meta = RUN_TOAST_KINDS[kind];
    let stack = $('#run-toast-stack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'run-toast-stack';
        stack.className = 'run-toast-stack';
        document.body.appendChild(stack);
    }
    const label = r.label || r.task_id || 'run';
    const toast = document.createElement('div');
    toast.className = 'run-toast';
    let html = `${meta.icon} <strong>${escHtml(label)}</strong> → ${escHtml(meta.text)}`;
    if (r.pr_url) {
        // Link click dismisses on its own navigation; stop it re-triggering the
        // toast's click-to-dismiss so the href actually opens.
        html += ` · <a href="${attrEsc(r.pr_url)}" target="_blank" rel="noopener"
                       onclick="event.stopPropagation()">PR</a>`;
    }
    toast.innerHTML = html;
    toast.onclick = () => toast.remove();
    stack.appendChild(toast);
    setTimeout(() => { toast.remove(); }, 15000);
}

setWorkPoll(pollWork);
onEvent('hello', d => _primeRunStatus(d));
onEvent('runs', d => { _runStateToasts(d); pollWork(); });

// --- Stage 0: ES-module exports ---
export { pollWork, startWorkPoll, toggleDispatchHold, workBadgeCounts };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
// The toolbar's "+ New task" and "+ Init a repo" open React dialogs (CMX-231).
Object.assign(window.chela, {
    openInitRepo: () => openInitRepo(_launcherData), openNewTask, setWorkSegment, toggleDispatchHold,
});
