// RIGHT-CLICK IN A PANE PASTES THROUGH CHELA'S PASTE PATH (CMX-201).
//
// app.py's _TERM_CONTEXTMENU_SHIM and _TERM_PASTE_KEY_SHIM are lifted straight out
// of app.py and evaluated in a jsdom page at /term/<wid>/ (the pane's real path, so
// the shims resolve the same window id they do in ttyd's page).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - a fine-pointer right-click is defaultPrevented (no native menu) and pastes
//     the clipboard via /api/term/paste — the SAME function Ctrl/Cmd+V calls (spied);
//   - an image on the clipboard goes through /api/term/paste-image, then its path;
//   - an empty clipboard pastes nothing;
//   - a coarse pointer (phone long-press) is left alone: no preventDefault, no paste;
//   - MUST STILL PASS: the right-button mousedown/mouseup xterm.js forwards to tmux
//     and the app still reach the page, un-prevented.
//
// Run: node --test tests/term_contextmenu.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const APP_PY = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..',
    'chela', 'dashboard', 'app.py'), 'utf8');

// The shim as app.py defines it: the concatenated Python string literals of the
// `NAME = ( ... )` tuple, comment lines dropped, Python escapes undone.
function shimFromAppPy(name) {
    const m = new RegExp(`^${name} = \\(\\n([\\s\\S]*?)\\n\\)\\n`, 'm').exec(APP_PY);
    if (!m) throw new Error(`${name} moved in app.py — update this test`);
    const parts = m[1].split('\n').filter(l => !/^\s*#/.test(l))
        .map(l => /^\s*"((?:[^"\\]|\\.)*)"\s*$/.exec(l))
        .map((x, i) => { if (!x) throw new Error(`unparsed ${name} line ${i}`); return x[1]; });
    const html = parts.join('').replace(/\\\\/g, '\\');
    const js = /^<script>([\s\S]*)<\/script>$/.exec(html);
    if (!js) throw new Error(`${name} is not a single <script> block`);
    return js[1];
}

const flush = () => new Promise(r => setTimeout(r, 0));

// clip: {text} → a text/plain item; {image} → an image/png item; {} → empty clipboard.
function pane({ fine = true, clip = {} } = {}) {
    const dom = new JSDOM('<!doctype html><html><body><div class="xterm"></div></body></html>',
        { url: 'http://localhost:5005/term/%401/', runScripts: 'outside-only' });
    const w = dom.window;
    const posts = [];
    w.fetch = async (path, init) => {
        const body = typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
        posts.push({ path, body });
        return { ok: true, json: async () => (path === '/api/term/paste-image' ? { path: '/tmp/img.png' } : {}) };
    };
    w.matchMedia = q => ({ media: q, matches: q === '(pointer: fine)' ? fine : false });
    const items = [];
    if (clip.text !== undefined) items.push({ types: ['text/plain'], getType: async () => ({ text: async () => clip.text }) });
    if (clip.image) items.push({ types: ['image/png'], getType: async () => new w.Blob(['x'], { type: 'image/png' }) });
    Object.defineProperty(w.navigator, 'clipboard', { value: { read: async () => items }, configurable: true });
    w.eval(shimFromAppPy('_TERM_PASTE_KEY_SHIM'));
    w.eval(shimFromAppPy('_TERM_CONTEXTMENU_SHIM'));
    const target = w.document.querySelector('.xterm');
    const rightClick = () => {
        const ev = new w.MouseEvent('contextmenu', { bubbles: true, cancelable: true, button: 2 });
        target.dispatchEvent(ev);
        return ev;
    };
    const ctrlV = () => target.dispatchEvent(
        new w.KeyboardEvent('keydown', { key: 'v', ctrlKey: true, bubbles: true, cancelable: true }));
    return { w, posts, target, rightClick, ctrlV };
}

test('a right-click suppresses the native menu and pastes the clipboard text', async () => {
    const { posts, rightClick } = pane({ clip: { text: 'hello' } });
    const ev = rightClick();
    assert.equal(ev.defaultPrevented, true, 'the native context menu was not suppressed');
    await flush(); await flush();
    assert.deepEqual(posts, [{ path: '/api/term/paste', body: { agent: '@1', text: 'hello' } }]);
});

test('right-click and Ctrl+V call the SAME paste function (one code path)', async () => {
    const { w, rightClick, ctrlV } = pane({ clip: { text: 'x' } });
    assert.equal(typeof w.__chelaPasteClipboard, 'function', 'the Ctrl+V shim no longer exposes its paste function');
    const calls = [];
    w.__chelaPasteClipboard = () => { calls.push('paste'); return Promise.resolve(); };
    rightClick();
    assert.deepEqual(calls, ['paste'], 'right-click did not call the shared paste function');
    ctrlV();
    assert.deepEqual(calls, ['paste', 'paste'], 'Ctrl+V did not call the shared paste function');
});

test('an image on the clipboard goes through /api/term/paste-image, then its path', async () => {
    const { posts, rightClick } = pane({ clip: { image: true } });
    rightClick();
    for (let i = 0; i < 6; i++) await flush();
    assert.deepEqual(posts.map(p => p.path), ['/api/term/paste-image', '/api/term/paste']);
    assert.deepEqual(posts[1].body, { agent: '@1', text: '/tmp/img.png' });
});

test('an empty clipboard pastes nothing', async () => {
    for (const clip of [{}, { text: '' }]) {
        const { posts, rightClick } = pane({ clip });
        assert.equal(rightClick().defaultPrevented, true);
        for (let i = 0; i < 4; i++) await flush();
        assert.deepEqual(posts, [], `an empty clipboard (${JSON.stringify(clip)}) posted a paste`);
    }
});

test('a coarse pointer (phone long-press) keeps its native behaviour and pastes nothing', async () => {
    const { posts, rightClick } = pane({ fine: false, clip: { text: 'hello' } });
    assert.equal(rightClick().defaultPrevented, false, 'the shim suppressed contextmenu on a phone');
    await flush(); await flush();
    assert.deepEqual(posts, [], 'a phone long-press pasted');
});

test('MUST STILL PASS: right-button mousedown/mouseup still reach xterm, un-prevented', () => {
    const { w, target } = pane({ clip: { text: 'x' } });
    const seen = [];
    target.addEventListener('mousedown', e => seen.push(`down:${e.button}`));
    target.addEventListener('mouseup', e => seen.push(`up:${e.button}`));
    const down = new w.MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 2 });
    const up = new w.MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 2 });
    target.dispatchEvent(down); target.dispatchEvent(up);
    assert.deepEqual(seen, ['down:2', 'up:2']);
    assert.equal(down.defaultPrevented || up.defaultPrevented, false);
});

// That term_http actually SERVES this shim is guarded from the rendered response in
// tests/test_term_contextmenu_shim.py — not from app.py's source text here.
