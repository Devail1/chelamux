// 📎 File drop / paste into a Wall terminal (CMX-412) — injected into ttyd's page by
// term_http, right after `window.__CHELA_FILE_DROP__` (the Settings switch at serve time).
//
// The browser cannot give a pane a host path: the file is on the viewer's device. So a
// dropped or pasted NON-image file is POSTed to /api/term/upload with this pane's window
// id; the server saves it to <session cwd>/uploads/<name> and types `@uploads/<name> ` into
// the prompt itself (no Enter). This page only shows the toast.
//
// An IMAGE (png/jpeg/webp/gif) takes the pre-CMX-412 image path instead (CMX-423): POST
// /api/term/paste-image (saved under /tmp/chela-paste-images/), then type the returned path
// via /api/term/paste. Claude Code turns that path into a real attachment ("[Image #N]"),
// which is how it actually SEES the image; an `@uploads/x.png` mention is just a file ref.
// A drop/paste of several files sends each to its path, in the original order.
//
// Registered in the CAPTURE phase and injected before the legacy paste shims, so a file
// paste is claimed here first; text pastes fall straight through, untouched. With the
// switch off nothing is registered and the legacy image-paste path is unchanged.
(function () {
    'use strict';
    var m = location.pathname.match(/\/term\/([^\/]+)/);
    if (!m || !window.__CHELA_FILE_DROP__) return;
    var wid = decodeURIComponent(m[1]);
    var EXT = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                'image/gif': '.gif', 'text/plain': '.txt', 'application/pdf': '.pdf' };

    function toast(kind, text) {
        var el = document.createElement('div');
        el.className = 'chela-upload-toast';
        el.setAttribute('role', kind === 'err' ? 'alert' : 'status');
        el.dataset.kind = kind;
        el.textContent = text;
        el.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;'
            + 'max-width:80vw;padding:6px 10px;border-radius:6px;font:13px/1.35 system-ui,sans-serif;'
            + 'color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.35);pointer-events:none;'
            + 'background:' + (kind === 'err' ? '#9b1c1c' : '#1f4f82');
        document.body.appendChild(el);
        setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, kind === 'err' ? 5000 : 3000);
        return el;
    }

    function nameFor(blob) {
        if (blob && blob.name) return blob.name;
        var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; };
        return 'paste-' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-'
            + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds())
            + (EXT[(blob && blob.type) || ''] || '.bin');
    }

    async function upload(blob) {
        var name = nameFor(blob);
        var fd = new FormData();
        fd.append('agent', wid);
        fd.append('file', blob, name);
        var r, j = null;
        try {
            r = await fetch('/api/term/upload', { method: 'POST', body: fd, credentials: 'same-origin' });
            try { j = await r.json(); } catch (e) { j = null; }
        } catch (e) {
            toast('err', 'Upload failed — ' + name + ' was not saved');
            return null;
        }
        if (!r.ok || !j || !j.ok) {
            toast('err', 'Not uploaded: ' + ((j && j.error) || ('HTTP ' + r.status)));
            return null;
        }
        toast('ok', 'Saved ' + j.path + (j.typed === false ? ' (type its @path yourself)' : ''));
        return j;
    }

    // The legacy image endpoint's MIME allowlist (_PASTE_IMAGE_MIME_EXT): anything else —
    // svg, heic, a pdf — goes to uploads/, which takes any type.
    var IMAGE = { 'image/png': 1, 'image/jpeg': 1, 'image/webp': 1, 'image/gif': 1 };

    function isImage(blob) {
        return !!(blob && IMAGE[(blob.type || '').toLowerCase()]);
    }

    // Byte-for-byte the pre-CMX-412 sequence (the legacy paste shims make the same two calls).
    async function pasteImage(blob) {
        try {
            var fd = new FormData();
            fd.append('agent', wid);
            fd.append('image', blob, blob.name || 'paste');
            var r = await fetch('/api/term/paste-image', { method: 'POST', body: fd, credentials: 'same-origin' });
            if (!r.ok) {
                var j0 = null;
                try { j0 = await r.json(); } catch (e) { j0 = null; }
                toast('err', 'Image not pasted: ' + ((j0 && j0.error) || ('HTTP ' + r.status)));
                return null;
            }
            var j = await r.json();
            if (!j || !j.path) return null;
            await fetch('/api/term/paste', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin', body: JSON.stringify({ agent: wid, text: j.path }),
            });
            return j;
        } catch (err) {
            toast('err', 'Image paste failed');
            return null;
        }
    }

    async function sendAll(files) {
        for (var i = 0; i < files.length; i++) {
            if (isImage(files[i])) await pasteImage(files[i]);
            else await upload(files[i]);
        }
    }

    function filesOf(dt) {
        var out = [];
        if (!dt) return out;
        if (dt.files && dt.files.length) {
            for (var i = 0; i < dt.files.length; i++) out.push(dt.files[i]);
            return out;
        }
        var items = dt.items || [];
        for (var k = 0; k < items.length; k++) {
            if (items[k].kind === 'file') { var f = items[k].getAsFile(); if (f) out.push(f); }
        }
        return out;
    }

    function hasFiles(dt) {
        var t = (dt && dt.types) || [];
        for (var i = 0; i < t.length; i++) if (t[i] === 'Files') return true;
        return false;
    }

    document.addEventListener('dragover', function (e) {
        if (!hasFiles(e.dataTransfer)) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    }, true);

    document.addEventListener('drop', function (e) {
        var files = filesOf(e.dataTransfer);
        if (!files.length) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        sendAll(files);
    }, true);

    document.addEventListener('paste', function (e) {
        var files = filesOf(e.clipboardData);
        if (!files.length) return;          // text paste → xterm.js / the legacy shims
        e.preventDefault();
        e.stopImmediatePropagation();
        sendAll(files);
    }, true);

})();
