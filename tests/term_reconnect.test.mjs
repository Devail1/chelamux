// CMX-167: the self-reconnect shim (chela/dashboard/static/term-reconnect.js), run for real in
// a jsdom page at /term/@1/ with a FAKE WebSocket, a fake clock and a fake ttyd: `connect()`
// opens a "tty" socket and, on close, shows ttyd's "Press ⏎ to Reconnect" overlay and waits
// for Enter, as ttyd 1.7.7 does after an `error`. The shim's real `location.reload()` runs:
// jsdom reports it as a "navigation" not-implemented error on the virtual console, and the
// harness answers that by re-running connect (a fresh page), so "a new socket opened" is
// observable AND a no-op reload goes red. No test replaces __chelaReconnect.reload.
//
// Guards (each corrupt→RED, recorded in the PR's self-check experiments):
//   - close while visible → one new socket after the 0.5 s backoff, no keypress;
//   - close while hidden → nothing until visibilitychange → visible, then exactly one;
//   - two closes in a row → one reconnect in flight, not two;
//   - a frame chela suspended (data-suspended-src / about:blank) never reconnects;
//   - server down → backoff 0.5/1/2/5/10/10 s, a quiet status line, Enter retries at once;
//   - ttyd's overlay is hidden while a reconnect is pending.
//
// Run: pnpm exec vitest run tests/term_reconnect.test.mjs (pytest runs it via tests/test_js_suites.py).
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM, VirtualConsole } from 'jsdom';

const SHIM = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..',
    'chela', 'dashboard', 'static', 'term-reconnect.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r)); };

function page({ probe = () => 200, frame = null } = {}) {
    const vc = new VirtualConsole();
    let onReload = () => { throw new Error('reload before the fake ttyd loaded'); };
    vc.on('jsdomError', e => {
        if (/navigation/.test(e.message)) onReload();
        else throw e;
    });
    const dom = new JSDOM('<!doctype html><html><head></head><body><div class="xterm"></div></body></html>',
        { url: 'http://localhost:5005/term/%401/', runScripts: 'outside-only', pretendToBeVisual: true,
          virtualConsole: vc });
    const w = dom.window;
    // fake clock
    let now = 0, seq = 0;
    const timers = new Map();
    w.setTimeout = (fn, ms) => { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn }); return id; };
    w.clearTimeout = id => { timers.delete(id); };
    const advance = async ms => {
        const end = now + ms;
        for (;;) {
            const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
            if (!due) break;
            timers.delete(due[0]); now = due[1].at; due[1].fn(); await flush();
        }
        now = end; await flush();
    };
    // visibility
    let vis = 'visible';
    Object.defineProperty(w.document, 'visibilityState', { configurable: true, get: () => vis });
    const setVis = v => { vis = v; w.document.dispatchEvent(new w.Event('visibilitychange')); };
    const setVisSilently = v => { vis = v; };   // the state changes, no event fires
    if (frame) Object.defineProperty(w, 'frameElement', { configurable: true, get: () => frame(w) });
    // fake WebSocket
    const sockets = [];
    class FakeWS extends w.EventTarget {
        constructor(url, protocols) {
            super(); this.url = url; this.protocols = protocols; this.readyState = 0; sockets.push(this);
        }
        open() { this.readyState = 1; this.dispatchEvent(new w.Event('open')); }
        close() { this.readyState = 3; this.dispatchEvent(new w.Event('close')); }
    }
    FakeWS.CONNECTING = 0; FakeWS.OPEN = 1; FakeWS.CLOSING = 2; FakeWS.CLOSED = 3;
    w.WebSocket = FakeWS;
    const probes = [];
    w.fetch = async (url, opts) => {
        probes.push({ url: String(url), at: now, opts });
        const status = probe(probes.length);
        return { ok: status < 400, status };
    };
    w.eval(SHIM);
    // fake ttyd (runs AFTER the shim, as ttyd's body script does)
    const xterm = w.document.querySelector('.xterm');
    const overlay = w.document.createElement('div');
    const keyPresses = [];
    w.document.addEventListener('keydown', e => keyPresses.push(e.key));
    const connect = () => {
        const s = new w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);
        s.addEventListener('close', () => { overlay.textContent = 'Press ⏎ to Reconnect'; xterm.appendChild(overlay); });
        s.addEventListener('open', () => { if (overlay.parentNode) overlay.remove(); });
        return s;
    };
    const reloads = [];
    onReload = () => { reloads.push(now); connect(); };   // location.reload() → a fresh ttyd page
    const first = connect(); first.open();
    return { w, sockets, probes, reloads, advance, setVis, setVisSilently, overlay, keyPresses, first, now: () => now };
}

test('a socket closed while visible reconnects after the 0.5 s backoff, with no keypress', async () => {
    const p = page();
    p.first.close();
    await p.advance(499);
    assert.equal(p.reloads.length, 0, 'not before the backoff');
    await p.advance(1);
    assert.deepEqual(p.reloads, [500]);
    assert.equal(p.sockets.length, 2, 'a new socket opened');
    assert.deepEqual(p.probes.map(x => x.url), ['/term/%401/token']);
    assert.equal(p.probes[0].opts.cache, 'no-store');
    assert.equal(p.probes[0].opts.credentials, 'same-origin');
    assert.deepEqual(p.keyPresses, [], 'no Enter was synthesized');
});

test('the wrapper keeps WebSocket constants and returns the real socket', () => {
    const p = page();
    assert.equal(p.w.WebSocket.OPEN, 1);
    assert.equal(p.w.WebSocket.CLOSED, 3);
    assert.equal(p.first.url, 'ws://localhost:5005/term/%401/ws');
    assert.deepEqual(p.first.protocols, ['tty']);
});

test('a socket closed while hidden waits for visibilitychange, then reconnects exactly once', async () => {
    const p = page();
    p.setVis('hidden');
    p.first.close();
    await p.advance(60000);
    assert.equal(p.reloads.length, 0, 'never while hidden');
    assert.equal(p.probes.length, 0);
    p.setVis('visible');   // visibilitychange, and nothing else
    await p.advance(0);
    assert.deepEqual(p.reloads, [60000], 'reconnects on visibilitychange itself');
    p.w.dispatchEvent(new p.w.Event('pageshow'));
    p.w.dispatchEvent(new p.w.Event('online'));
    p.setVis('visible');
    await p.advance(30000);
    assert.equal(p.reloads.length, 1, 'exactly one reconnect');
});

test('becoming visible with no event at all never reconnects: the triggers are load-bearing', async () => {
    const p = page();
    p.setVis('hidden');
    p.first.close();
    await p.advance(1000);
    p.setVisSilently('visible');
    await p.advance(60000);
    assert.equal(p.reloads.length, 0);
    assert.equal(p.probes.length, 0);
});

for (const trigger of ['pageshow', 'online']) {
    test(`hidden then visible: ${trigger} alone reconnects`, async () => {
        const p = page();
        p.setVis('hidden');
        p.first.close();
        await p.advance(1000);
        p.setVisSilently('visible');
        p.w.dispatchEvent(new p.w.Event(trigger));
        await p.advance(0);
        assert.deepEqual(p.reloads, [1000]);
        assert.equal(p.sockets.length, 2);
    });
}

test('online while hidden does nothing; pageshow after pagehide reconnects', async () => {
    const p = page();
    p.setVis('hidden');
    p.first.close();
    p.w.dispatchEvent(new p.w.Event('online'));
    await p.advance(1000);
    assert.equal(p.reloads.length, 0);
    p.w.dispatchEvent(new p.w.Event('pagehide'));
    p.setVis('visible');
    await p.advance(1000);
    assert.equal(p.reloads.length, 0, 'a document going away never reloads');
    p.w.dispatchEvent(new p.w.Event('pageshow'));
    await p.advance(0);
    assert.equal(p.reloads.length, 1);
});

test('two close events in a row leave one reconnect in flight, not two', async () => {
    let release;
    const p = page({ probe: () => 200 });
    const slow = new Promise(r => { release = r; });
    const fetch0 = p.w.fetch;
    let started = 0;
    p.w.fetch = async (...a) => { started++; await slow; return fetch0(...a); };
    p.first.close();
    p.first.dispatchEvent(new p.w.Event('close'));
    await p.advance(500);
    assert.equal(started, 1, 'one attempt started');
    p.first.dispatchEvent(new p.w.Event('close'));
    p.w.dispatchEvent(new p.w.Event('online'));
    await p.advance(5000);
    assert.equal(started, 1, 'a second attempt while one is in flight is dropped');
    release();
    await p.advance(5000);
    assert.equal(started, 1);
    assert.equal(p.reloads.length, 1);
    assert.equal(p.sockets.length, 2);
});

test('ttyd reconnecting by itself is left alone (no second connect)', async () => {
    const p = page();
    p.first.close();
    const own = new p.w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);   // ttyd's own retry
    await p.advance(10000);
    assert.equal(p.reloads.length, 0);
    assert.equal(p.probes.length, 0, 'not even a probe while ttyd\'s own socket is connecting');
    own.open();
    assert.ok(!p.w.document.documentElement.classList.contains('chela-reconnecting'));
});

test('a socket that is not ttyd\'s is not watched', async () => {
    const p = page();
    const other = new p.w.WebSocket('wss://relay.example/x');
    other.close();
    const proto = new p.w.WebSocket('wss://relay.example/y', 'chat');
    proto.close();
    await p.advance(10000);
    assert.equal(p.reloads.length, 0);
});

const suspendedFrame = attr => w => {
    const f = w.document.createElement('iframe');
    if (attr === 'stash') f.setAttribute('data-suspended-src', '/term/%401/');
    f.setAttribute('src', attr === 'blank' ? 'about:blank' : '/term/%401/');
    return f;
};

for (const attr of ['stash', 'blank']) {
    test(`a frame chela suspended (${attr}) never auto-reconnects`, async () => {
        const p = page({ frame: suspendedFrame(attr) });
        p.first.close();
        p.w.dispatchEvent(new p.w.Event('online'));
        await p.advance(60000);
        assert.equal(p.reloads.length, 0);
        assert.equal(p.probes.length, 0);
    });
}

test('a live (unsuspended) frame does reconnect: the suspended check is not a blanket no', async () => {
    const p = page({ frame: suspendedFrame('live') });
    p.first.close();
    await p.advance(500);
    assert.equal(p.reloads.length, 1);
});

test('a frame suspended while the probe is in flight does not reload', async () => {
    let stash = false;
    const p = page({ frame: w => { const f = w.document.createElement('iframe');
        if (stash) f.setAttribute('data-suspended-src', '/term/%401/'); return f; } });
    let release;
    const slow = new Promise(r => { release = r; });
    const fetch0 = p.w.fetch;
    p.w.fetch = async (...a) => { await slow; return fetch0(...a); };
    p.first.close();
    await p.advance(500);
    stash = true;
    release();
    await p.advance(0);
    assert.equal(p.reloads.length, 0);
});

test('server down: backoff 0.5, 1, 2, 5, 10, 10 s, then a quiet status line', async () => {
    let up = false;
    const p = page({ probe: () => (up ? 200 : 502) });
    p.first.close();
    await p.advance(28500);
    assert.deepEqual(p.probes.map(x => x.at), [500, 1500, 3500, 8500, 18500, 28500]);
    assert.equal(p.reloads.length, 0, 'never reload onto an error page');
    const st = p.w.document.querySelector('.chela-reconnect-status');
    assert.ok(st, 'status line shown');
    assert.equal(st.textContent, 'Reconnecting… press Enter to retry now');
    assert.ok(!/—/.test(st.textContent), 'no em dash');
    up = true;
    await p.advance(10000);
    assert.equal(p.reloads.length, 1);
    p.sockets.at(-1).open();
    assert.equal(p.w.document.querySelector('.chela-reconnect-status'), null, 'status cleared on open');
});

test('the status line waits for the second failure', async () => {
    const p = page({ probe: () => 502 });
    p.first.close();
    await p.advance(500);
    assert.equal(p.w.document.querySelector('.chela-reconnect-status'), null);
    await p.advance(1000);
    assert.ok(p.w.document.querySelector('.chela-reconnect-status'));
});

test('Enter retries at once while down, and does nothing while connected', async () => {
    let up = false;
    const p = page({ probe: () => (up ? 200 : 502) });
    const enter = () => p.w.dispatchEvent(new p.w.KeyboardEvent('keydown', { key: 'Enter' }));
    enter();
    await p.advance(0);
    assert.equal(p.probes.length, 0, 'connected: Enter is just Enter');
    p.first.close();
    await p.advance(8500);           // 4 failures, next try due at 18500
    assert.equal(p.probes.length, 4);
    up = true;
    p.w.dispatchEvent(new p.w.KeyboardEvent('keydown', { key: 'a' }));
    await p.advance(0);
    assert.equal(p.probes.length, 4, 'only Enter retries');
    enter();
    await p.advance(0);
    assert.equal(p.probes.length, 5);
    assert.equal(p.reloads.length, 1);
});

test('Enter resets the backoff to its start', async () => {
    const p = page({ probe: () => 502 });
    p.first.close();
    await p.advance(8500);           // 4 failures
    p.w.dispatchEvent(new p.w.KeyboardEvent('keydown', { key: 'Enter' }));
    await p.advance(0);              // probe 5 fails, failures=1 → next in 1 s
    await p.advance(1000);
    assert.deepEqual(p.probes.map(x => x.at).slice(4), [8500, 9500]);
});

test('ttyd\'s overlay is hidden while a reconnect is pending, and shown again after', async () => {
    const p = page({ probe: () => 502 });
    const html = p.w.document.documentElement;
    p.first.close();
    assert.equal(p.overlay.textContent, 'Press ⏎ to Reconnect');
    assert.ok(html.classList.contains('chela-reconnecting'));
    assert.equal(p.w.getComputedStyle(p.overlay).display, 'none');
    const own = new p.w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);
    own.open();
    assert.ok(!html.classList.contains('chela-reconnecting'));
    assert.notEqual(p.w.getComputedStyle(p.overlay).display, 'none');
});

test('a close from an older socket is ignored once a newer one is open', async () => {
    const p = page();
    const second = new p.w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);
    second.open();
    p.first.close();
    await p.advance(10000);
    assert.equal(p.reloads.length, 0);
    assert.ok(!p.w.document.documentElement.classList.contains('chela-reconnecting'));
});

test('a second close while a backoff is pending does not add a second timer', async () => {
    const p = page({ probe: () => 502 });
    p.first.close();
    await p.advance(300);
    const own = new p.w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);   // ttyd's retry
    own.close();
    await p.advance(1200);
    assert.deepEqual(p.probes.map(x => x.at), [500, 1500]);
});

test('injecting the shim twice wraps WebSocket once', () => {
    const p = page();
    const wrapped = p.w.WebSocket;
    p.w.eval(SHIM);
    assert.equal(p.w.WebSocket, wrapped);
});

test('ttyd reconnecting by itself while the probe is in flight cancels the reload', async () => {
    let release;
    const p = page();
    const slow = new Promise(r => { release = r; });
    const fetch0 = p.w.fetch;
    p.w.fetch = async (...a) => { await slow; return fetch0(...a); };
    p.first.close();
    await p.advance(500);
    const own = new p.w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);
    own.open();
    release();
    await p.advance(0);
    assert.equal(p.reloads.length, 0);
    own.close();                     // and the next drop is handled from scratch
    await p.advance(500);
    assert.equal(p.reloads.length, 1);
});

test('an older socket opening late does not clear a pending reconnect for the newer one', async () => {
    const p = page({ probe: () => 502 });
    const stale = new p.w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);
    const second = new p.w.WebSocket('ws://localhost:5005/term/%401/ws', ['tty']);
    second.open();
    second.close();
    await p.advance(1500);           // two failures: status line up
    stale.open();
    assert.ok(p.w.document.documentElement.classList.contains('chela-reconnecting'));
    assert.ok(p.w.document.querySelector('.chela-reconnect-status'));
});

test('after a reconnect opens, the next drop starts the backoff over at 0.5 s', async () => {
    let up = false;
    const p = page({ probe: () => (up ? 200 : 502) });
    p.first.close();
    await p.advance(8500);           // 4 failures
    up = true;
    await p.advance(10000);
    assert.deepEqual(p.reloads, [18500]);
    p.sockets.at(-1).open();
    p.sockets.at(-1).close();
    await p.advance(500);
    assert.deepEqual(p.reloads, [18500, 19000]);
});

test('the default reconnect is the document\'s own location.reload', () => {
    const p = page();
    p.w.__chelaReconnect.reload();
    assert.equal(p.reloads.length, 1, 'location.reload() ran');
});
