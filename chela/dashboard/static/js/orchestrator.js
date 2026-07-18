// ---------------------------------------------------------------------------
// ORCHESTRATOR SUBSCRIBE — the pane-title toggle for "receive the decisions
// inbox here", plus the shared state a decisions-panel owner chip renders from.
//
// Wraps /api/orchestrator/{subscribe,release,status} (chela/dashboard/app.py),
// which themselves wrap chela.inbox.register/unregister — an ATOMIC take-over
// of the single `orchestrator` slot (chela/inbox.py). Clicking a SECOND pane's
// toggle supersedes the first; the first pane's next status read simply reports
// it is no longer the owner. There is never a moment with two live recipients.
//
// State lives HERE, not per-caller: terminals.js (the pane toggle) and
// decisions.js (the owner chip) both read the SAME `_status` and both react to
// the SAME SSE `orchestrator` delta (sse.js) — one fact, rendered twice, never
// two copies that can disagree about who currently owns the role.
// ---------------------------------------------------------------------------
import { api } from './util.js';

let _status = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };
const _listeners = new Set();

function orchestratorState() {
    return _status;
}

// Subscribe to every change of `_status` — terminals.js redraws pane buttons,
// decisions.js redraws the owner chip. A listener that throws must not stop
// the others from hearing the update.
function onOrchestratorChange(fn) {
    _listeners.add(fn);
    return () => _listeners.delete(fn);
}

function _apply(data) {
    if (data && typeof data === 'object' && !Array.isArray(data)) _status = data;
    _listeners.forEach(fn => {
        try { fn(_status); } catch (e) { console.error('orchestrator listener', e); }
    });
    return _status;
}

async function refreshOrchestratorStatus() {
    let data;
    try {
        data = await api('/api/orchestrator/status');
    } catch (e) {
        return _status;               // transient — the next poll/SSE delta retries
    }
    return _apply(data);
}

async function orchestratorSubscribe(wid) {
    let data;
    try {
        data = await api('/api/orchestrator/subscribe', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wid }),
        });
    } catch (e) {
        return { ok: false, error: String(e) };
    }
    if (data && data.ok) _apply(data);
    return data || { ok: false, error: 'empty response' };
}

async function orchestratorRelease(wid) {
    let data;
    try {
        data = await api('/api/orchestrator/release', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wid }),
        });
    } catch (e) {
        return { ok: false, error: String(e) };
    }
    if (data && data.ok) _apply(data);
    return data || { ok: false, error: 'empty response' };
}

// --- Stage 0: ES-module exports ---
export { onOrchestratorChange, orchestratorRelease, orchestratorState, orchestratorSubscribe, refreshOrchestratorStatus };
