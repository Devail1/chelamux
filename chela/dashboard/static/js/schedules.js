// --- Stage 0: ES-module imports ---
import { $, _agentsCache, api, attrEsc, closeModal, escHtml, humanSchedule, relativeTime, shortTime, showModal } from './util.js';

// ---------------------------------------------------------------------------
// Render: Schedules
// ---------------------------------------------------------------------------

async function refreshSchedules() {
    renderCron();   // read-only system-cron section, runs regardless of chela schedule count
    const tasks = await api('/api/schedules');
    const tbody = $('#sched-tbody');
    const empty = $('#sched-empty');
    if (tasks.length === 0) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';
    tbody.innerHTML = tasks.map(t => `
        <tr>
            <td>${t.id}</td>
            <td>${escHtml(t.agent_name)}</td>
            <td><b>${escHtml(humanSchedule(t.schedule_type, t.schedule_value))}</b></td>
            <td title="${escHtml(t.prompt)}">${escHtml(t.prompt.length > 60 ? t.prompt.slice(0, 60) + '...' : t.prompt)}</td>
            <td class="ts">${shortTime(t.last_run)}</td>
            <td class="ts">${t.next_run ? relativeTime(t.next_run) : '-'}</td>
            <td><span class="badge ${t.enabled ? 'badge-on' : 'badge-off'}">${t.enabled ? 'ON' : 'OFF'}</span></td>
            <td>
                <button onclick="chela.toggleSchedule(${t.id}, ${!t.enabled})">${t.enabled ? 'Disable' : 'Enable'}</button>
                <button class="btn-danger" onclick="chela.deleteSchedule(${t.id})">Del</button>
            </td>
        </tr>
    `).join('');
}

// Read-only system-cron table (parsed from `crontab -l` by /api/cron). No edit
// controls — the dashboard never touches the user's crontab.
async function renderCron() {
    const tbody = $('#cron-tbody');
    const empty = $('#cron-empty');
    if (!tbody) return;
    let data;
    try { data = await api('/api/cron'); } catch (e) { return; }
    const jobs = (data && data.jobs) || [];
    if (!jobs.length) {
        tbody.innerHTML = '';
        if (empty) {
            empty.textContent = (data && data.error) ? data.error : 'No cron jobs';
            empty.style.display = 'block';
        }
        return;
    }
    if (empty) empty.style.display = 'none';
    tbody.innerHTML = jobs.map(j => {
        const tz = (j.tz && j.tz !== 'local') ? ` <span class="badge">${escHtml(j.tz)}</span>` : '';
        const cmd = j.command.length > 70 ? j.command.slice(0, 70) + '…' : j.command;
        return `
        <tr>
            <td><b>${escHtml(j.schedule)}</b>${tz}</td>
            <td>${j.project ? escHtml(j.project) : '<span style="color:var(--text-dim)">—</span>'}</td>
            <td title="${attrEsc(j.command)}"><code>${escHtml(cmd)}</code></td>
            <td class="ts">${j.next_run ? relativeTime(j.next_run) : '-'}</td>
        </tr>`;
    }).join('');
}

function showAddSchedule() {
    // Populate agent dropdown dynamically from cached agents
    const sel = $('#sched-agent');
    sel.innerHTML = [..._agentsCache]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map(a => `<option value="${escHtml(a.name)}">${escHtml(a.name)}</option>`)
        .join('');
    onSchedTypeChange();   // sync the value label/placeholder to the current type
    showModal('modal-sched');
}

// Swap the value label + placeholder to match the selected schedule type so the
// user knows what format #sched-value expects (see chela/models.py schedule_value).
function onSchedTypeChange() {
    const type = $('#sched-type').value;
    const hints = {
        interval: ['Interval (e.g. 30s, 5m, 1h)', '5m'],
        cron: ['Cron expression (5 fields: min hr dom mon dow)', '0 */8 * * *'],
        once: ['Run at (ISO 8601, e.g. 2026-01-15T09:00)', 'YYYY-MM-DDTHH:MM'],
    };
    const [text, placeholder] = hints[type] || hints.interval;
    const label = $('#sched-value-label');
    const input = $('#sched-value');
    if (label) label.textContent = text;
    if (input) input.placeholder = placeholder;
}

async function doAddSchedule() {
    const data = {
        agent_name: $('#sched-agent').value,
        schedule_type: $('#sched-type').value,
        schedule_value: $('#sched-value').value.trim(),
        prompt: $('#sched-prompt').value.trim(),
    };
    if (!data.schedule_value || !data.prompt) return;
    await api('/api/schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    closeModal('modal-sched');
    refreshSchedules();
}

async function toggleSchedule(id, enabled) {
    await api('/api/schedules/' + id, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
    });
    refreshSchedules();
}

async function deleteSchedule(id) {
    if (!confirm('Delete task ' + id + '?')) return;
    await api('/api/schedules/' + id, { method: 'DELETE' });
    refreshSchedules();
}


// --- Stage 0: ES-module exports ---
export { refreshSchedules, showAddSchedule };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { deleteSchedule, doAddSchedule, onSchedTypeChange, showAddSchedule, toggleSchedule });
