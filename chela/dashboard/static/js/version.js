// CMX-426: "chela was updated — Reload".
//
// A deploy restarts chela-dashboard, but an open page keeps running the JS/CSS it
// loaded. Static assets are served under a versioned path (app.py ASSET_VERSION), so a
// RELOAD picks up the new build; this module tells an open page that one exists. The
// page's own version is the one index.html was rendered with (window.CHELA_ASSET_VERSION);
// the server's comes from /api/version on the existing refresh() poll.
//
// Never reloads by itself: a share or a half-typed draft may be in progress. It offers.
import { ASSET_VERSION, api } from './util.js';

const PAGE_VERSION = ASSET_VERSION;

// Offer a reload only when BOTH versions are known and they differ. An unknown side (an
// old template with no version, a failed fetch) never offers: a banner nobody can act on
// correctly is noise, and a match must stay silent so a stable deploy has no reload storm.
function reloadOffered(pageVersion, serverVersion) {
    if (!pageVersion || !serverVersion) return false;
    return pageVersion !== serverVersion;
}

let _dismissed = '';   // the server version the user closed the banner for

function _banner(doc) {
    let el = doc.getElementById('update-banner');
    if (el) return el;
    el = doc.createElement('div');
    el.id = 'update-banner';
    el.className = 'update-banner';
    el.setAttribute('role', 'status');
    el.hidden = true;
    const msg = doc.createElement('span');
    msg.textContent = 'chela was updated';
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
    close.addEventListener('click', () => { _dismissed = el.dataset.version || ''; el.hidden = true; });
    el.append(msg, reload, close);
    doc.body.appendChild(el);
    return el;
}

// Show or hide the banner for this server version. Returns whether it is shown.
function applyVersion(serverVersion, pageVersion = PAGE_VERSION, doc = document) {
    const show = reloadOffered(pageVersion, serverVersion) && serverVersion !== _dismissed;
    const existing = doc.getElementById('update-banner');
    if (!show && !existing) return false;
    const el = _banner(doc);
    el.dataset.version = serverVersion || '';
    el.hidden = !show;
    return show;
}

async function checkForUpdate() {
    let v = '';
    try { v = (await api('/api/version') || {}).version || ''; } catch (_) { return false; }
    return applyVersion(v);
}

export { PAGE_VERSION, applyVersion, checkForUpdate, reloadOffered };
