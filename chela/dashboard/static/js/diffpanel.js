// ---------------------------------------------------------------------------
// PER-SESSION DIFF PANEL (CMX-299) — the "Files" chip on a wall tile's bottom
// bar (terminals.js's _ctxBarHTML) opens this: every file the session's live
// pane cwd has changed since its last commit, and a click-through unified
// diff for any one of them. Backed by chela.diffsurface via
// /api/agents/<wid>/diff + /api/agents/<wid>/diff/patch — both read-only,
// no dependency on the dispatcher (works for a plain attended checkout the
// same as a dispatched worktree).
// ---------------------------------------------------------------------------
import { $, $$, api, attrEsc, closeModal, escHtml, showModal } from './util.js';
import { patchLineClass, statusMeta, summaryLabel } from './diffpanelmodel.js';

// The wid the currently-open modal is showing — set on open, cleared on
// close. A single flight target: opening a second diff modal before closing
// the first can't happen (one modal, one DOM node), so one module-level
// value is enough, same as taskmodal.js needs no per-instance state.
let _openWid = null;

function _patchHtml(patchText) {
    if (!patchText) return '<div class="diff-patch-empty">No diff text for this file.</div>';
    const lines = patchText.replace(/\n$/, '').split('\n');
    return '<pre class="diff-patch-pre">' + lines.map(line =>
        `<span class="diff-patch-line ${patchLineClass(line)}">${escHtml(line)}</span>`
    ).join('\n') + '</pre>';
}

async function _loadDiffPatch(wid, path, rowEl) {
    const view = $('#diff-patch-view');
    if (!view) return;
    $$('.diff-file-row.active').forEach(el => el.classList.remove('active'));
    if (rowEl) rowEl.classList.add('active');
    view.innerHTML = '<div class="diff-patch-empty">Loading…</div>';
    const res = await api(`/api/agents/${encodeURIComponent(wid)}/diff/patch?path=${encodeURIComponent(path)}`);
    if (wid !== _openWid) return;  // modal moved on (closed / reopened for another wid) while this was in flight
    if (!res || res.ok === false) {
        view.innerHTML = `<div class="diff-patch-empty">${escHtml((res && res.error) || 'Could not load diff.')}</div>`;
        return;
    }
    view.innerHTML = _patchHtml(res.patch || '');
}

function _fileListHtml(state) {
    const files = (state && Array.isArray(state.files)) ? state.files : [];
    if (!files.length) return '<div class="diff-file-list-empty">Nothing to show.</div>';
    return '<ul class="diff-file-list">' + files.map(f => {
        const meta = statusMeta(f.status);
        return `<li class="diff-file-row" data-diff-file="${attrEsc(f.path)}" tabindex="0">
          <span class="diff-status-chip ${meta.cls}" title="${escHtml(f.status)}">${meta.label}</span>
          <span class="diff-file-path">${escHtml(f.path)}</span>
          <span class="diff-file-stat"><span class="diff-add">+${escHtml(String(f.additions))}</span> <span class="diff-del">−${escHtml(String(f.deletions))}</span></span>
        </li>`;
    }).join('') + '</ul>';
}

function _render(wid, state) {
    const content = $('#diff-modal-content');
    if (!content) return;
    content.innerHTML = `
      <div class="diff-modal-head">
        <h3>Changed files</h3>
        <div class="diff-modal-summary">${escHtml(summaryLabel(state))}</div>
      </div>
      <div class="diff-modal-body">
        <div class="diff-file-pane">${_fileListHtml(state)}</div>
        <div class="diff-patch-view" id="diff-patch-view">
          <div class="diff-patch-empty">Select a file to view its diff.</div>
        </div>
      </div>`;
}

async function openDiffModal(wid) {
    if (!wid) return;
    _openWid = wid;
    const content = $('#diff-modal-content');
    if (content) content.innerHTML = '<div class="diff-modal-head"><h3>Changed files</h3></div><div class="diff-patch-empty">Loading…</div>';
    showModal('modal-diff');
    _bindDiffModalDismiss();
    const state = await api(`/api/agents/${encodeURIComponent(wid)}/diff`);
    if (wid !== _openWid) return;  // closed (or reopened elsewhere) before the fetch resolved
    _render(wid, state);
}

function closeDiffModal() {
    _openWid = null;
    closeModal('modal-diff');
    _unbindDiffModalDismiss();
}

function _diffModalKey(e) {
    if (e.key === 'Escape') closeDiffModal();
}

function _diffModalBackdrop(e) {
    if (e.target && e.target.id === 'modal-diff') closeDiffModal();
}

function _diffModalClick(e) {
    const row = e.target.closest('.diff-file-row');
    if (!row || !_openWid) return;
    const path = row.dataset.diffFile;
    if (path) _loadDiffPatch(_openWid, path, row);
}

function _bindDiffModalDismiss() {
    document.addEventListener('keydown', _diffModalKey, true);
    const overlay = $('#modal-diff');
    if (overlay) {
        overlay.addEventListener('click', _diffModalBackdrop);
        overlay.addEventListener('click', _diffModalClick);
    }
}

function _unbindDiffModalDismiss() {
    document.removeEventListener('keydown', _diffModalKey, true);
    const overlay = $('#modal-diff');
    if (overlay) {
        overlay.removeEventListener('click', _diffModalBackdrop);
        overlay.removeEventListener('click', _diffModalClick);
    }
}

// --- Stage 0: ES-module exports ---
export { openDiffModal, closeDiffModal };

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { openDiffModal, closeDiffModal });
