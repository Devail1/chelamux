// 🔔 Pending approvals (CMX-103) — override (and, with CMX-87, release) requests waiting on
// the operator, on every view.
//
// `chela merge --override` files a request and returns; before this, the only pointer to it
// was the Telegram card or the CLI output of whoever filed it, so a dashboard-only operator
// never learned one was waiting and it expired. This is the pill (#btn-approvals, in
// .safety-float beside the access-request pill) with the count, and a sheet listing each
// request: what, who asked, why, age, time to expiry.
//
// Nothing here decides by itself. Approve and Deny POST to /api/override/<id>/<action>, which
// is the SAME decide path as the /override/<id> confirm page (chela.overrides.decide —
// re-checks head / CI / MERGEABLE / expiry live). Approve is a deliberate second action: the
// first click only ARMS the button ("Confirm merge"), the second sends `confirm: true`, and
// the server refuses an approve without it. A request the server already knows cannot be
// approved (`blocked`: CI red, conflicting, head moved…) shows why and offers only Deny.
//
// The browser notification on a NEW request is opt-in (Settings → Notifications, off by
// default) and silently does nothing where the Notification API is missing or refused.
import { api, attrEsc, escHtml } from './util.js';

export const NOTIFY_KEY = 'chela_approval_notify';
const ARM_MS = 6000;

let _rows = [];
let _seen = null;          // ids already known — null until the first listing primes it
let _loaded = false;       // a listing has succeeded at least once on this page
let _staleLoud = false;    // the server itself reported an error (not just a dead fetch)
let _stale = false;        // the last refresh failed — rows are the last KNOWN list, not "nothing"
let _flash = '';           // the last refused decision's reason — survives the re-render

export function durStr(s) {
    if (s == null) return '';
    const m = Math.max(0, Math.round(s / 60));
    if (m < 1) return '<1m';
    if (m < 60) return m + 'm';
    const h = Math.floor(m / 60);
    return h < 48 ? h + 'h ' + (m % 60) + 'm' : Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
}

// A request's PR url is agent-supplied: it becomes a link only when it is plainly http(s).
// Whitespace and control characters are stripped first (browsers ignore a tab or newline
// inside a scheme, so `java\tscript:` must not slip past a prefix test). Anything else
// renders as plain text, never as an href.
export function safeHref(url) {
    const clean = String(url == null ? '' : url).trim().replace(/[\u0000-\u001f\u007f-\u009f]/g, '');
    return /^https?:\/\//i.test(clean) ? clean : null;
}

// What the request asks, in one line — per record kind.
export function whatText(r) {
    if (r.kind === 'release') return 'Release ' + (r.version || r.label || '?');
    if (r.kind && r.kind !== 'override') return r.question || r.kind;
    const sha = (r.head_sha || '?').slice(0, 12);
    const past = r.judge_state ? 'past the judge’s ' + r.judge_state : 'with no judge verdict';
    return 'Override-merge ' + (r.label || r.task_id || '?') + ' ' + past + ' on ' + sha
        + (r.base ? ' → ' + r.base : '');
}

export function rowHTML(r) {
    const id = attrEsc(r.id);
    if (r.kind === 'unreadable') {
        // A request FILE the server could not use: shown, never actionable.
        return `<div class="pa-row pa-bad" data-id="${id}">
      <div class="pa-blocked">⛔ ${escHtml(r.blocked || 'unreadable request ' + r.id)}</div></div>`;
    }
    const href = r.pr_url ? safeHref(r.pr_url) : null;
    const pr = !r.pr_url ? ''
        : href ? ` · <a href="${attrEsc(href)}" target="_blank" rel="noopener">PR</a>`
            : ` · <span class="pa-prtext">PR: ${escHtml(r.pr_url)}</span>`;
    const head = `<div class="pa-what">${escHtml(whatText(r))}</div>
      <div class="pa-meta">asked by ${escHtml(r.actor || 'unknown')} · ${escHtml(durStr(r.age_s))} ago · expires in ${escHtml(durStr(r.expires_in_s))}${pr}</div>
      ${r.reason ? `<div class="pa-why">“${escHtml(r.reason)}”</div>` : ''}`;
    const approve = r.blocked
        ? `<div class="pa-blocked">⛔ Can't be approved: ${escHtml(r.blocked)}</div>`
        : `<button class="pa-approve" type="button" data-id="${id}">Approve</button>`;
    return `<div class="pa-row" data-id="${id}">${head}
      <div class="pa-ctl">${approve}<button class="pa-deny" type="button" data-id="${id}">Deny</button></div>
      </div>`;
}

function _renderPill() {
    const btn = document.getElementById('btn-approvals');
    if (!btn) return;
    const n = _rows.length;
    // A failed refresh with nothing known must not hide the pill: unknown is not "none".
    // (Quiet only when the endpoint has NEVER answered here — e.g. a page with no approvals
    // backend; once it has answered, or says `error` itself, a failure is shown.)
    const showStale = _stale && (_loaded || _staleLoud);
    btn.hidden = n === 0 && !showStale;
    const txt = btn.querySelector('.pa-pill-text');
    if (txt) {
        txt.textContent = n === 0 && showStale ? '⚠️ couldn’t refresh approvals'
            : '⚖️ ' + n + ' pending approval' + (n === 1 ? '' : 's');
    }
    btn.setAttribute('aria-label', n === 0 && showStale ? 'couldn’t refresh approvals — tap to review'
        : n + ' approval request' + (n === 1 ? '' : 's') + ' waiting — tap to review');
}

export function notifyEnabled() {
    try { return localStorage.getItem(NOTIFY_KEY) === '1'; } catch (_) { return false; }
}

// A browser notification for each request not seen before. The first listing only PRIMES
// the seen-set (a reload must not re-announce what was already waiting). Every failure —
// no API, permission not granted, a throwing constructor — is silent.
function _notifyNew(rows) {
    const fresh = _seen === null ? [] : rows.filter(r => !_seen.has(r.id));
    _seen = new Set(rows.map(r => r.id));
    if (!fresh.length || !notifyEnabled()) return;
    try {
        const N = window.Notification;
        if (!N || N.permission !== 'granted') return;
        fresh.forEach(r => {
            try { new N('chela: approval waiting', { body: whatText(r), tag: 'chela-approval-' + r.id }); }
            catch (_) { /* unsupported in this context (e.g. needs a service worker) */ }
        });
    } catch (_) { /* degrade silently */ }
}

export async function tickApprovals() {
    let data;
    try { data = await api('/api/overrides'); } catch (_) { data = null; }
    if (!data || data.error || !Array.isArray(data.requests)) {
        // A failed or error reply keeps the last KNOWN rows and says so — it is never
        // "Nothing is waiting for approval".
        _stale = true;
        _staleLoud = !!(data && data.error);
        _renderPill();
        if (document.getElementById('approvals-backdrop')) _buildSheet();
        return;
    }
    _stale = false;
    _staleLoud = false;
    _loaded = true;
    _rows = data.requests;
    _notifyNew(_rows);
    _renderPill();
    if (document.getElementById('approvals-backdrop')) _buildSheet();
}

async function _decide(id, action) {
    let resp;
    try {
        resp = await api('/api/override/' + encodeURIComponent(id) + '/' + action, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(action === 'approve' ? { confirm: true } : {}),
        });
    } catch (_) { resp = null; }
    if (resp && resp.ok) return { ok: true };
    return { ok: false, error: (resp && (resp.detail || resp.error || resp.state)) || 'Could not ' + action + ' the request' };
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
    sheet.innerHTML = `<div class="ss-hd">⚖️ Pending approvals
        <button class="ss-close" type="button" aria-label="Close">&times;</button></div>
      <div class="ss-sub">Approving merges NOW — after re-checking that the head is unchanged, CI is green and GitHub reports the PR mergeable. It skips only the judge, and is recorded. Deny merges nothing.</div>
      <div class="pa-err ss-err"${_flash ? '' : ' hidden'}>${escHtml(_flash)}</div>
      <div class="pa-stale ss-err"${_stale ? '' : ' hidden'}>Couldn’t refresh approvals — showing the last known list.</div>
      <div class="ss-list">${_rows.map(rowHTML).join('') || `<div class="ss-empty">${_stale ? 'The approvals list is unavailable.' : 'Nothing is waiting for approval.'}</div>`}</div>`;
    backdrop.appendChild(sheet);
    document.body.appendChild(backdrop);
    sheet.querySelector('.ss-close').onclick = closeApprovals;
    sheet.querySelectorAll('.pa-row').forEach(row => {
        const id = row.dataset.id;
        const run = async (action, btn) => {
            btn.disabled = true;
            _flash = '';
            const r = await _decide(id, action);
            // The re-render below rebuilds the sheet (and a refused request has usually
            // left the list) — so the reason rides on the sheet, not on the old row.
            if (!r.ok) _flash = r.error;
            await tickApprovals();
        };
        const ap = row.querySelector('.pa-approve');
        if (ap) ap.onclick = () => {
            // First click arms; only a second click within ARM_MS approves.
            if (!ap.classList.contains('pa-armed')) {
                ap.classList.add('pa-armed');
                ap.textContent = 'Confirm merge';
                setTimeout(() => {
                    if (ap.isConnected && !ap.disabled) { ap.classList.remove('pa-armed'); ap.textContent = 'Approve'; }
                }, ARM_MS);
                return;
            }
            run('approve', ap);
        };
        const dn = row.querySelector('.pa-deny');
        if (dn) dn.onclick = () => run('deny', dn);
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
    _flash = '';
    document.removeEventListener('keydown', _key, true);
}

// Settings → Notifications. Turning it on asks the browser for permission; where the API
// is missing or the answer is not "granted", it stays off — silently.
export async function setApprovalNotify(v) {
    const off = () => { try { localStorage.removeItem(NOTIFY_KEY); } catch (_) { /* ignore */ } };
    if (v !== 'on') { off(); return false; }
    try {
        const N = window.Notification;
        if (!N) { off(); return false; }
        const perm = N.permission === 'granted' ? 'granted' : await N.requestPermission();
        if (perm !== 'granted') { off(); return false; }
        localStorage.setItem(NOTIFY_KEY, '1');
        return true;
    } catch (_) { off(); return false; }
}

// --- Stage 0: window.chela — surface reachable from inline HTML handlers ---
window.chela = window.chela || {};
Object.assign(window.chela, { openApprovals, closeApprovals, setApprovalNotify });
