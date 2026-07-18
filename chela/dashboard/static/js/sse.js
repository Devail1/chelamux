// --- Stage 0: ES-module imports ---
import { $, BASE_PATH, TERMINALS_ON, _agentsCache, api, attrEsc, currentTab, escHtml, setAgentsCache } from './util.js';
import { refreshAgents } from './agents.js';
import { pollWork } from './work.js';
import { onLogDelta } from './feed.js';
import { onDecisionsLogDelta } from './decisions.js';
import { refreshOrchestratorStatus } from './orchestrator.js';
import { RUN_TOAST_KINDS, runToastKind } from './runtoast.js';
import { _absorbFreshTerminals, _cssEsc, _refreshPaneLabels, _renderedWids, _stopReadyPoll, _swapToFrame, _termReady, dropTerminalPane } from './terminals.js';

// ---------------------------------------------------------------------------
// Reactive updates via Server-Sent Events (accelerator over the poll timers)
//
// ONE EventSource opened on load — there is deliberately no second one. Each delta
// event re-runs the EXISTING render path for the affected view: refreshAgents and
// pollWork refetch the full shape and redraw, dropTerminalPane / renderTerminals
// handle the wall reactively, and the `log` frame tells the Feed its cursor has
// something behind it. Every frame is a NOTIFICATION — it carries no data the UI
// renders directly, so a dropped frame is never a lost fact.
//
// This is strictly additive. The 30s global refresh, the 4s termTick and work.js's
// 30s /api/dispatcher poll all keep running. If the stream never connects or drops,
// the browser auto-reconnects and the timers cover the gap, so the UI behaves
// exactly as it did before SSE existed — the Feed included.
// ---------------------------------------------------------------------------

let _sse = null;

function _sseWindows(d) {
    // Window set changed (agent window appeared / vanished). Invalidate the
    // agent cache so any render refetches the fresh shape.
    setAgentsCache([]);
    if (currentTab === 'agents') {
        refreshAgents();
    } else if (TERMINALS_ON && currentTab === 'terminals') {
        // Reconcile by stable window id, not the payload names: refetch the live
        // agent set, drop tiles for vanished wids, and absorb new ones surgically
        // (wall appends a tile, single refreshes the dropdown — neither reloads a
        // live iframe). Diffing by wid means a pure rename is a no-op here. The
        // SSE `windows` event still fires on spawn/kill elsewhere even with the
        // terminals tab inactive — the guard above skips it then.
        api('/api/agents').then(agents => {
            setAgentsCache(agents);
            const live = (agents || []).filter(a => a.online !== false && a.window_id)
                .map(a => a.window_id);
            const liveSet = new Set(live);
            _renderedWids.filter(w => !liveSet.has(w)).forEach(dropTerminalPane);
            _absorbFreshTerminals(live);
            _refreshPaneLabels();   // a rename may have changed only the label
        }).catch(() => { /* transient — the 4s termTick will catch up */ });
    }
}

function _sseRuns(d) {
    // A dispatcher run's status / PR state changed. First fire any run-state
    // toasts (works regardless of the active view), then redraw. One call now:
    // pollWork() refetches /api/dispatcher ONCE and feeds the board, the runs
    // tables and the sidebar badges from that one payload.
    _runStateToasts(d);
    pollWork();
}

function _sseLog(d) {
    // The event log's seq moved. The frame is a NOTIFICATION — it carries the new
    // seq, not the events — so the reader fetches /api/log from its OWN cursor. Only
    // while its view is on screen: off it, the view's entry does a fresh read, so a
    // background fetch per appended event would buy nothing. Two readers share this
    // one delta — the Feed (everything) and the decisions panel (a filtered tail).
    if (currentTab === 'feed') onLogDelta(d);
    if (currentTab === 'personas') onDecisionsLogDelta(d);
}

function _sseOrchestrator() {
    // Who owns the pane-title toggle changed (a subscribe/release/self-heal took the
    // slot, or it went to nobody). A NOTIFICATION like every other frame here — it
    // carries only the new wid — so refetch /api/orchestrator/status for the full
    // state (state/why/queued). Refreshed regardless of tab: a takeover on Personas
    // must repaint the Wall's pane buttons too, and vice versa.
    refreshOrchestratorStatus();
}

// Per-run last-seen status, so the toast is EDGE-TRIGGERED (fires only on the
// transition, not on every ~1s poll that carries an unrelated pr_state change).
// Primed from the SSE `hello` baseline so a run already in awaiting_review when
// the page loads / reconnects is recorded — not re-toasted.
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

function _sseTermReady(d) {
    // Push-driven accelerator: a terminal just got its ttyd port. Swap any
    // matching placeholder for its iframe immediately. The ~1.5s poll remains
    // the reliable default, so this is purely additive — if it never fires, the
    // poller still resolves the swap.
    if (!TERMINALS_ON || currentTab !== 'terminals') return;
    const stage = $('#term-stage');
    // d.ready carries the port-map keys, which are now window ids (@N) — the
    // same value as each placeholder's data-pending, so the match holds.
    (d && d.ready || []).forEach(wid => {
        if (stage.querySelector('.term-pending[data-pending="' + _cssEsc(wid) + '"]')) {
            _stopReadyPoll(wid);
            _termReady.add(wid);
            _swapToFrame(wid);
        }
    });
}

function initSSE() {
    if (!window.EventSource) return;   // older browser → polling-only, still fine
    try {
        _sse = new EventSource(BASE_PATH + '/api/events');
    } catch (e) {
        console.warn('SSE unavailable, polling-only:', e);
        return;
    }
    _sse.addEventListener('hello', e => {
        let d = null;
        try { d = JSON.parse(e.data); } catch (_) { /* baseline optional */ }
        _primeRunStatus(d);
    });
    _sse.addEventListener('windows', e => {
        let d = null;
        try { d = JSON.parse(e.data); } catch (_) { /* trigger-only */ }
        _sseWindows(d);
    });
    _sse.addEventListener('runs', e => {
        let d = null;
        try { d = JSON.parse(e.data); } catch (_) { /* trigger-only */ }
        _sseRuns(d);
    });
    _sse.addEventListener('log', e => {
        let d = null;
        try { d = JSON.parse(e.data); } catch (_) { /* trigger-only */ }
        _sseLog(d);
    });
    _sse.addEventListener('term-ready', e => {
        let d = null;
        try { d = JSON.parse(e.data); } catch (_) { /* trigger-only */ }
        _sseTermReady(d);
    });
    _sse.addEventListener('orchestrator', () => _sseOrchestrator());
    // onerror: the browser reconnects on its own; the poll timers cover the gap
    // meanwhile, so there is nothing to do but note it.
    _sse.onerror = () => { /* auto-reconnect; polling is the fallback */ };
}


// --- Stage 0: ES-module exports ---
export { initSSE };
