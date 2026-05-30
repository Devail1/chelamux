// ---------------------------------------------------------------------------
// Reactive updates via Server-Sent Events (accelerator over the poll timers)
//
// One EventSource opened on load. Each delta event re-runs the EXISTING render
// path for the affected, currently-active tab — refreshAgents / refreshKanban /
// refreshDispatcher refetch the full shape and redraw, and dropTerminalPane /
// renderTerminals handle the wall reactively — so no new DOM path is added.
//
// This is strictly additive. The 30s global refresh, the 4s termTick, and the
// dispatcher / kanban 30s timers all keep running. If the stream never connects
// or drops, the browser auto-reconnects and the timers cover the gap, so the UI
// behaves exactly as it did before SSE existed.
// ---------------------------------------------------------------------------

let _sse = null;

function _sseWindows(d) {
    // Window set changed (agent window appeared / vanished). Invalidate the
    // agent cache so any render refetches the fresh shape.
    _agentsCache = [];
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
            _agentsCache = agents;
            const live = (agents || []).filter(a => a.online !== false && a.window_id)
                .map(a => a.window_id);
            const liveSet = new Set(live);
            _renderedWids.filter(w => !liveSet.has(w)).forEach(dropTerminalPane);
            _absorbFreshTerminals(live);
            _refreshPaneLabels();   // a rename may have changed only the label
        }).catch(() => { /* transient — the 4s termTick will catch up */ });
    }
}

function _sseRuns() {
    // A dispatcher run's status / PR state changed — redraw whichever board is
    // showing it. Both refetch /api/dispatcher and fully re-render.
    if (currentTab === 'dispatcher') refreshDispatcher();
    else if (currentTab === 'kanban') refreshKanban();
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
    _sse.addEventListener('windows', e => {
        let d = null;
        try { d = JSON.parse(e.data); } catch (_) { /* trigger-only */ }
        _sseWindows(d);
    });
    _sse.addEventListener('runs', () => _sseRuns());
    _sse.addEventListener('term-ready', e => {
        let d = null;
        try { d = JSON.parse(e.data); } catch (_) { /* trigger-only */ }
        _sseTermReady(d);
    });
    // onerror: the browser reconnects on its own; the poll timers cover the gap
    // meanwhile, so there is nothing to do but note it.
    _sse.onerror = () => { /* auto-reconnect; polling is the fallback */ };
}

