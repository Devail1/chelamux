// CMX-412: the in-pane file drop/paste shim (chela/dashboard/static/term-upload.js), run
// for real inside a jsdom page at /term/@1/ — the same path ttyd's page is served under.
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - a dropped file is POSTed to /api/term/upload with this pane's window id, the drop is
//     claimed (preventDefault), and the saved name is toasted;
//   - a refusal shows an ERROR toast carrying the server's reason;
//   - a TEXT paste falls through untouched;
//   - CMX-423: a pasted/dropped IMAGE takes the pre-CMX-412 image path (/api/term/paste-image,
//     then its path typed via /api/term/paste), never uploads/; a mixed drop sends each file
//     to its own path in order; a refusal on the image path shows an error toast;
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
        const { status, body } = respond ? respond(opts, String(url)) : String(url) === '/api/term/upload' ? {
            status: 200,
            body: { ok: true, name: opts.body.get('file').name, path: 'uploads/' + opts.body.get('file').name, typed: true },
        } : String(url) === '/api/term/paste-image' ? {
            status: 200, body: { path: '/tmp/chela-paste-images/abc.png' },
        } : { status: 200, body: { pasted: 1 } };
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

test('a saved-but-not-typed upload tells the user to type the @path', async () => {
    const { w } = page({ respond: () => ({ status: 200,
        body: { ok: true, name: 'a.txt', path: 'uploads/a.txt', typed: false } }) });
    fire(w, 'drop', 'dataTransfer', { files: [new w.File(['x'], 'a.txt')], types: ['Files'] });
    await flush(); await flush();
    assert.deepEqual(toasts(w), [{ kind: 'ok', text: 'Saved uploads/a.txt (type its @path yourself)' }]);
});

test('a network failure shows an error toast naming the file', async () => {
    const { w } = page();
    w.fetch = async () => { throw new Error('offline'); };
    fire(w, 'drop', 'dataTransfer', { files: [new w.File(['x'], 'a.txt')], types: ['Files'] });
    await flush(); await flush();
    assert.deepEqual(toasts(w), [{ kind: 'err', text: 'Upload failed — a.txt was not saved' }]);
});

test('every dropped file is uploaded, in order', async () => {
    const { w, calls } = page();
    fire(w, 'drop', 'dataTransfer', { files: [new w.File(['1'], 'one.txt'), new w.File(['2'], 'two.txt')], types: ['Files'] });
    for (let i = 0; i < 6; i++) await flush();
    assert.deepEqual(calls.map(c => c.opts.body.get('file').name), ['one.txt', 'two.txt']);
});

test('a refused upload shows an error toast with the server reason', async () => {
    const { w } = page({ respond: () => ({ status: 403, body: { ok: false, error: 'Share guests cannot upload files.' } }) });
    fire(w, 'drop', 'dataTransfer', { files: [new w.File(['x'], 'a.txt')], types: ['Files'] });
    await flush(); await flush();
    assert.deepEqual(toasts(w), [{ kind: 'err', text: 'Not uploaded: Share guests cannot upload files.' }]);
});

test('a pasted image takes the old image path (typed /tmp path), never uploads/', async () => {
    const { w, calls } = page();
    const img = new w.File(['png'], 'image.png', { type: 'image/png' });
    const ev = fire(w, 'paste', 'clipboardData',
        { files: [], items: [{ kind: 'file', type: 'image/png', getAsFile: () => img }] });
    for (let i = 0; i < 6; i++) await flush();
    assert.equal(ev.defaultPrevented, true);
    assert.deepEqual(calls.map(c => c.url), ['/api/term/paste-image', '/api/term/paste']);
    assert.equal(calls[0].opts.body.get('agent'), '@1');
    assert.equal(calls[0].opts.body.get('image').name, 'image.png');
    assert.equal(calls[0].opts.body.get('file'), null);
    assert.deepEqual(JSON.parse(calls[1].opts.body), { agent: '@1', text: '/tmp/chela-paste-images/abc.png' });
    assert.deepEqual(toasts(w), [], 'the image path is silent on success, as before CMX-412');
});

test('a text paste falls through untouched', async () => {
    const { w, calls } = page();
    const txt = fire(w, 'paste', 'clipboardData',
        { files: [], items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }] });
    await flush();
    assert.equal(txt.defaultPrevented, false, 'a text paste must reach xterm.js');
    assert.equal(calls.length, 0, 'a text paste must not upload anything');
});

test('a pasted nameless PDF goes to uploads/ under a generated name', async () => {
    const { w, calls } = page();
    const pdf = new w.Blob(['%PDF'], { type: 'application/pdf' });
    fire(w, 'paste', 'clipboardData',
        { files: [], items: [{ kind: 'file', type: 'application/pdf', getAsFile: () => pdf }] });
    for (let i = 0; i < 4; i++) await flush();
    assert.deepEqual(calls.map(c => c.url), ['/api/term/upload']);
    assert.match(calls[0].opts.body.get('file').name, /^paste-\d{8}-\d{6}\.pdf$/);
});

test('⭐ a mixed drop (png then pdf, and back) sends each to its own path, in order', async () => {
    const { w, calls } = page();
    const files = [new w.File(['p'], 'a.png', { type: 'image/png' }),
        new w.File(['d'], 'b.pdf', { type: 'application/pdf' }),
        new w.File(['j'], 'c.JPG', { type: 'image/jpeg' })];
    const ev = fire(w, 'drop', 'dataTransfer', { files, types: ['Files'] });
    for (let i = 0; i < 12; i++) await flush();
    assert.equal(ev.defaultPrevented, true);
    assert.deepEqual(calls.map(c => c.url), ['/api/term/paste-image', '/api/term/paste',
        '/api/term/upload', '/api/term/paste-image', '/api/term/paste']);
    assert.equal(calls[0].opts.body.get('image').name, 'a.png');
    assert.equal(calls[2].opts.body.get('file').name, 'b.pdf');
    assert.equal(calls[3].opts.body.get('image').name, 'c.JPG');
    assert.deepEqual(toasts(w), [{ kind: 'ok', text: 'Saved uploads/b.pdf' }]);
});

test('an image type outside the old allowlist (svg) goes to uploads/', async () => {
    const { w, calls } = page();
    fire(w, 'drop', 'dataTransfer', { files: [new w.File(['<svg/>'], 'x.svg', { type: 'image/svg+xml' })], types: ['Files'] });
    for (let i = 0; i < 4; i++) await flush();
    assert.deepEqual(calls.map(c => c.url), ['/api/term/upload']);
});

test('a refused image paste (share guest) types nothing and shows an error toast', async () => {
    const { w, calls } = page({ respond: () => ({ status: 403,
        body: { ok: false, error: 'Share guests cannot paste images.', reason: 'share_guest' } }) });
    fire(w, 'drop', 'dataTransfer', { files: [new w.File(['p'], 'a.png', { type: 'image/png' })], types: ['Files'] });
    for (let i = 0; i < 6; i++) await flush();
    assert.deepEqual(calls.map(c => c.url), ['/api/term/paste-image']);
    assert.deepEqual(toasts(w), [{ kind: 'err', text: 'Image not pasted: Share guests cannot paste images.' }]);
});

test('with the Settings switch off nothing is claimed', async () => {
    const { w, calls } = page({ enabled: false });
    const ev = fire(w, 'drop', 'dataTransfer', { files: [new w.File(['x'], 'a.txt')], types: ['Files'] });
    await flush();
    assert.equal(ev.defaultPrevented, false);
    assert.equal(calls.length, 0);
});
