// 🙋 Access requests from sandboxed guests (CMX-7).
//
// A guest (or its Claude) in a sandboxed session can ASK for more access — a host mount,
// a domain in web mode, a named operation — with `chela-request` inside the container.
// This is the operator's half: a pill (#btn-access-req, in .safety-float beside the
// shares kill switch) that shows how many requests wait, and a sheet to approve or deny
// each one. Nothing here decides anything by itself: approve needs a click, it lasts
// 60 min unless another duration is picked, and a mount is read-only unless the guest
// asked for write AND "allow write" is ticked. A request the server refuses (a secrets
// directory, $HOME, /mnt/*…) shows the reason and has no Approve button.
import { api, attrEsc, escHtml } from './util.js';

const DURATIONS = [15, 60, 240, 1440];
let _rows = [];
let _state = { share_typing: false, default_minutes: 60 };

export function durLabel(n) {
    return n % 1440 === 0 ? (n / 1440) + (n === 1440 ? ' day' : ' days')
        : n % 60 === 0 ? (n / 60) + ' h' : n + ' min';
}

function _who(r) { return r.window || r.wid || ('sandbox ' + r.sid); }

function _left(s) {
    if (s == null) return '';
    const m = Math.ceil(s / 60);
    return m >= 60 ? Math.floor(m / 60) + 'h ' + (m % 60) + 'm left' : m + ' min left';
}

export function rowHTML(r, state) {
    const st = state || _state;
    const id = attrEsc(r.id);
    const head = `<div class="ar-hd"><span class="ar-who">${escHtml(_who(r))}</span>
        <span class="ar-status ar-${attrEsc(r.status)}">${escHtml(r.status)}</span></div>
      <div class="ar-what">${escHtml(r.description || '')}</div>
      ${r.reason ? `<div class="ar-why">“${escHtml(r.reason)}”</div>` : ''}`;
    if (r.status === 'approved') {
        return `<div class="ar-row" data-id="${id}">${head}
          <div class="ar-meta">${escHtml(_left(r.seconds_left))}${r.rw ? ' · read-write' : r.kind === 'mount' ? ' · read-only' : ''}</div>
          <button class="ar-revoke" type="button" data-id="${id}">Revoke</button></div>`;
    }
    if (r.status !== 'pending') return `<div class="ar-row ar-done" data-id="${id}">${head}</div>`;
    if (r.refusal) {
        return `<div class="ar-row" data-id="${id}">${head}
          <div class="ar-refused">⛔ Can't be approved: ${escHtml(r.refusal)}</div>
          <button class="ar-deny" type="button" data-id="${id}">Deny</button></div>`;
    }
    const dflt = st.default_minutes || 60;
    const opts = [...new Set([...DURATIONS, dflt])].sort((a, b) => a - b)
        .map(n => `<option value="${n}"${n === dflt ? ' selected' : ''}>${escHtml(durLabel(n))}</option>`).join('');
    const rw = r.kind === 'mount' && r.access === 'rw'
        ? `<label class="ar-rw"><input type="checkbox" class="ar-rw-in"> allow write (the guest asked for it — read-only otherwise)</label>` : '';
    const off = st.share_typing ? '' : ' disabled';
    return `<div class="ar-row" data-id="${id}">${head}
      <div class="ar-ctl"><select class="ar-dur" aria-label="How long the approval lasts">${opts}</select>${rw}
        <button class="ar-approve" type="button" data-id="${id}"${off}>Approve</button>
        <button class="ar-deny" type="button" data-id="${id}">Deny</button></div>
      ${st.share_typing ? '' : '<div class="ar-refused">Guest typing is off — turn it on in Settings to approve.</div>'}
      <div class="ar-err" hidden></div></div>`;
}

export function pendingCount(rows) {
    return (rows || []).filter(r => r.status === 'pending').length;
}

function _renderPill() {
    const btn = document.getElementById('btn-access-req');
    if (!btn) return;
    const n = pendingCount(_rows);
    btn.hidden = n === 0;
    const txt = btn.querySelector('.ar-pill-text');
    if (txt) txt.textContent = '🙋 ' + n + ' access request' + (n === 1 ? '' : 's');
    btn.setAttribute('aria-label', n + ' sandboxed-session access request' + (n === 1 ? '' : 's') + ' waiting — tap to decide');
}

export async function tickAccessRequests() {
    try {
        const data = await api('/api/share-requests');
        _rows = (data && data.requests) || [];
        _state = { share_typing: !!(data && data.share_typing), default_minutes: (data && data.default_minutes) || 60 };
    } catch (_) { return; }
    _renderPill();
    if (document.getElementById('access-req-backdrop')) _buildSheet();
}

async function _decide(id, action, body) {
    let resp;
    try {
        resp = await api('/api/share-requests/' + encodeURIComponent(id) + '/' + action, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
    } catch (_) { resp = null; }
    return resp && resp.ok ? { ok: true } : { ok: false, error: (resp && resp.error) || 'Could not ' + action + ' the request' };
}

function _buildSheet() {
    const old = document.getElementById('access-req-backdrop');
    if (old) old.remove();
    const backdrop = document.createElement('div');
    backdrop.id = 'access-req-backdrop';
    backdrop.className = 'shares-backdrop';
    backdrop.onclick = (e) => { if (e.target === backdrop) closeAccessRequests(); };
    const sheet = document.createElement('div');
    sheet.className = 'shares-sheet access-req-sheet';
    sheet.innerHTML = `<div class="ss-hd">🙋 Access requests
        <button class="ss-close" type="button" aria-label="Close">&times;</button></div>
      <div class="ss-sub">A sandboxed guest asked for more access. Nothing changes until you approve. An approved mount restarts that session's container with it — read-only unless you allow write — and a live typing share of it may end. Stopping the share or turning Guest typing off revokes every approval.</div>
      <div class="ss-list">${_rows.map(r => rowHTML(r)).join('') || '<div class="ss-empty">No access requests.</div>'}</div>`;
    backdrop.appendChild(sheet);
    document.body.appendChild(backdrop);
    sheet.querySelector('.ss-close').onclick = closeAccessRequests;
    sheet.querySelectorAll('.ar-row').forEach(row => {
        const id = row.dataset.id;
        const err = row.querySelector('.ar-err');
        const run = async (action, body) => {
            const r = await _decide(id, action, body);
            if (!r.ok && err) { err.textContent = r.error; err.hidden = false; return; }
            await tickAccessRequests();
        };
        const ap = row.querySelector('.ar-approve');
        if (ap) ap.onclick = () => {
            ap.disabled = true;
            const dur = row.querySelector('.ar-dur');
            const rw = row.querySelector('.ar-rw-in');
            run('approve', { minutes: dur ? parseInt(dur.value, 10) : 60, rw: !!(rw && rw.checked) });
        };
        const dn = row.querySelector('.ar-deny');
        if (dn) dn.onclick = () => { dn.disabled = true; run('deny'); };
        const rv = row.querySelector('.ar-revoke');
        if (rv) rv.onclick = () => { rv.disabled = true; run('revoke'); };
    });
    document.addEventListener('keydown', _key, true);
}

function _key(e) { if (e.key === 'Escape') closeAccessRequests(); }

export async function openAccessRequests() {
    await tickAccessRequests();
    _buildSheet();
}

export function closeAccessRequests() {
    const bd = document.getElementById('access-req-backdrop');
    if (bd) bd.remove();
    document.removeEventListener('keydown', _key, true);
}

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { openAccessRequests, closeAccessRequests });
