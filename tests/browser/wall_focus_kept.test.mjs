// CMX-493 — a focused Wall pane keeps the keyboard until the user moves it.
//
// Liav, 11-23 (0.20.0): click a pane, it gets the ring and the cursor, a moment later it loses
// both. Two causes, both measured here on the built page:
//   1. ttyd calls terminal.focus() whenever its socket opens, so a SIBLING pane that loads or
//      reconnects (term-reconnect.js reloads its document) takes the keyboard.
//      static/term-focus.js, injected by term_http, stops it.
//   2. the 30 s refresh rebuilt the whole wall, every iframe included, when it saw a window come
//      or go before the 4 s tick did (features/wall renderTerminals).
//
// The panes run a stand-in for ttyd's page: a textarea focused on load (onSocketOpen) and on a
// mousedown whose default is prevented (xterm's own handler). What it cannot: Safari.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { AGENTS, launchChromium, openDashboard, ORIGIN } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const paneHtml = guard => `<!doctype html><html><head>${guard ? '<script src="/static/term-focus.js"></script>' : ''}</head>
<body><textarea id="t" style="width:90%;height:90px"></textarea><script>
    const t = document.getElementById('t');
    window.term = { focus: () => t.focus() }; // ttyd's page global (focusFrame calls it)
    document.addEventListener('mousedown', e => { e.preventDefault(); t.focus(); });
    t.focus(); // ttyd's onSocketOpen
</script></body></html>`;

/** The Wall with stand-in panes, every one loaded; `fake` installs a fake clock first. */
async function wall({ guard = true, fake = false, api = {} } = {}) {
    const d = await openDashboard(browser, { width: 1440, height: 900, api });
    const { context, page } = d;
    if (fake) await page.clock.install();
    await context.route(u => u.origin === ORIGIN && u.pathname.startsWith('/term/'),
        r => r.fulfill({ contentType: 'text/html', body: paneHtml(guard) }));
    await page.reload();
    await page.waitForFunction(() => document.querySelectorAll('iframe.term-frame').length === 4
        && [...document.querySelectorAll('iframe.term-frame')].every(f => f.contentDocument?.getElementById('t')), null, { timeout: 15000 });
    const frame = wid => page.frames().find(f => decodeURIComponent(f.url()).includes(`/term/${wid}/`));
    const typed = wid => frame(wid).evaluate(() => document.getElementById('t').value);
    /** Reload a pane's document, as term-reconnect.js does, and wait for its on-load focus(). */
    const reconnect = async wid => {
        const loaded = page.evaluate(w => new Promise(res => {
            const f = [...document.querySelectorAll('iframe.term-frame')].find(x => decodeURIComponent(x.src).includes(`/term/${w}/`));
            f.addEventListener('load', () => res(), { once: true });
            f.contentWindow.location.reload();
        }), wid);
        await loaded;
    };
    const holder = () => page.evaluate(() => {
        const f = [...document.querySelectorAll('iframe.term-frame')].find(x => x.contentDocument.hasFocus());
        return f ? f.closest('[gs-id]').getAttribute('gs-id') : null;
    });
    return { ...d, frame, typed, reconnect, holder };
}

describe('CMX-493: the focused pane keeps the keyboard', { skip: why }, () => {
    test('60 s of live polls, a window coming and going, and a sibling reconnecting: same pane, same iframe', async () => {
        const api = { '/api/agents': AGENTS };
        const { context, page, errors, frame, typed, reconnect, holder } = await wall({ fake: true, api });
        try {
            await frame('@1').click('#t');
            await page.evaluate(() => {
                const f = document.querySelector('[gs-id="@1"] iframe');
                f.__mine = true;
                window.__lost = [];
                f.contentWindow.addEventListener('blur', () => window.__lost.push('blur'));
            });
            const reads = [];
            page.on('request', r => { const p = new URL(r.url()).pathname; if (p.startsWith('/api/')) reads.push(p); });
            let swapped = false;
            for (let s = 1; s <= 120; s++) {
                // just ahead of the refresh: whichever poll reads the new window set first
                if (s === 57) {
                    api['/api/agents'] = [...AGENTS.filter(a => a.window_id !== '@3'), { ...AGENTS[0], name: 'e-new', window_id: '@5' }];
                    swapped = true;
                }
                await page.clock.runFor(500);
                if (s % 20 === 0) await reconnect('@2');
                const at = await page.evaluate(() => [document.querySelector('[gs-id="@1"] iframe')?.__mine === true,
                    document.activeElement?.closest('[gs-id]')?.getAttribute('gs-id') ?? null]);
                assert.deepEqual([s / 2, ...at, await holder()], [s / 2, true, '@1', '@1'], 'the pane lost the keyboard or its iframe');
            }
            assert.ok(swapped);
            // the polls did run, and the window set did change on screen
            for (const p of ['/api/agents', '/api/agents/context']) assert.ok(reads.filter(x => x === p).length >= 14, `${p} polled ${reads.filter(x => x === p).length}x`);
            assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.grid-stack-item')].map(e => e.getAttribute('gs-id')).sort()), ['@1', '@2', '@4', '@5']);
            assert.deepEqual(await page.evaluate(() => window.__lost), []);
            assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.term-focused')].map(e => e.closest('[gs-id]').getAttribute('gs-id'))), ['@1']);
            await page.keyboard.type('still here');
            assert.equal(await typed('@1'), 'still here');
            assert.deepEqual(errors, []);
        } finally {
            await context.close();
        }
    });

    // The refresh alone: the store already holds the new set when the Wall's render runs.
    test('the view switch refresh, reading a changed window set, reloads no pane', async () => {
        const api = { '/api/agents': AGENTS };
        const { context, page, frame } = await wall({ api });
        try {
            await frame('@1').click('#t');
            await page.evaluate(() => { document.querySelector('[gs-id="@1"] iframe').__mine = true; });
            api['/api/agents'] = AGENTS.filter(a => a.window_id !== '@3');
            await page.evaluate(() => document.querySelector('#side-nav .side-item, #side-nav button, #side-nav a')?.click());
            await page.waitForFunction(() => !document.querySelector('[gs-id="@3"]'), null, { timeout: 8000 });
            assert.equal(await page.evaluate(() => document.querySelector('[gs-id="@1"] iframe').__mine), true);
        } finally {
            await context.close();
        }
    });

    test('negative control: without the guard a reconnecting sibling takes the keyboard', async () => {
        const { context, page, frame, typed, reconnect, holder } = await wall({ guard: false });
        try {
            await frame('@1').click('#t');
            assert.equal(await holder(), '@1');
            await reconnect('@2');
            assert.equal(await holder(), '@2');
            await page.keyboard.type('x');
            assert.equal(await typed('@1'), '');
        } finally {
            await context.close();
        }
    });

    test('the user still moves the keyboard: a press in a pane, Alt+N; a pane that reconnects keeps its own', async () => {
        const { context, page, frame, typed, reconnect, holder } = await wall();
        try {
            await frame('@1').click('#t');
            assert.equal(await holder(), '@1');
            await frame('@2').click('#t'); // xterm's shape: mousedown default prevented, focus() called
            await page.keyboard.type('two');
            assert.equal(await typed('@2'), 'two');
            await reconnect('@2');
            assert.equal(await holder(), '@2');
            await page.keyboard.type('again');
            assert.equal(await typed('@2'), 'again');
            const before = await holder();
            await page.keyboard.press('Alt+2'); // row 1 is @2, the one that needs you
            await page.waitForFunction(b => {
                const f = [...document.querySelectorAll('iframe.term-frame')].find(x => x.contentDocument.hasFocus());
                return f && f.closest('[gs-id]').getAttribute('gs-id') !== b;
            }, before, { timeout: 5000 });
            const now = await holder();
            await page.keyboard.type('alt');
            assert.equal(await typed(now), 'alt');
        } finally {
            await context.close();
        }
    });

    test('a pane takes focus when nobody else has the keyboard, and never from a dashboard field', async () => {
        const { context, page, frame, reconnect, holder } = await wall();
        try {
            await page.evaluate(() => { document.activeElement?.blur(); window.focus(); });
            assert.equal(await holder(), null);
            await reconnect('@3');
            assert.equal(await holder(), '@3');
            for (const make of ['<input id="f">', '<textarea id="f"></textarea>', '<select id="f"></select>', '<div id="f" contenteditable="true">x</div>']) {
                await page.evaluate(h => {
                    document.getElementById('f')?.remove();
                    document.body.insertAdjacentHTML('beforeend', h);
                    document.getElementById('f').focus();
                }, make);
                await frame('@2').evaluate(() => new Promise(r => setTimeout(r, 0)));
                await reconnect('@2');
                assert.equal(await page.evaluate(() => document.activeElement?.id), 'f', make);
            }
            // a button is not typing: a pane may take focus from it, as before
            await page.evaluate(() => {
                document.getElementById('f').remove();
                document.body.insertAdjacentHTML('beforeend', '<button id="f">b</button>');
                document.getElementById('f').focus();
            });
            await reconnect('@2');
            assert.equal(await holder(), '@2');
        } finally {
            await context.close();
        }
    });

    test('a background window, or a stale activeElement (CMX-104), still names the pane that is safe', async () => {
        const { context, page, frame, holder } = await wall();
        const selfFocus = wid => frame(wid).evaluate(() => document.getElementById('t').focus());
        const ae = () => page.evaluate(() => document.activeElement?.closest('[gs-id]')?.getAttribute('gs-id') ?? null);
        try {
            await frame('@1').click('#t');
            // the window is in the background: no document has focus, activeElement is the pane it returns to
            for (const w of ['@1', '@2']) await frame(w).evaluate(() => { document.hasFocus = () => false; });
            await selfFocus('@2');
            assert.equal(await ae(), '@1');
            // activeElement lags at the body while @1 really holds the keyboard
            await page.evaluate(() => { document.activeElement.blur(); window.focus(); });
            await frame('@1').evaluate(() => { document.hasFocus = () => true; });
            await selfFocus('@2');
            assert.equal(await ae(), null);
            await frame('@1').evaluate(() => { document.hasFocus = () => false; });
            await selfFocus('@2');
            assert.equal(await ae(), '@2');
            assert.equal(await holder(), null); // both stubbed: activeElement is the only witness here
            // a synthetic event is no gesture
            await frame('@3').evaluate(() => {
                document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
                document.getElementById('t').focus();
            });
            assert.equal(await ae(), '@2');
            // a press is, for a second only
            await frame('@3').click('#t');
            assert.equal(await ae(), '@3');
            await frame('@1').click('#t');
            await page.waitForTimeout(1100);
            await frame('@3').evaluate(() => { document.hasFocus = () => false; });
            await selfFocus('@3');
            assert.equal(await ae(), '@1');
        } finally {
            await context.close();
        }
    });
});
