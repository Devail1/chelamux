// 🔌 Self-reconnecting terminal (CMX-167) — injected into ttyd's page by term_http, FIRST in
// <head>, so it runs before ttyd's own (body) script creates its WebSocket.
//
// ttyd 1.7.7 reconnects by itself only on a close with no prior `error` event; iOS killing a
// backgrounded tab's socket, or a wifi/4G switch, fires `error` first, so ttyd stops at
// "Press ⏎ to Reconnect" and waits for Enter. This shim watches the page's ttyd socket (the
// one opened with the "tty" subprotocol) and, once it is down, brings the terminal back:
//   - visible: after a backoff of 0.5 s, 1 s, 2 s, 5 s, then every 10 s;
//   - hidden: nothing until `visibilitychange` → visible, `pageshow` or `online`.
//
// HOW it reconnects: probe ttyd's `token` URL, then reload this document. Reloading is
// chosen over driving ttyd's connect path because that path is private to ttyd's bundle:
// the only handle on it is a synthetic Enter keypress, and an Enter that ever arrives once
// the socket is back up is typed into the agent's prompt. A reload uses no ttyd internals,
// and every new page attaches a tmux client exactly as the first load did (ignore-size
// included). The probe comes first so a reload never lands on an error page while the
// server is down: that page would carry no shim and stop retrying.
//
// While a reconnect is pending ttyd's overlay is hidden; after repeated failures a quiet
// status line shows instead, and Enter retries at once. A frame chela blanked on purpose
// (`data-suspended-src`, see _teardownTermFrames) is never reconnected: chela restores it.
(function () {
    'use strict';
    var NativeWS = window.WebSocket;
    if (!NativeWS || window.__chelaReconnect) return;
    var BACKOFF_MS = [500, 1000, 2000, 5000, 10000];
    var QUIET_AFTER = 2;           // failed attempts before the status line shows
    var CLS = 'chela-reconnecting';
    var R = window.__chelaReconnect = { reload: function () { location.reload(); } };
    var latest = null;             // the page's newest ttyd socket
    var timer = null;              // a scheduled attempt
    var inflight = false;          // an attempt is probing or reloading
    var failures = 0;
    var leaving = false;           // pagehide: this document is going away
    var status = null;

    var css = document.createElement('style');
    // ttyd's overlay is the one classless <div> it appends to the .xterm element.
    css.textContent = 'html.' + CLS + ' .xterm > div:not([class]){display:none!important}';
    (document.head || document.documentElement).appendChild(css);

    function suspended() {
        try {
            var f = window.frameElement;
            return !!f && (f.hasAttribute('data-suspended-src')
                || f.getAttribute('src') === 'about:blank');
        } catch (e) { return false; }   // cross-origin parent: chela is not managing this frame
    }
    function blocked() { return leaving || suspended(); }
    function down() { return !!latest && latest.readyState >= 2; }   // CLOSING or CLOSED
    function hidden() { return document.visibilityState === 'hidden'; }
    function delay() { return BACKOFF_MS[Math.min(failures, BACKOFF_MS.length - 1)]; }

    function showStatus() {
        if (status || !document.body) return;
        status = document.createElement('div');
        status.className = 'chela-reconnect-status';
        status.setAttribute('role', 'status');
        status.textContent = 'Reconnecting… press Enter to retry now';
        status.style.cssText = 'position:fixed;left:50%;top:8px;transform:translateX(-50%);'
            + 'z-index:2147483647;padding:3px 10px;border-radius:10px;pointer-events:none;'
            + 'font:12px/1.4 system-ui,sans-serif;color:#ddd;background:rgba(40,40,40,.85)';
        document.body.appendChild(status);
    }
    function clearPending() {
        failures = 0;
        inflight = false;
        if (timer) { clearTimeout(timer); timer = null; }
        document.documentElement.classList.remove(CLS);
        if (status && status.parentNode) status.parentNode.removeChild(status);
        status = null;
    }

    function schedule(ms) {
        if (timer || inflight || blocked() || hidden()) return;
        timer = setTimeout(attempt, ms);
    }
    function attempt() {
        timer = null;
        if (inflight || blocked() || hidden() || !down()) return;   // !down: ttyd is reconnecting itself
        inflight = true;
        var url = location.pathname.replace(/\/+$/, '') + '/token';
        fetch(url, { cache: 'no-store', credentials: 'same-origin' }).then(function (r) {
            if (!r.ok) throw new Error('probe ' + r.status);
            if (blocked() || !down()) { inflight = false; return; }
            R.reload();   // stays in flight: this document is about to be replaced
        }).catch(function () {
            inflight = false;
            failures++;
            if (failures >= QUIET_AFTER) showStatus();
            schedule(delay());
        });
    }
    // Retry now (page came back, network came back, or the user pressed Enter).
    function kick() {
        if (!down() || inflight || blocked()) return;
        if (timer) { clearTimeout(timer); timer = null; }
        schedule(0);
    }

    function track(ws) {
        latest = ws;
        ws.addEventListener('open', function () { if (ws === latest) clearPending(); });
        ws.addEventListener('close', function () {
            if (ws !== latest || blocked()) return;
            document.documentElement.classList.add(CLS);
            schedule(delay());
        });
    }

    function ChelaWebSocket(url, protocols) {
        var ws = protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols);
        var p = [].concat(protocols === undefined ? [] : protocols);
        if (p.indexOf('tty') !== -1) track(ws);
        return ws;
    }
    ChelaWebSocket.prototype = NativeWS.prototype;
    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) { ChelaWebSocket[k] = NativeWS[k]; });
    window.WebSocket = ChelaWebSocket;

    document.addEventListener('visibilitychange', function () { if (!hidden()) kick(); });
    window.addEventListener('online', kick);
    window.addEventListener('pagehide', function () { leaving = true; });
    window.addEventListener('pageshow', function () { leaving = false; kick(); });
    window.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && down()) { failures = 0; kick(); }
    }, true);
})();
