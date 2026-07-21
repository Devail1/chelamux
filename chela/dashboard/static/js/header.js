// --- Stage 0: ES-module imports ---
import { $, api, relativeTime } from './util.js';

// ---------------------------------------------------------------------------
// Render: Header summary
// ---------------------------------------------------------------------------

async function refreshSummary() {
    const d = await api('/api/summary');
    const agentsEl = $('#hdr-agents');
    if (d.agents_total > 0) {
        agentsEl.textContent = d.agents_online + '/' + d.agents_total;
        agentsEl.className = 'value ' + (d.agents_online === d.agents_total ? 'green' : (d.agents_online > 0 ? 'yellow' : 'red'));
    } else {
        agentsEl.textContent = d.windows_total + ' windows';
        agentsEl.className = 'value';
    }

    const schedEl = $('#hdr-schedules');
    if (schedEl) schedEl.textContent = d.schedules_active + '/' + d.schedules_total;

    const nextRuns = d.next_runs || {};
    const soonest = Object.entries(nextRuns).sort((a, b) => a[1].localeCompare(b[1]))[0];
    if (soonest) {
        $('#hdr-next').textContent = soonest[0] + ' in ' + relativeTime(soonest[1]);
    } else {
        $('#hdr-next').textContent = 'none';
    }

    $('#hdr-updated').textContent = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}


// --- Stage 0: ES-module exports ---
export { refreshSummary };
