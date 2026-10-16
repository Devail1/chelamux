// CMX-426: "chela was updated — Reload".
//
// A deploy restarts chela-dashboard, but an open page keeps running the JS/CSS it
// loaded. Static assets are served under a versioned path (app.py ASSET_VERSION), so a
// RELOAD picks up the new build; this module tells an open page that one exists. The
// page's own version is the one index.html was rendered with (window.CHELA_ASSET_VERSION);
// the server's comes from /api/version on the existing refresh() poll.
//
// Never reloads by itself: a share or a half-typed draft may be in progress. It offers.
//
// CMX-129: the same banner carries "N services run old code — Restart" (a control only in
// Settings is how merged code sat unrestarted for a day). One banner, never two stacked, each
// fact with its own button. Restart POSTs the one existing route (/api/update/apply).
import { ASSET_VERSION, api } from './util.js';
import { followUpdate, staleOffer } from './updateflow.js';

const PAGE_VERSION = ASSET_VERSION;

// Offer a reload only when BOTH versions are known and they differ. An unknown side (an
// old template with no version, a failed fetch) never offers: a banner nobody can act on
// correctly is noise, and a match must stay silent so a stable deploy has no reload storm.
function reloadOffered(pageVersion, serverVersion) {
    if (!pageVersion || !serverVersion) return false;
    return pageVersion !== serverVersion;
}

let _dismissed = '';       // the server version the user closed the banner for
let _dismissedStale = '';  // the stale set (names) the user closed the banner for
let _reload = '';          // server version a reload is offered for, '' when none
let _stale = null;         // staleOffer() of the last status read, null when none/unknown
let _progress = null;      // {text, cls, done} while a restart is followed, then its outcome

function _banner(doc) {
    let el = doc.getElementById('update-banner');
    if (el) return el;
    el = doc.createElement('div');
    el.id = 'update-banner';
    el.className = 'update-banner';
    el.setAttribute('role', 'status');
    el.hidden = true;
    const msg = doc.createElement('span');
    msg.id = 'update-banner-msg';
    msg.textContent = 'chela was updated';
    const restart = doc.createElement('button');
    restart.className = 'btn-accent';
    restart.id = 'update-banner-restart';
    restart.textContent = 'Restart';
    restart.hidden = true;
    restart.addEventListener('click', () => { restartStale(doc); });
    const reload = doc.createElement('button');
    reload.className = 'btn-accent';
    reload.id = 'update-banner-reload';
    reload.textContent = 'Reload';
    reload.addEventListener('click', () => window.location.reload());
    const close = doc.createElement('button');
    close.className = 'icon-btn';
    close.id = 'update-banner-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    close.addEventListener('click', () => {
        _dismissed = el.dataset.version || '';
        _dismissedStale = _stale ? _stale.key : '';
        if (_progress && _progress.done) _progress = null;
        _render(doc);
    });
    el.append(msg, restart, reload, close);
    doc.body.appendChild(el);
    return el;
}

// Draw the banner from the current facts. Returns whether it is shown.
function _render(doc) {
    const showReload = !!_reload && _reload !== _dismissed;
    const busy = !!_progress && !_progress.done;
    const showStale = !!_stale && _stale.key !== _dismissedStale && !busy;
    const show = showReload || showStale || !!_progress;
    const existing = doc.getElementById('update-banner');
    if (!show && !existing) return false;
    const el = _banner(doc);
    const parts = [];
    if (showReload) parts.push('chela was updated');
    if (_progress) parts.push(_progress.text);
    else if (showStale) parts.push(_stale.text);
    const msg = doc.getElementById('update-banner-msg');
    msg.textContent = parts.join(' · ');
    msg.className = _progress && _progress.cls ? 's-savemsg ' + _progress.cls : '';
    const restart = doc.getElementById('update-banner-restart');
    restart.hidden = !showStale;
    if (showStale) restart.textContent = _stale.label;
    doc.getElementById('update-banner-reload').hidden = !showReload;
    el.hidden = !show;
    return show;
}

// Show or hide the reload half for this server version. Returns whether it is shown.
function applyVersion(serverVersion, pageVersion = PAGE_VERSION, doc = document) {
    const offered = reloadOffered(pageVersion, serverVersion);
    _reload = offered ? serverVersion : '';
    _render(doc);
    const el = doc.getElementById('update-banner');
    if (el) el.dataset.version = serverVersion || '';
    return offered && serverVersion !== _dismissed;
}

// The stale half for an /api/update/status payload; unknown (see staleOffer) offers nothing.
function applyStale(status, doc = document) {
    _stale = staleOffer(status);
    _render(doc);
    return !!_stale && _stale.key !== _dismissedStale && !(_progress && !_progress.done);
}

async function restartStale(doc = document, deps = {}) {
    const offer = _stale;
    if (!offer || (_progress && !_progress.done)) return null;
    if (!confirm(`${offer.label}: ${offer.names.join(', ')}? Running agents and judges keep `
        + 'running; the daemon restarts between ticks.')) return null;
    _progress = { text: 'Starting…', cls: '' };
    _render(doc);
    let resp;
    try {
        resp = await api('/api/update/apply', { method: 'POST' });
    } catch (e) {
        resp = { ok: false, error: 'request failed, the dashboard may be restarting' };
    }
    if (!resp || resp.ok === false || resp.error) {
        _progress = { text: (resp && resp.error) || 'Restart refused.', cls: 'err', done: true };
        _render(doc);
        return _progress;
    }
    if (!resp.started) {
        _progress = { text: resp.detail || 'Nothing to restart.', cls: 'ok', done: true };
        _render(doc);
        return _progress;
    }
    const step = await followUpdate(resp, s => {
        _progress = { text: s.text, cls: '' };
        _render(doc);
    }, deps);
    _progress = { text: step.text, cls: step.state === 'done' ? 'ok' : 'err', done: true };
    if (step.state === 'done') _stale = null;   // the next status read re-decides
    _render(doc);
    return _progress;
}

async function checkForUpdate() {
    // Independent reads: a failed one must not hide what the other found.
    checkStale().catch(() => {});
    let v = '';
    try { v = (await api('/api/version') || {}).version || ''; } catch (_) { return false; }
    return applyVersion(v);
}

async function checkStale(doc = document) {
    let st = null;
    try { st = await api('/api/update/status'); } catch (_) { st = null; }
    return applyStale(st, doc);
}

export { PAGE_VERSION, applyStale, applyVersion, checkForUpdate, checkStale, reloadOffered, restartStale };
