// ⚖️ Pending approvals (CMX-103) — every request waiting on the operator, on every page.
//
// `chela merge --override` files a request and returns; until now the only pointer to it
// was the Telegram card or the requester's CLI output, so a dashboard-only operator never
// knew one was waiting. This is a pill (#btn-approvals, in .safety-float beside the shares
// kill switch — visible from every view) with the count, and a sheet listing each request:
// what, who asked, why, how old, and how long until it expires.
//
// Approve / Deny POST /api/approvals/<id>/<action>, which goes through the SAME decide call
// as the /override/<id> page. Approve is two taps: the first only arms the row ("Confirm —
// merge now"), the second posts `confirm: true`; a single mis-tap never merges. A request
// the server already knows cannot merge (head moved, CI not green, conflicting) shows WHY,
// with no Approve. A share guest gets `can_decide: false` and no controls at all.
//
// Optional browser notification on a NEW request: opt-in (Settings → Notifications, key
// chela_approval_notify), off by default, silently nothing where the Notification API is
// missing or permission was refused.
import { api, attrEsc, escHtml } from './util.js';

const NOTIFY_KEY = 'chela_approval_notify';
let _rows = [];
let _canDecide = false;
let _seen = null;          // ids already known — null until the first fetch primes it
const _armed = new Set();  // rows whose Approve was tapped once (awaiting the confirm tap)

export function dur(s) {
    s = Math.max(0, Math.round(s || 0));
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60);
    if (m < 60) return m + ' min';
    const h = Math.floor(m / 60);
    return h < 24 ? h + 'h ' + (m % 60) + 'm' : Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
}

export function rowHTML(r, canDecide, armed) {
    const id = attrEsc(r.id);
    const pr = r.pr_url ? ` · <a href="${attrEsc(r.pr_url)}" target="_blank" rel="noopener">PR</a>` : '';
    const head = `<div class="ap-hd"><span class="ap-what">${escHtml(r.what || r.id)}</span></div>
      <div class="ap-meta">asked by ${escHtml(r.actor || 'unknown')} · ${escHtml(dur(r.age_s))} ago · expires in ${escHtml(dur(r.expires_in_s))}${pr}</div>
      ${r.reason ? `<div class="ap-why">“${escHtml(r.reason)}”</div>` : ''}`;
    if (!canDecide) return `<div class="ap-row" data-id="${id}">${head}</div>`;
    if (r.blocked) {
        return `<div class="ap-row" data-id="${id}">${head}
          <div class="ap-blocked">⛔ Can't be approved: ${escHtml(r.blocked)}</div>
          <div class="ap-ctl"><button class="ap-deny" type="button">Deny</button></div>
          <div class="ap-err" hidden></div></div>`;
    }
    const ctl = armed
        ? `<button class="ap-confirm" type="button">Confirm — merge now</button>
           <button class="ap-cancel" type="button">Cancel</button>`
        : `<button class="ap-approve" type="button">Approve…</button>
           <button class="ap-deny" type="button">Deny</button>`;
    return `<div class="ap-row${armed ? ' ap-armed' : ''}" data-id="${id}">${head}
      ${armed ? '<div class="ap-warn">Approving merges NOW, past the judge — after re-checking the head, CI and mergeability.</div>' : ''}
      <div class="ap-ctl">${ctl}</div><div class="ap-err" hidden></div></div>`;
}

function _renderPill() {
    const btn = document.getElementById('btn-approvals');
    if (!btn) return;
    const n = _rows.length;
    btn.hidden = n === 0;
    const txt = btn.querySelector('.ap-pill-text');
    if (txt) txt.textContent = '⚖️ ' + n + ' pending approval' + (n === 1 ? '' : 's');
    btn.setAttribute('aria-label', n + ' pending approval' + (n === 1 ? '' : 's') + ' — tap to review');
}

export function notifyEnabled() {
    try { return localStorage.getItem(NOTIFY_KEY) === '1'; } catch (_) { return false; }
}

// A browser notification per NEW request. Never throws, never prompts — the permission is
// asked only from the Settings toggle (a user gesture).
function _notifyNew(rows) {
    const fresh = rows.filter(r => !_seen.has(r.id));
    rows.forEach(r => _seen.add(r.id));
    if (!fresh.length || !notifyEnabled()) return;
    try {
        const N = window.Notification;
        if (!N || N.permission !== 'granted') return;
        fresh.forEach(r => {
            const n = new N('chela: approval needed', { body: r.what || r.id, tag: 'chela-approval-' + r.id });
            n.onclick = () => { try { window.focus(); } catch (_) { /* ignore */ } openApprovals(); };
        });
    } catch (_) { /* unsupported / blocked — silently nothing */ }
}

export async function tickApprovals() {
    let data;
    try { data = await api('/api/approvals'); } catch (_) { return; }
    if (!data || !Array.isArray(data.approvals)) return;
    _rows = data.approvals;
    _canDecide = data.can_decide === true;
    const live = new Set(_rows.map(r => r.id));
    [..._armed].forEach(id => { if (!live.has(id)) _armed.delete(id); });
    if (_seen === null) _seen = new Set(_rows.map(r => r.id));   // prime: no burst on load
    else _notifyNew(_rows);
    _renderPill();
    if (document.getElementById('approvals-backdrop')) _buildSheet();
}

async function _decide(id, action) {
    let resp;
    try {
        resp = await api('/api/approvals/' + encodeURIComponent(id) + '/' + action, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(action === 'approve' ? { confirm: true } : {}),
        });
    } catch (_) { resp = null; }
    return resp && resp.ok ? { ok: true, resp } : { ok: false, error: (resp && resp.error) || 'Could not ' + action + ' the request' };
}

function _buildSheet() {
    const old = document.getElementById('approvals-backdrop');
    if (old) old.remove();
    const backdrop = document.createElement('div');
    backdrop.id = 'approvals-backdrop';
    backdrop.className = 'shares-backdrop';
    backdrop.onclick = (e) => { if (e.target === backdrop) closeApprovals(); };
    const sheet = document.createElement('div');
    sheet.className = 'shares-sheet approvals-sheet';
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-label', 'Pending approvals');
    sheet.innerHTML = `<div class="ss-hd">⚖️ Pending approvals
        <button class="ss-close" type="button" aria-label="Close">&times;</button></div>
      <div class="ss-sub">Requests waiting on you. Nothing merges until you approve — and an approval re-checks that the head is unchanged, CI is green and the PR is mergeable before it merges.</div>
      <div class="ss-list">${_rows.map(r => rowHTML(r, _canDecide, _armed.has(r.id))).join('') || '<div class="ss-empty">Nothing is waiting.</div>'}</div>`;
    backdrop.appendChild(sheet);
    document.body.appendChild(backdrop);
    sheet.querySelector('.ss-close').onclick = closeApprovals;
    if (!_canDecide) return;
    sheet.querySelectorAll('.ap-row').forEach(row => {
        const id = row.dataset.id;
        const err = row.querySelector('.ap-err');
        const run = async (btn, action) => {
            btn.disabled = true;
            const r = await _decide(id, action);
            _armed.delete(id);
            if (!r.ok && err) { err.textContent = r.error; err.hidden = false; btn.disabled = false; return; }
            await tickApprovals();
        };
        const ap = row.querySelector('.ap-approve');
        if (ap) ap.onclick = () => { _armed.add(id); _buildSheet(); };
        const cf = row.querySelector('.ap-confirm');
        if (cf) cf.onclick = () => run(cf, 'approve');
        const cc = row.querySelector('.ap-cancel');
        if (cc) cc.onclick = () => { _armed.delete(id); _buildSheet(); };
        const dn = row.querySelector('.ap-deny');
        if (dn) dn.onclick = () => run(dn, 'deny');
    });
    document.addEventListener('keydown', _key, true);
}

function _key(e) { if (e.key === 'Escape') closeApprovals(); }

export async function openApprovals() {
    await tickApprovals();
    _buildSheet();
}

export function closeApprovals() {
    const bd = document.getElementById('approvals-backdrop');
    if (bd) bd.remove();
    _armed.clear();
    document.removeEventListener('keydown', _key, true);
}

// Settings → Notifications toggle. Turning it on asks for permission (a user gesture); a
// browser without the API, or a refusal, leaves it effectively off — no error shown.
export async function setApprovalNotify(on) {
    try {
        if (!on) { localStorage.removeItem(NOTIFY_KEY); return false; }
        localStorage.setItem(NOTIFY_KEY, '1');
        const N = window.Notification;
        if (N && N.permission === 'default' && typeof N.requestPermission === 'function') {
            await N.requestPermission();
        }
    } catch (_) { /* unsupported — silently nothing */ }
    return notifyEnabled();
}

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { openApprovals, closeApprovals, setApprovalNotify });
