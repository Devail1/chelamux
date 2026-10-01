// CMX-412 harness: loads the page `/term/<wid>/` ACTUALLY serves (written to a file by
// tests/test_term_upload_served.py — Flask test client, upstream ttyd faked) into jsdom with
// every injected <script> running for real, then fires ONE user action and prints which
// dashboard routes the page called, in order, as JSON.
//
// The external `<script src="/static/v/<ver>/term-upload.js">` tag (CMX-426: versioned) is
// swapped for the file's source at the SAME position (jsdom cannot fetch it). If term_http
// stops serving the tag, nothing is swapped and the upload shim does not run — exactly as in
// a browser.
//
// Usage: node term_upload_harness.mjs <servedHtmlPath> <action>
//   action: keyV-image | keyV-text | paste-image | paste-text | paste-pdf | drop-file | drop-mixed
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM, VirtualConsole } from 'jsdom';

const [, , htmlPath, action] = process.argv;
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const UPLOAD_JS = readFileSync(join(ROOT, 'chela', 'dashboard', 'static', 'term-upload.js'), 'utf8');
const TAG = /<script src="\/static\/(?:v\/[^/"]+\/)?term-upload\.js"><\/script>/;
let html = readFileSync(htmlPath, 'utf8');
html = html.replace(TAG, () => '<script>' + UPLOAD_JS + '</script>');

const calls = [];
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push(String(e && e.message || e)));

const dom = new JSDOM(html, {
    url: 'http://localhost:5005/term/%401/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(w) {
        w.fetch = async (url, opts = {}) => {
            const path = new URL(String(url), w.location.href).pathname;
            const c = { path };
            const b = opts.body;
            if (b && typeof b.get === 'function') {
                c.agent = b.get('agent');
                const f = b.get('file') || b.get('image');
                c.field = b.get('file') ? 'file' : (b.get('image') ? 'image' : null);
                c.name = f && f.name;
            } else if (typeof b === 'string') {
                try { c.json = JSON.parse(b); } catch (e) { c.json = b; }
            }
            calls.push(c);
            let body = {};
            if (path === '/api/term/upload') body = { ok: true, name: c.name, path: 'uploads/' + c.name, typed: true };
            else if (path === '/api/term/paste-image') body = { path: '/tmp/chela-paste/x.png' };
            return { ok: true, status: 200, json: async () => body };
        };
        const img = new w.Blob(['png'], { type: 'image/png' });
        const clip = action === 'keyV-image'
            ? [{ types: ['image/png'], getType: async () => img }]
            : [{ types: ['text/plain'], getType: async () => new w.Blob(['hello'], { type: 'text/plain' }) }];
        Object.defineProperty(w.navigator, 'clipboard', {
            configurable: true,
            value: { read: async () => clip, readText: async () => 'hello' },
        });
    },
});
const w = dom.window;
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 0)); };

function fire(type, prop, dt) {
    const ev = new w.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, prop, { value: dt });
    w.document.body.dispatchEvent(ev);
    return ev;
}

let prevented = null;
const file = new w.File(['png'], 'shot.png', { type: 'image/png' });
if (action === 'keyV-image' || action === 'keyV-text') {
    const ev = new w.KeyboardEvent('keydown', { key: 'v', ctrlKey: true, bubbles: true, cancelable: true });
    w.document.body.dispatchEvent(ev);
    prevented = ev.defaultPrevented;
} else if (action === 'paste-image') {
    prevented = fire('paste', 'clipboardData',
        { files: [file], items: [{ kind: 'file', type: 'image/png', getAsFile: () => file }] }).defaultPrevented;
} else if (action === 'paste-pdf') {
    const pdf = new w.File(['%PDF'], 'doc.pdf', { type: 'application/pdf' });
    prevented = fire('paste', 'clipboardData',
        { files: [pdf], items: [{ kind: 'file', type: 'application/pdf', getAsFile: () => pdf }] }).defaultPrevented;
} else if (action === 'paste-text') {
    prevented = fire('paste', 'clipboardData',
        { files: [], items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }] }).defaultPrevented;
} else if (action === 'drop-file') {
    const over = fire('dragover', 'dataTransfer', { types: ['Files'], dropEffect: 'none' });
    const drop = fire('drop', 'dataTransfer', { files: [file], types: ['Files'] });
    prevented = { dragover: over.defaultPrevented, drop: drop.defaultPrevented };
} else if (action === 'drop-mixed') {
    const pdf = new w.File(['%PDF'], 'doc.pdf', { type: 'application/pdf' });
    const over = fire('dragover', 'dataTransfer', { types: ['Files'], dropEffect: 'none' });
    const drop = fire('drop', 'dataTransfer', { files: [file, pdf], types: ['Files'] });
    prevented = { dragover: over.defaultPrevented, drop: drop.defaultPrevented };
} else {
    throw new Error('unknown action ' + action);
}
await settle();
const toasts = [...w.document.querySelectorAll('.chela-upload-toast')].map(t => t.textContent);
process.stdout.write(JSON.stringify({ calls, prevented, toasts, errors }) + '\n');
process.exit(0);
