// CMX-412: the in-pane file drop/paste shim (chela/dashboard/static/term-upload.js), run
// for real inside a jsdom page at /term/@1/ — the same path ttyd's page is served under.
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - a dropped file is POSTed to /api/term/upload with this pane's window id, the drop is
//     claimed (preventDefault), and the saved name is toasted;
//   - a refusal shows an ERROR toast carrying the server's reason;
//   - a pasted image is uploaded, a TEXT paste falls through untouched;
//   - with the Settings switch off (window.__CHELA_FILE_DROP__ = false) nothing is claimed.
//
// Run: node --test tests/term_upload.test.mjs (pytest runs it via tests/test_js_suites.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const SHIM = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..',
    'chela', 'dashboard', 'static', 'term-upload.js'), 'utf8');
const flush = () => new Promise(r => setTimeout(r, 0));

function page({ enabled = true, respond } = {}) {
    const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>',
        { url: 'http://localhost:5005/term/%401/', runScripts: 'outside-only' });
    const w = dom.window;
    const calls = [];
    w.fetch = async (url, opts) => {
        calls.push({ url: String(url), opts });
        const { status, body } = respond ? respond(opts) : {
            status: 200,
            body: { ok: true, name: opts.body.get('file').name, path: 'uploads/' + opts.body.get('file').name, typed: true },
        };
        return { ok: status < 400, status, json: async () => body };
    };
    w.__CHELA_FILE_DROP__ = enabled;
    w.eval(SHIM);
    return { w, calls };
}

function fire(w, type, prop, dt) {
    const ev = new w.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, prop, { value: dt });
    w.document.body.dispatchEvent(ev);
    return ev;
}

const toasts = w => [...w.document.querySelectorAll('.chela-upload-toast')]
    .map(t => ({ kind: t.dataset.kind, text: t.textContent }));

test('a dropped file is uploaded with the pane window id and its saved name toasted', async () => {
    const { w, calls } = page();
    const file = new w.File(['hello'], 'notes.txt', { type: 'text/plain' });
    const ev = fire(w, 'drop', 'dataTransfer', { files: [file], types: ['Files'] });
    await flush(); await flush();
    assert.equal(ev.defaultPrevented, true, 'the drop must be claimed, not navigate the frame');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/term/upload');
    assert.equal(calls[0].opts.method, 'POST');
    assert.equal(calls[0].opts.body.get('agent'), '@1');
    assert.equal(calls[0].opts.body.get('file').name, 'notes.txt');
    assert.deepEqual(toasts(w), [{ kind: 'ok', text: 'Saved uploads/notes.txt' }]);
});

test('a refused upload shows an error toast with the server reason', async () => {
    const { w } = page({ respond: () => ({ status: 403, body: { ok: false, error: 'Share guests cannot upload files.' } }) });
    fire(w, 'drop', 'dataTransfer', { files: [new w.File(['x'], 'a.txt')], types: ['Files'] });
    await flush(); await flush();
    assert.deepEqual(toasts(w), [{ kind: 'err', text: 'Not uploaded: Share guests cannot upload files.' }]);
});

test('a pasted image is uploaded; a text paste falls through untouched', async () => {
    const { w, calls } = page();
    const img = new w.File(['png'], 'image.png', { type: 'image/png' });
    const ev = fire(w, 'paste', 'clipboardData',
        { files: [], items: [{ kind: 'file', type: 'image/png', getAsFile: () => img }] });
    await flush(); await flush();
    assert.equal(ev.defaultPrevented, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].opts.body.get('file').name, 'image.png');

    const txt = fire(w, 'paste', 'clipboardData',
        { files: [], items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }] });
    await flush();
    assert.equal(txt.defaultPrevented, false, 'a text paste must reach xterm.js');
    assert.equal(calls.length, 1, 'a text paste must not upload anything');
});

test('the Ctrl+V key shim hook names a nameless clipboard blob', async () => {
    const { w, calls } = page();
    assert.equal(typeof w.__chelaUpload, 'function');
    await w.__chelaUpload(new w.Blob(['png'], { type: 'image/png' }));
    assert.match(calls[0].opts.body.get('file').name, /^paste-\d{8}-\d{6}\.png$/);
});

test('with the Settings switch off nothing is claimed', async () => {
    const { w, calls } = page({ enabled: false });
    const ev = fire(w, 'drop', 'dataTransfer', { files: [new w.File(['x'], 'a.txt')], types: ['Files'] });
    await flush();
    assert.equal(ev.defaultPrevented, false);
    assert.equal(calls.length, 0);
    assert.equal(w.__chelaUpload, undefined);
});
