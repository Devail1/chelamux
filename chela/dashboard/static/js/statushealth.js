// ---------------------------------------------------------------------------
// NATIVE STATUS FEED HEALTH MARKER (CMX-179) — the topbar #status-health-warn pill.
//
// `claude agents --json` (agent_manager.py) is the ONE authority for every pane's
// busy/idle status. It died silently for 12 days: the timeout was below its real
// warm-start cost, so every call failed, the cache stayed empty, and every pane
// just kept rendering "idle" — indistinguishable on screen from a genuinely calm
// fleet. This module is the fix for THAT half: poll /api/agents/status_health and
// show a marker the moment the feed itself is not answering, so an empty status
// map and a calm fleet are never confused again.
//
// Wiring (main.js, same pattern as resources.js): enterStatusHealth() seeds it once
// on page load; tickStatusHealth() rides the refresh() loop as a plain poll — the
// feed's own health is not something an SSE delta can push (nothing is listening
// while it's down).
// ---------------------------------------------------------------------------
import { $, api } from './util.js';

function _render(health) {
    const el = $('#status-health-warn');
    if (!el) return;
    // Fail closed on a missing/malformed response: show the warning rather than
    // silently hide it, since "no answer at all" is itself evidence something is
    // wrong upstream.
    el.hidden = !!(health && health.ok);
}

// Never throws out: a transient failure to reach the dashboard's OWN API leaves the
// last render in place (the next tick retries), same contract as tickResources.
async function tickStatusHealth() {
    let health;
    try {
        health = await api('/api/agents/status_health');
    } catch (e) {
        console.error('tickStatusHealth', e);
        return;
    }
    _render(health);
}

async function enterStatusHealth() {
    await tickStatusHealth();
}

// --- Stage 0: ES-module exports ---
export { enterStatusHealth, tickStatusHealth };
