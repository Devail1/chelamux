// ---------------------------------------------------------------------------
// ➕📐 NEW TASK (CMX-6) — create a Linear issue from the Work view / ⌘K palette.
//
// The form POSTs to the dashboard (/api/dispatcher/linear/issue); the DASHBOARD calls
// Linear. The API key never reaches this page. The workflow list and the "blocked by"
// choices are read off the /api/dispatcher payload work.js already polls — no second
// fetch, and no Linear read from the browser.
//
// ⛔ A failed submit KEEPS every field: the error shows under the form and the typed
// brief stays where it was. Only a success clears the form.
// ---------------------------------------------------------------------------
import { $, BASE_PATH, closeModal, escHtml, attrEsc, showModal } from './util.js';
import { lastWorkData, pollWork } from './work.js';

// The workflows chela can create issues in — those whose tracker is linear.
export function linearWorkflows(data) {
    return ((data && data.workflows) || []).filter(wf => wf && wf.tracker_kind === 'linear');
}

// Open issues a new task may be blocked by: the queue's open tasks plus the in-flight
// runs (claimed / running / in review) — every one an open issue in the team.
export function blockerChoices(wf) {
    if (!wf) return [];
    const seen = new Set();
    const out = [];
    const add = (id, title) => {
        if (!id || seen.has(id)) return;
        seen.add(id);
        out.push({ id, title: title || '' });
    };
    for (const t of (wf.open_tasks || [])) add(t.id, t.title);
    for (const r of [...(wf.active_runs || []), ...(wf.awaiting_review_runs || [])]) {
        add(r.task_id, r.title);
    }
    return out;
}

function _fillBlockers() {
    const wfSel = $('#newtask-workflow');
    const wf = linearWorkflows(lastWorkData()).find(w => w.path === (wfSel && wfSel.value));
    const sel = $('#newtask-blocked');
    if (!sel) return;
    const kept = new Set([...sel.selectedOptions].map(o => o.value));
    sel.innerHTML = blockerChoices(wf).map(c =>
        `<option value="${attrEsc(c.id)}"${kept.has(c.id) ? ' selected' : ''}>`
        + `${escHtml(c.id)} — ${escHtml(c.title.slice(0, 80))}</option>`).join('');
}

function openNewTask() {
    const wfs = linearWorkflows(lastWorkData());
    const wfSel = $('#newtask-workflow');
    if (wfSel) {
        const prev = wfSel.value;
        wfSel.innerHTML = wfs.map(w =>
            `<option value="${attrEsc(w.path)}">${escHtml(w.project_key || w.path)}</option>`).join('');
        if (wfs.some(w => w.path === prev)) wfSel.value = prev;
    }
    const err = $('#newtask-error');
    if (err) err.textContent = wfs.length ? ''
        : 'No workflow here uses a Linear tracker (tracker: kind: linear).';
    _fillBlockers();
    showModal('modal-newtask');
    const title = $('#newtask-title');
    if (title) title.focus();
}

function _clearForm() {
    for (const id of ['#newtask-title', '#newtask-desc']) {
        const el = $(id);
        if (el) el.value = '';
    }
    const prio = $('#newtask-priority');
    if (prio) prio.value = '0';
    const sel = $('#newtask-blocked');
    if (sel) [...sel.options].forEach(o => { o.selected = false; });
}

async function submitNewTask() {
    const err = $('#newtask-error');
    const btn = $('#newtask-submit');
    const payload = {
        workflow_path: ($('#newtask-workflow') || {}).value || '',
        title: (($('#newtask-title') || {}).value || '').trim(),
        description: ($('#newtask-desc') || {}).value || '',
        priority: parseInt(($('#newtask-priority') || {}).value || '0', 10) || 0,
        blocked_by: [...((($('#newtask-blocked') || {}).selectedOptions) || [])].map(o => o.value),
    };
    if (!payload.title) {
        if (err) err.textContent = 'A title is required.';
        return null;
    }
    if (err) err.textContent = '';
    if (btn) btn.disabled = true;
    let data = {};
    try {
        const resp = await fetch(BASE_PATH + '/api/dispatcher/linear/issue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        try { data = (await resp.json()) || {}; } catch (_) { data = {}; }
        if (!resp.ok || !data.ok) {
            // ⛔ Keep the form: the brief the user typed is still in its fields.
            if (err) err.textContent = data.error || `Linear refused it (HTTP ${resp.status}).`;
            return null;
        }
    } catch (e) {
        if (err) err.textContent = 'Request failed — nothing was created. ' + String(e);
        return null;
    } finally {
        if (btn) btn.disabled = false;
    }
    _clearForm();
    closeModal('modal-newtask');
    const warn = (data.warnings || []).join('; ');
    if (warn) alert(`${data.issue.identifier} created, but: ${warn}`);
    await pollWork();          // the new issue lands in the queue on this refresh
    return data.issue;
}

export { openNewTask, submitNewTask };

window.chela = window.chela || {};
Object.assign(window.chela, { openNewTask, submitNewTask, newTaskWorkflowChanged: _fillBlockers });
