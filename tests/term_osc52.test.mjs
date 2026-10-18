// A SELECTION IN A PANE REACHES THE PC CLIPBOARD VIA OSC 52 (CMX-206).
//
// app.py's _TERM_OSC52_SHIM is lifted straight out of app.py and evaluated in a jsdom
// page with a fake ttyd `window.term` whose parser records registerOscHandler calls.
// The handler is driven with the exact OSC 52 payloads tmux forwards (the part after
// `ESC ] 52 ;`).
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - a write (`c;<base64>`, and the other target letters) → writeText(decoded text),
//     UTF-8 decoded, not Latin-1;
//   - ⛔ a `?` READ query → ignored, writeText never called, nothing written back;
//   - oversize (> 1 MiB decoded), malformed base64, invalid UTF-8, unknown targets,
//     an empty payload → ignored;
//   - a refused writeText (or no clipboard API) → a visible toast in the pane;
//   - the handler is registered on window.term for OSC 52, also when ttyd assigns
//     window.term AFTER the shim ran, and only once per Terminal.
//
// Run: pnpm exec vitest run tests/term_osc52.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { test, afterEach } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const APP_PY = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..',
    'chela', 'dashboard', 'app.py'), 'utf8');

// Same extraction as term_contextmenu.test.mjs: the concatenated Python string
// literals of the `NAME = ( ... )` tuple, comment lines dropped, escapes undone.
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

// Every pane is closed after each test, pass or fail: the shim's setInterval keeps a
// jsdom window (and so node) alive, and a failed assertion must go RED, not hang.
const OPEN = [];
afterEach(() => { for (const w of OPEN.splice(0)) w.close(); });

const flush = () => new Promise(r => setTimeout(r, 0));
const b64 = s => Buffer.from(s, 'utf8').toString('base64');

function fakeTerm() {
    const handlers = [];
    return { handlers, parser: { registerOscHandler: (id, fn) => { handlers.push({ id, fn }); return { dispose() {} }; } } };
}

// writeText: 'ok' records the text; 'refuse' rejects; 'throw' throws synchronously;
// 'none' → no navigator.clipboard at all.
function pane({ writeText = 'ok', termFirst = true } = {}) {
    const dom = new JSDOM('<!doctype html><html><body><div class="xterm"></div></body></html>',
        { url: 'http://localhost:5005/term/%401/', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    OPEN.push(w);
    const writes = [];
    const reads = [];
    if (writeText !== 'none') {
        Object.defineProperty(w.navigator, 'clipboard', {
            configurable: true,
            value: {
                writeText: async t => {
                    if (writeText === 'refuse') throw new w.DOMException('Document is not focused.', 'NotAllowedError');
                    writes.push(t);
                },
                readText: async () => { reads.push('read'); return 'SECRET'; },
            },
        });
    }
    if (writeText === 'throw') w.navigator.clipboard.writeText = () => { throw new Error('sync'); };
    w.console.warn = () => {};
    const term = fakeTerm();
    if (termFirst) w.term = term;
    w.eval(shimFromAppPy('_TERM_OSC52_SHIM'));
    const osc = data => {
        const h = term.handlers.find(x => x.id === 52);
        assert.ok(h, 'no OSC 52 handler registered on window.term');
        return h.fn(data);
    };
    const toasts = () => [...w.document.querySelectorAll('.chela-osc52-toast')].map(d => d.textContent);
    return { w, term, writes, reads, osc, toasts };
}

test('an OSC 52 write puts the decoded selection on the clipboard', async () => {
    const p = pane();
    assert.equal(p.osc(`c;${b64('hello world')}`), true, 'the OSC 52 sequence was not marked handled');
    await flush();
    assert.deepEqual(p.writes, ['hello world']);
    assert.deepEqual(p.toasts(), []);
});

test('the text is decoded as UTF-8 (Hebrew, emoji, newlines), not Latin-1', async () => {
    const p = pane();
    const s = 'שלום ✻ 🎉\nline two';
    p.osc(`c;${b64(s)}`);
    await flush();
    assert.deepEqual(p.writes, [s]);
});

test('every selection-target form tmux/apps send is honoured (empty, p, s, 0-7, multi)', async () => {
    for (const pc of ['', 'p', 's', 'q', '0', '7', 'pc']) {
        const p = pane();
        p.osc(`${pc};${b64('x' + pc)}`);
        await flush();
        assert.deepEqual(p.writes, ['x' + pc], `target '${pc}' was not written`);
    }
});

test('⛔ a `?` READ query is ignored: no clipboard write, no read, nothing answered', async () => {
    for (const q of ['c;?', ';?', 'p;?']) {
        const p = pane();
        assert.equal(p.osc(q), true, `the read query ${q} fell through to another handler`);
        await flush();
        assert.deepEqual(p.writes, [], `read query ${q} wrote the clipboard`);
        assert.deepEqual(p.reads, [], `read query ${q} read the operator's clipboard`);
        assert.deepEqual(p.toasts(), []);
    }
});

test('malformed, empty or non-UTF-8 payloads are ignored', async () => {
    const bad = [
        'c;', 'c;!!!!', 'c;aGVsbG8', 'c;aGVs bG8=', `c;${b64('ok')}===`, 'c;aGVsbG8=\n',
        `x;${b64('bad target')}`, `C;${b64('upper target')}`, b64('no separator'),
        `c;${Buffer.from([0xff, 0xfe, 0x41]).toString('base64')}`,
    ];
    for (const data of bad) {
        const p = pane();
        p.osc(data);
        await flush();
        assert.deepEqual(p.writes, [], `malformed payload ${JSON.stringify(data)} was written`);
    }
});

test('oversize (> 1 MiB decoded) is ignored; exactly 1 MiB is written', async () => {
    const MiB = 1048576;
    const over = pane();
    over.osc(`c;${Buffer.alloc(MiB + 1, 0x61).toString('base64')}`);
    await flush();
    assert.deepEqual(over.writes, [], 'an over-1MiB selection was written');
    const at = pane();
    at.osc(`c;${Buffer.alloc(MiB, 0x61).toString('base64')}`);
    await flush();
    assert.equal(at.writes.length, 1, 'a selection of exactly 1 MiB was dropped');
    assert.equal(at.writes[0].length, MiB);
});

test('a refused writeText fails VISIBLY with a toast in the pane', async () => {
    for (const mode of ['refuse', 'throw', 'none']) {
        const p = pane({ writeText: mode });
        p.osc(`c;${b64('hi')}`);
        await flush(); await flush();
        const t = p.toasts();
        assert.equal(t.length, 1, `writeText mode '${mode}' failed silently`);
        assert.match(t[0], /clipboard/i);
    }
});

test('the handler is registered once, for OSC 52, also when ttyd assigns window.term late', async () => {
    const early = pane();
    assert.deepEqual(early.term.handlers.map(h => h.id), [52]);
    const late = pane({ termFirst: false });
    assert.deepEqual(late.term.handlers, []);
    late.w.term = late.term;
    await new Promise(r => setTimeout(r, 1200));
    assert.deepEqual(late.term.handlers.map(h => h.id), [52], 'a late window.term never got the handler (or got it twice)');
    late.osc(`c;${b64('late')}`);
    await flush();
    assert.deepEqual(late.writes, ['late']);
});

// That term_http actually SERVES this shim is guarded from the rendered response in
// tests/test_term_osc52_shim.py — not from app.py's source text here.
