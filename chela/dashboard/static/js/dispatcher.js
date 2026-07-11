// --- Stage 0: ES-module imports ---
import { $, BASE_PATH, api, attrEsc, escHtml, shortTime, showModal } from './util.js';
import { _launcherData } from './launcher.js';

// ---------------------------------------------------------------------------
// Render: Dispatcher (work-item dispatcher per-workflow view)
//
// Polls /api/dispatcher every DISPATCHER_REFRESH_MS on its own timer so the
// panel updates even when the global refresh loop fires another tab. The
// timer is owned here, not by the tab switch — flipping back into the
// Dispatcher tab does a one-shot fetch; the interval keeps it fresh after.
// ---------------------------------------------------------------------------

const DISPATCHER_REFRESH_MS = 30000;
let _dispatcherTimer = null;

// --- Init a repo -----------------------------------------------------------
// Seed a starter WORKFLOW.md + TODO.md into a repo via POST /api/dispatcher/init
// (server-side starter.seed_repo, which never overwrites). The path field offers
// the launcher's known project dirs as datalist suggestions when available.
function openInitRepo() {
    const result = document.getElementById('init-result');
    if (result) { result.textContent = ''; result.className = 'init-result'; }
    const dl = document.getElementById('init-suggestions');
    if (dl && typeof _launcherData !== 'undefined' && _launcherData) {
        const paths = [...(_launcherData.favorites || []), ...(_launcherData.recent || [])]
            .map(e => e && e.path).filter(Boolean);
        dl.innerHTML = [...new Set(paths)].map(p => `<option value="${attrEsc(p)}"></option>`).join('');
    }
    showModal('modal-init');
    const inp = document.getElementById('init-path');
    setTimeout(() => inp && inp.focus(), 50);
}

async function doInitRepo() {
    const inp = document.getElementById('init-path');
    const result = document.getElementById('init-result');
    const path = ((inp && inp.value) || '').trim();
    const setMsg = (cls, text) => { if (result) { result.className = 'init-result ' + cls; result.textContent = text; } };
    if (!path) { setMsg('err', 'Enter a repo path.'); return; }
    setMsg('', 'Seeding…');
    let res;
    try {
        res = await api('/api/dispatcher/init', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
    } catch (e) { setMsg('err', 'Request failed.'); return; }
    if (!res || !res.ok) { setMsg('err', (res && res.error) || 'Failed.'); return; }

    const lines = [];
    if (res.created.length) lines.push('✓ Created ' + res.created.join(' + '));
    if (res.skipped.length) lines.push('• Skipped (already exist): ' + res.skipped.join(' + '));
    if (!res.is_git) lines.push('⚠ Not a git repo — the dispatcher needs one to branch & open PRs.');
    lines.push('Next: add ' + res.path + '/WORKFLOW.md to CHELA_DISPATCH_WORKFLOWS and restart the'
        + ' daemon, or run:  chela dispatch ' + res.path + '/WORKFLOW.md');
    setMsg('ok', lines.join('\n'));
    if (typeof refreshDispatcher === 'function') refreshDispatcher();
}

function _runStatusBadge(status) {
    const cls = (status === 'running' || status === 'claimed') ? 'badge-on'
              : (status === 'failed') ? 'badge-off'
              : (status === 'awaiting_review') ? 'badge-awaiting'
              : 'badge-priority-low';
    return `<span class="badge ${cls}">${escHtml(status || '?')}</span>`;
}

function _runPrCell(prUrl) {
    if (!prUrl) return '';
    // Pull "#NNN" out of the canonical github URL for a compact label; fall
    // back to "PR" so links to non-github hosts still render something useful.
    const m = String(prUrl).match(/\/pull\/(\d+)(?:[\/?#]|$)/);
    const label = m ? `#${m[1]}` : 'PR';
    return `<a class="pr-badge" href="${escHtml(prUrl)}" target="_blank" rel="noopener noreferrer" title="${escHtml(prUrl)}">${escHtml(label)}</a>`;
}

// data-label + cell-empty: read by the @media (max-width:768px) block to
// re-flow these tables as stacked label/value rows. _cell() collapses
// no-content cells so the mobile view hides them entirely instead of
// printing an empty "Error" / "Ended" stub.
function _cell(label, content, extraCls) {
    const hasContent = content != null && content !== '';
    const cls = (hasContent ? '' : 'cell-empty') + (extraCls ? ' ' + extraCls : '');
    const clsAttr = cls.trim() ? ` class="${cls.trim()}"` : '';
    return `<td data-label="${escHtml(label)}"${clsAttr}>${hasContent ? content : ''}</td>`;
}

function _runDisplayId(r) {
    // PROJECT_KEY-N is the primary identity for runs with a task_number;
    // pre-migration rows (task_number=null) fall back to the legacy short hash
    // so historical PRs stay linkable.
    if (r.project_key && r.task_number != null) {
        return `${r.project_key}-${r.task_number}`;
    }
    return r.task_id || '';
}

function _runDeleteBtn(r) {
    if (!r.task_id) return '';
    return `<button class="dispatcher-delete-btn" type="button"
                   data-del-kind="run"
                   data-task-id="${attrEsc(r.task_id)}"
                   onclick="chela.dispatcherDeleteClick(this)"
                   title="Delete this row" aria-label="Delete">&times;</button>`;
}

function _openDeleteBtn(t) {
    if (!t.file || !t.title) return '';
    return `<button class="dispatcher-delete-btn" type="button"
                   data-del-kind="source-line"
                   data-file="${attrEsc(t.file)}"
                   data-text="${attrEsc(t.title)}"
                   onclick="chela.dispatcherDeleteClick(this)"
                   title="Delete this row" aria-label="Delete">&times;</button>`;
}

function _renderRunsTable(runs, label) {
    if (!runs || !runs.length) {
        return `<div style="padding:8px 0; color:var(--text-dim); font-size:11px;">No ${label}.</div>`;
    }
    return '<div class="table-wrap dispatcher-table-wrap"><table class="dispatcher-table"><thead><tr>' +
        '<th>Task</th><th>Status</th><th>Branch</th><th>Window</th><th>Started</th><th>Ended</th><th>Attempt</th><th>PR</th><th>Error</th><th></th>' +
        '</tr></thead><tbody>' +
        runs.map(r => '<tr data-task-id="' + attrEsc(r.task_id || '') + '">' +
            `<td data-label="Task" title="${attrEsc(r.task_id)}"><b>${escHtml((r.title || '').slice(0, 80))}</b><div class="ts">${escHtml(_runDisplayId(r))}</div></td>` +
            _cell('Status', _runStatusBadge(r.status)) +
            _cell('Branch', escHtml(r.branch_name || '')) +
            _cell('Window', escHtml(r.window_name || '')) +
            _cell('Started', shortTime(r.started_at), 'ts') +
            _cell('Ended', shortTime(r.ended_at), 'ts') +
            _cell('Attempt', r.attempt || '') +
            _cell('PR', _runPrCell(r.pr_url)) +
            _cell('Error', r.last_error ? `<span title="${attrEsc(r.last_error)}">${escHtml(r.last_error.slice(0, 60))}</span>` : '') +
            _cell('Delete', _runDeleteBtn(r)) +
        '</tr>').join('') +
        '</tbody></table></div>';
}

function _renderOpenTasks(tasks) {
    if (!tasks || !tasks.length) {
        return `<div style="padding:8px 0; color:var(--text-dim); font-size:11px;">No open tasks.</div>`;
    }
    return '<div class="table-wrap dispatcher-table-wrap"><table class="dispatcher-table"><thead><tr>' +
        '<th>Task ID</th><th>Title</th><th>Source</th><th></th>' +
        '</tr></thead><tbody>' +
        tasks.map(t => '<tr>' +
            `<td data-label="Task ID"><code style="font-size:11px;">${escHtml(t.id)}</code></td>` +
            `<td data-label="Title">${escHtml((t.title || '').slice(0, 200))}</td>` +
            `<td data-label="Source" class="ts">${escHtml(t.file)}:${t.line_number}</td>` +
            _cell('Delete', _openDeleteBtn(t)) +
        '</tr>').join('') +
        '</tbody></table></div>';
}

// Inline-confirm in a dispatcher row: collapse the row to a single full-width
// cell carrying the confirm UI, then either restore on cancel or refresh the
// table on success. Same affordance as the kanban × — the only difference is
// the host element being a <tr> instead of a card.
function dispatcherDeleteClick(btn) {
    const row = btn.closest('tr');
    if (!row || row.classList.contains('row-confirming')) return;
    const colCount = row.cells.length;
    row.classList.add('row-confirming');
    const td = document.createElement('td');
    td.className = 'row-confirm-cell';
    td.colSpan = colCount;
    td.dataset.delKind = btn.dataset.delKind || '';
    td.dataset.taskId = btn.dataset.taskId || '';
    td.dataset.file = btn.dataset.file || '';
    td.dataset.text = btn.dataset.text || '';
    td.innerHTML = `
      <div class="kanban-confirm">
        <span class="kanban-confirm-msg">Delete this row?</span>
        <button class="btn-confirm" type="button" onclick="chela.dispatcherDeleteConfirm(this, true)">Delete</button>
        <button type="button" onclick="chela.dispatcherDeleteConfirm(this, false)">Cancel</button>
      </div>`;
    row.appendChild(td);
}

async function dispatcherDeleteConfirm(actionBtn, ok) {
    const td = actionBtn.closest('td');
    if (!td) return;
    const row = td.closest('tr');
    if (!ok) {
        td.remove();
        if (row) row.classList.remove('row-confirming');
        return;
    }
    const kind = td.dataset.delKind;
    const payload = { kind };
    if (kind === 'run') payload.task_id = td.dataset.taskId;
    else if (kind === 'source-line') {
        payload.file = td.dataset.file;
        payload.text = td.dataset.text;
    }
    td.querySelector('.kanban-confirm').innerHTML = '<span class="kanban-confirm-msg">Deleting…</span>';
    let resp, data = {};
    try {
        resp = await fetch(BASE_PATH + '/api/dispatcher/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        try { data = await resp.json(); } catch (_) { data = {}; }
    } catch (e) {
        _dispatcherDeleteShowError(td, String(e));
        return;
    }
    if (!resp.ok || !data.ok) {
        _dispatcherDeleteShowError(td, data.error || `HTTP ${resp.status}`);
        return;
    }
    refreshDispatcher();
}

function _dispatcherDeleteShowError(td, msg) {
    td.querySelector('.kanban-confirm').innerHTML = `
        <span class="kanban-confirm-msg" style="color:var(--red);">${escHtml(msg)}</span>
        <button type="button" onclick="chela.dispatcherDeleteConfirm(this, false)">Close</button>`;
}

function _renderWorkflowCard(wf) {
    const path = escHtml(wf.path);
    const errBanner = wf.error
        ? `<div style="color:var(--red); font-size:11px; padding:4px 0;">Error: ${escHtml(wf.error)}</div>`
        : '';
    const openCount = (wf.open_tasks || []).length;
    const activeCount = (wf.active_runs || []).length;
    const awaitingCount = (wf.awaiting_review_runs || []).length;
    const recentCount = (wf.recent_runs || []).length;
    return `
    <div class="card">
        <h3 style="margin-bottom:6px;">${path}</h3>
        <div style="font-size:11px; color:var(--text-dim); margin-bottom:10px;">
            ${openCount} open task${openCount === 1 ? '' : 's'} ·
            ${activeCount} active ·
            ${awaitingCount} awaiting review ·
            ${recentCount} recent
        </div>
        ${errBanner}
        <div style="font-size:11px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px; margin-top:8px;">Open tasks</div>
        ${_renderOpenTasks(wf.open_tasks)}
        <div style="font-size:11px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px; margin-top:14px;">Active runs</div>
        ${_renderRunsTable(wf.active_runs, 'active runs')}
        <div style="font-size:11px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px; margin-top:14px;">Awaiting review</div>
        ${_renderRunsTable(wf.awaiting_review_runs, 'awaiting review runs')}
        <div style="font-size:11px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px; margin-top:14px;">Recent runs</div>
        ${_renderRunsTable(wf.recent_runs, 'recent runs')}
    </div>`;
}

async function refreshDispatcher() {
    let data;
    try {
        data = await api('/api/dispatcher');
    } catch (e) {
        console.error('refreshDispatcher', e);
        return;
    }
    const list = $('#dispatcher-list');
    const empty = $('#dispatcher-empty');
    if (!data.configured || !data.workflows || !data.workflows.length) {
        list.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';
    list.innerHTML = data.workflows.map(_renderWorkflowCard).join('');
}

function startDispatcherTimer() {
    stopDispatcherTimer();
    _dispatcherTimer = setInterval(refreshDispatcher, DISPATCHER_REFRESH_MS);
}

function stopDispatcherTimer() {
    if (_dispatcherTimer) { clearInterval(_dispatcherTimer); _dispatcherTimer = null; }
}


// --- Stage 0: ES-module exports ---
export { _runDisplayId, _runPrCell, refreshDispatcher, startDispatcherTimer, stopDispatcherTimer };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { dispatcherDeleteClick, dispatcherDeleteConfirm, doInitRepo, openInitRepo });
