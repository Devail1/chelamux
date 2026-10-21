// --- Stage 0: ES-module imports ---
import { $, api, attrEsc, escHtml, shortTime, showModal } from './util';
import { _launcherData } from './launcher';
import { runCardNote, runStatusBadgeClass } from './runstate';
import { riskChip, trackerHref } from '../../frontend/src/features/taskmodal/model';
import { pollWork, postWorkDelete } from './work';

// ---------------------------------------------------------------------------
// Render: the Runs segment of WORK (the per-workflow dispatch tables)
//
// This module no longer FETCHES: work.js owns the single /api/dispatcher poll for
// the whole app and hands the payload here (renderDispatcher) and to the board
// (renderKanban). It used to run its own 30s timer against that endpoint while
// kanban.js ran a second one and the sidebar badges a third — three pollers, one
// dataset. Rendering is all that is left here, which is all it ever was.
// ---------------------------------------------------------------------------

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
    if (!res.is_git) lines.push('⚠ Not a git repo. The dispatcher needs one to branch & open PRs.');
    lines.push('Next: add ' + res.path + '/WORKFLOW.md to CHELA_DISPATCH_WORKFLOWS and restart the'
        + ' daemon, or run:  chela dispatch ' + res.path + '/WORKFLOW.md');
    setMsg('ok', lines.join('\n'));
    pollWork();
}

// The class table lives in runstate.js — pure, and therefore TESTED (node --test). This
// used to be an inline ternary chain, which is how CMX-68's two new run states shipped
// without badges: `needs_human`, the one state that means a human must look at this run,
// rendered in the same grey as an unknown status.
function _runStatusBadge(status) {
    return `<span class="badge ${runStatusBadgeClass(status)}">${escHtml(status || '?')}</span>`;
}

function _runPrCell(prUrl) {
    if (!prUrl) return '';
    // Pull "#NNN" out of the canonical github URL for a compact label; fall
    // back to "PR" so links to non-github hosts still render something useful.
    const m = String(prUrl).match(/\/pull\/(\d+)(?:[\/?#]|$)/);
    const label = m ? `#${m[1]}` : 'PR';
    return `<a class="pr-badge" href="${escHtml(prUrl)}" target="_blank" rel="noopener noreferrer" title="${escHtml(prUrl)}">${escHtml(label)}</a>`;
}

// Progress inside a dispatched run's OWN task list (issue #462) — `r.tasks`, as
// `tasklists.progress_for_run` computed it server-side: `null` whenever the run
// couldn't be joined to a live session's task directory (no window yet, a dead
// epoch, or a session that never wrote one), which must render as NOTHING here —
// not "0/0" — so an ordinary run with no task data looks exactly as it always has.
function _taskProgressChip(tasks) {
    if (!tasks || !tasks.total) return '';
    const label = `${tasks.done}/${tasks.total}`;
    const lines = [`${tasks.done} of ${tasks.total} tasks done`];
    if (tasks.in_progress) lines.push(`In progress: ${tasks.in_progress.subject}`);
    for (const b of (tasks.blocked || [])) {
        const by = (b.blocked_by || []).join(', ');
        lines.push(by ? `"${b.subject}" blocked by ${by}` : `"${b.subject}" blocked`);
    }
    return `<span class="task-progress-chip" title="${attrEsc(lines.join('\n'))}">☑ ${escHtml(label)}</span>`;
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

// CMX-94: on a Linear workflow the id links to its issue — the `tracker_url` the server
// read from Linear (never built from a workspace slug); no URL ⇒ plain text, as before.
function _trackerLink(item, inner) {
    const href = trackerHref(item);
    return href ? `<a class="run-tracker-link" href="${attrEsc(href)}" target="_blank" rel="noopener noreferrer">${inner}</a>` : inner;
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

function _renderRunsTable(runs, label, linear) {
    if (!runs || !runs.length) {
        return `<div style="padding:8px 0; color:var(--text-dim); font-size:11px;">No ${label}.</div>`;
    }
    return '<div class="table-wrap dispatcher-table-wrap"><table class="dispatcher-table"><thead><tr>' +
        '<th>Task</th><th>Status</th><th>Tasks</th><th>Branch</th><th>Window</th><th>Started</th><th>Ended</th><th>Attempt</th><th>PR</th><th>Error</th><th></th>' +
        '</tr></thead><tbody>' +
        runs.map(r => '<tr data-task-id="' + attrEsc(r.task_id || '') + '">' +
            `<td data-label="Task" title="${attrEsc(r.task_id)}"><b>${escHtml((r.title || '').slice(0, 80))}</b><div class="ts">${linear ? _trackerLink(r, escHtml(_runDisplayId(r))) : escHtml(_runDisplayId(r))}</div></td>` +
            _cell('Status', _runStatusBadge(r.status)) +
            _cell('Tasks', _taskProgressChip(r.tasks)) +
            _cell('Branch', escHtml(r.branch_name || '')) +
            _cell('Window', escHtml(r.window_name || '')) +
            _cell('Started', shortTime(r.started_at), 'ts') +
            _cell('Ended', shortTime(r.ended_at), 'ts') +
            _cell('Attempt', r.attempt || '') +
            _cell('PR', _runPrCell(r.pr_url)) +
            _cell('Error', _runNoteCell(r)) +
            _cell('Delete', _runDeleteBtn(r)) +
        '</tr>').join('') +
        '</tbody></table></div>';
}

// The Error cell — or, for a run closed by hand, "Closed — <reason>" instead (CMX-406).
function _runNoteCell(r) {
    const note = runCardNote(r);
    return note ? `<span title="${attrEsc(note.text)}">${escHtml(note.text.slice(0, 60))}</span>` : '';
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

// CMX-94: a Linear issue row — its identifier linked to the issue, its Linear state and
// its risk label. No file:line (a Linear issue has none) and no × — the source-line delete
// edits a file, and the dashboard does not write to Linear.
function _renderLinearTasks(tasks) {
    if (!tasks.length) return '';
    return '<div class="table-wrap dispatcher-table-wrap"><table class="dispatcher-table"><thead><tr>' +
        '<th>Issue</th><th>Title</th><th>State</th><th>Risk</th>' +
        '</tr></thead><tbody>' +
        tasks.map(t => {
            const risk = riskChip(t);
            return '<tr>' +
                _cell('Issue', _trackerLink(t, `<code style="font-size:11px;">${escHtml(t.id)}</code>`)) +
                `<td data-label="Title">${escHtml((t.title || '').slice(0, 200))}</td>` +
                _cell('State', escHtml(t.tracker_state || '') + (t.blocked ? ' · blocked' : '')) +
                _cell('Risk', risk ? `<span class="kanban-risk-chip ${risk.cls}" title="${attrEsc(risk.reason)}">${escHtml(risk.label)}</span>` : '') +
            '</tr>';
        }).join('') +
        '</tbody></table></div>';
}

const _SECTION_STYLE = 'font-size:11px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px;';

// Linear's open set is every non-terminal issue (Backlog + Todo + In Progress + In
// Review), so one flat list read as that many to-dos (Liav, 10-12). Split by the server's
// `group`: Ready first, then the started states, then Backlog collapsed.
function _renderLinearOpenTasks(tasks) {
    const ready = tasks.filter(t => t.group === 'ready');
    const started = tasks.filter(t => t.group === 'in_progress');
    const backlog = tasks.filter(t => !ready.includes(t) && !started.includes(t));
    return `
        <div class="open-section" style="${_SECTION_STYLE} margin-top:8px;">Ready (Todo) · ${ready.length}</div>
        ${_renderLinearTasks(ready)}
        <div class="open-section" style="${_SECTION_STYLE} margin-top:14px;">In progress / In review · ${started.length}</div>
        ${_renderLinearTasks(started)}
        <details class="open-backlog" style="margin-top:14px;">
            <summary class="open-section" style="${_SECTION_STYLE} cursor:pointer;">Backlog · ${backlog.length}</summary>
            ${_renderLinearTasks(backlog)}
        </details>`;
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
    // The POST + redraw is shared with the board's × (work.js) — one delete action,
    // two confirm UIs (a table row here, a card there).
    const err = await postWorkDelete(payload);
    if (err) _dispatcherDeleteShowError(td, err);
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
    const linear = wf.tracker_kind === 'linear';
    const open = wf.open_tasks || [];
    // `needs_human` is the state the operator must act on: it gets its own table above
    // Active, and leaves whichever bucket the server filed it in (awaiting review today).
    const needsHumanOf = runs => (runs || []).filter(r => r && r.status === 'needs_human');
    const without = runs => (runs || []).filter(r => !(r && r.status === 'needs_human'));
    const needsHuman = [...needsHumanOf(wf.active_runs), ...needsHumanOf(wf.awaiting_review_runs),
        ...needsHumanOf(wf.recent_runs)];
    const active = without(wf.active_runs);
    const awaiting = without(wf.awaiting_review_runs);
    const recent = without(wf.recent_runs);
    // On Linear the headline number is READY (Todo), not every non-terminal issue.
    const readyCount = open.filter(t => t.group === 'ready').length;
    const openSummary = linear
        ? `${readyCount} ready`
        : `${open.length} open task${open.length === 1 ? '' : 's'}`;
    const openHtml = linear
        ? _renderLinearOpenTasks(open)
        : `<div style="${_SECTION_STYLE} margin-top:8px;">Open tasks</div>${_renderOpenTasks(open)}`;
    return `
    <div class="card">
        <h3 style="margin-bottom:6px;">${path}</h3>
        <div style="font-size:11px; color:var(--text-dim); margin-bottom:10px;">
            ${openSummary} ·
            ${needsHuman.length} needing a human ·
            ${active.length} active ·
            ${awaiting.length} awaiting review ·
            ${recent.length} recent
        </div>
        ${errBanner}
        ${openHtml}
        <div style="${_SECTION_STYLE} margin-top:14px;">Needs a human</div>
        <div data-runs="needs_human">${_renderRunsTable(needsHuman, 'runs needing a human', linear)}</div>
        <div style="${_SECTION_STYLE} margin-top:14px;">Active runs</div>
        <div data-runs="active">${_renderRunsTable(active, 'active runs', linear)}</div>
        <div style="${_SECTION_STYLE} margin-top:14px;">Awaiting review</div>
        <div data-runs="awaiting">${_renderRunsTable(awaiting, 'awaiting review runs', linear)}</div>
        <div style="${_SECTION_STYLE} margin-top:14px;">Recent runs</div>
        <div data-runs="recent">${_renderRunsTable(recent, 'recent runs', linear)}</div>
    </div>`;
}

// Render the Runs segment from a payload work.js already fetched. No fetch here:
// the same object is what the board renders, which is why the two can no longer
// drift apart or double-poll.
function renderDispatcher(data) {
    const list = $('#dispatcher-list');
    const empty = $('#dispatcher-empty');
    if (!list || !empty) return;
    if (!data || !data.configured || !data.workflows || !data.workflows.length) {
        list.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';
    list.innerHTML = data.workflows.map(_renderWorkflowCard).join('');
}


// --- Stage 0: ES-module exports ---
export { _runDisplayId, _runPrCell, _taskProgressChip, renderDispatcher };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { dispatcherDeleteClick, dispatcherDeleteConfirm, doInitRepo, openInitRepo });
