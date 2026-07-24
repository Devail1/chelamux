// ---------------------------------------------------------------------------
// HOST RESOURCES STRIP — live CPU/RAM/Disk in the header (CMX-172). Sampled
// on-request by the dashboard process itself (chela/dashboard/resources.py,
// GET /api/resources) — never the daemon, never a shared state file: this is
// the same "the panel that shows it samples it" shape as the Cost tab, just
// with a header-strip instead of a table.
//
// Wiring (main.js, same pattern as Decisions): enterResources() seeds it once
// on page load; tickResources() rides the refresh() loop as a plain poll —
// there is no SSE delta for host resources, so unlike Decisions there is no
// push side to this.
//
// Each readout hides itself when its value comes back null (an unreadable
// host — no /proc, a permissions error) rather than show a stale or fake
// number; the % text is always the primary signal, colour (resourcesmodel.js
// level()) is decoration on top, never the sole cue (Liav is red-weak).
// ---------------------------------------------------------------------------
import { $, api } from './util.js';
import { humanBytes, level } from './resourcesmodel.js';

const METRICS = [
    { id: 'res-cpu', label: 'CPU', pctKey: 'cpu_pct' },
    { id: 'res-mem', label: 'RAM', pctKey: 'mem_pct', usedKey: 'mem_used', totalKey: 'mem_total' },
    { id: 'res-disk', label: 'Disk', pctKey: 'disk_pct', usedKey: 'disk_used', totalKey: 'disk_total' },
];

function _renderMetric(m, data) {
    const el = $('#' + m.id);
    if (!el) return;
    const value = data[m.pctKey];
    if (value == null) {
        el.hidden = true;
        return;
    }
    el.hidden = false;
    el.className = 'res-item res-' + level(value);
    const valueEl = el.querySelector('.res-value');
    if (valueEl) valueEl.textContent = `${Math.round(value)}%`;
    const used = m.usedKey ? data[m.usedKey] : null;
    const total = m.totalKey ? data[m.totalKey] : null;
    el.title = used != null && total != null
        ? `${m.label}: ${humanBytes(used)} / ${humanBytes(total)}`
        : `${m.label}: ${Math.round(value)}%`;
}

function _render(data) {
    const strip = $('#resources-strip');
    if (!strip || !data) return;
    strip.hidden = false;
    METRICS.forEach(m => _renderMetric(m, data));
}

// Never throws out: a transient failure keeps the last render (the next tick
// retries), same contract as pollWork (work.js) / the other pollers.
async function tickResources() {
    let data;
    try {
        data = await api('/api/resources');
    } catch (e) {
        console.error('tickResources', e);
        return;
    }
    _render(data);
}

// The one-time page-load seed (main.js) — identical to tickResources today;
// kept as its own entry point so it mirrors enterDecisions()'s call shape and
// can grow independent seeding logic later without touching main.js again.
async function enterResources() {
    await tickResources();
}

// --- Stage 0: ES-module exports ---
export { enterResources, tickResources };
