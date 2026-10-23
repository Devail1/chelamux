// CMX-321 — a wall pane's terminal must not visibly change font or size while it loads.
//
// Liav, 2026-10-23 (phone): the text jumps right after a pane opens. ttyd opens xterm at
// its own built-in defaults and paints; its SET_PREFERENCES frame re-fonts it to the
// server's --client-option fontSize/fontFamily; then _TERM_FONT_PREF_SHIM applies the
// viewer's Settings > Terminal choice and walks the size through a decoy (the CMX-155
// atlas fix). Each step reflowed in view.
//
// What this reads back: every animation frame, whether `.xterm` is visible (its computed
// opacity) and the fontSize / fontFamily / cols / rows xterm holds in that frame. No frame
// may be visible before the viewer's final font and size are set.
//   1. The vendored xterm.js booted the way ttyd 1.7.7 boots it (runs in CI): built-in
//      defaults at open(), then SET_PREFERENCES merged as ttyd merges it (server options,
//      then `location.search` last). Also the negative control: a font load that never
//      settles still reveals the pane, after TERM_BOOT_FALLBACK_MS. And a detector
//      control: without _TERM_BOOT_CSS the same frame check goes red.
//   2. A REAL ttyd launched with every --client-option agent-terminals.sh passes. Skipped
//      when no ttyd is installed (CI has none).
// What it cannot: iOS WebKit (Playwright drives Chromium with a phone viewport).
//
// Harness: tests/browser/fixture.mjs + term_page.mjs. Local: `pnpm run test:browser`.
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium } from './fixture.mjs';
import { VENDOR, fulfillFont, startTtyd, termShims, ttydClientOptions, ttydMissing } from './term_page.mjs';

const PHONE = { viewport: { width: 390, height: 600 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true };
// The viewer's Settings > Terminal size, unlike the server default (14) and ttyd's own (13).
const PREF_SIZE = 12;
const DECOY = 11;
// The family the shim builds for the default faces (JetBrains Mono + Miriam Mono CLM).
const PREF_FAM = "'JetBrains Mono','Symbols Nerd Font','Symbola Fallback','Miriam Mono CLM',monospace";
// ttyd 1.7.7's built-in termOptions (html/src/components/app.tsx): what open() paints.
const TTYD_OPEN = { fontSize: 13, fontFamily: 'Consolas,Liberation Mono,Menlo,Courier,monospace' };

// Runs before the page's own scripts: the viewer's prefs, and a per-frame recorder.
function bootInit({ prefSize, hangFonts }) {
    localStorage.setItem('chela_term_fontsize', String(prefSize));
    if (hangFonts) document.fonts.load = () => new Promise(() => {});
    const w = /** @type {any} */ (window);
    w.__frames = [];
    const rec = () => {
        const x = document.querySelector('.xterm');
        const t = window.term;
        if (x && t && t.options) {
            w.__frames.push({
                at: performance.now(), visible: getComputedStyle(x).opacity !== '0',
                size: t.options.fontSize, fam: t.options.fontFamily, cols: t.cols, rows: t.rows,
            });
        }
        requestAnimationFrame(rec);
    };
    requestAnimationFrame(rec);
}

async function framesUntilSettled(page, { budgetMs = 6000 } = {}) {
    // Settled: visible, and the last 10 frames agree on font, size and grid.
    const t0 = Date.now();
    for (;;) {
        const frames = await page.evaluate(() => /** @type {any} */ (window).__frames);
        const tail = frames.slice(-10);
        const stable = tail.length === 10 && tail.every(f => f.visible
            && f.size === tail[0].size && f.fam === tail[0].fam && f.cols === tail[0].cols && f.rows === tail[0].rows);
        if (stable || Date.now() - t0 > budgetMs) return frames;
        await page.waitForTimeout(100);
    }
}

// The frames a viewer saw that were not the final font, size and grid.
function wrongVisibleFrames(frames) {
    const last = frames[frames.length - 1];
    return frames.filter(f => f.visible && (f.size !== PREF_SIZE || f.fam !== PREF_FAM
        || f.cols !== last.cols || f.rows !== last.rows));
}

const { browser, why } = await launchChromium({ args: ['--disable-features=LocalNetworkAccessChecks'] });
after(() => browser && browser.close());

// --- 1. vendored xterm.js, booted as ttyd boots it -------------------------------------

const FIXTURE = 'http://chela.test';

// ttyd's boot, as 1.7.7 runs it: open() at the built-in defaults, then (when the socket
// delivers SET_PREFERENCES) every font option merged {server, ...location.search} and
// applied, each followed by fit(). The vendored build has no FitAddon: fit() is a no-op.
// `pokes`: call window.chelaApplyTermPrefs that many more times, one per frame, the way
// the shim's own 500ms reconcile tick can land while the first apply is mid-walk.
function ttydLikePage({ server, pokes = 0 }) {
    return `<script>
        const t = new Terminal(${JSON.stringify(TTYD_OPEN)});
        window.term = t; window.term.fit = () => {};
        t.open(document.getElementById('terminal-container'));
        let pokes = ${pokes};
        const poke = () => { if (pokes-- > 0) { window.chelaApplyTermPrefs(); requestAnimationFrame(poke); } };
        requestAnimationFrame(poke);
        t.write('$ echo boot\\r\\nboot\\r\\n$ ');
        setTimeout(() => {
            const q = Object.fromEntries(new URLSearchParams(location.search));
            if (q.fontSize) q.fontSize = Number.parseInt(q.fontSize, 10);
            const prefs = { ...${JSON.stringify(server)}, ...q };
            for (const k of ['fontSize', 'fontFamily']) if (k in prefs) { t.options[k] = prefs[k]; t.fit(); }
        }, 300);
    </script>`;
}

async function bootVendored({ head, hangFonts = false, server, pokes = 0 }) {
    const context = await browser.newContext(PHONE);
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== FIXTURE) return route.abort('blockedbyclient');
        if (url.pathname.startsWith('/static/fonts/')) return fulfillFont(route, url);
        if (url.pathname === '/vendor/xterm.js') return route.fulfill({ body: readFileSync(join(VENDOR, 'xterm.js')), contentType: 'text/javascript' });
        if (url.pathname === '/vendor/xterm.css') return route.fulfill({ body: readFileSync(join(VENDOR, 'xterm.css')), contentType: 'text/css' });
        return route.fulfill({
            contentType: 'text/html',
            body: '<!doctype html><html><head><meta charset="utf-8">'
                + '<link rel="stylesheet" href="/vendor/xterm.css">' + head + '</head>'
                + '<body style="margin:0"><div id="terminal-container" style="width:100vw;height:100vh"></div>'
                + '<script src="/vendor/xterm.js"></script>' + ttydLikePage({ server, pokes }) + '</body></html>',
        });
    });
    await context.addInitScript(bootInit, { prefSize: PREF_SIZE, hangFonts });
    const page = await context.newPage();
    await page.goto(FIXTURE + '/term/@1/?cid=c1');
    return { context, page };
}

describe('a pane paints nothing until its final font and size (vendored xterm.js, phone)', { skip: why }, () => {
    const shims = why ? {} : termShims();
    const options = why ? {} : ttydClientOptions();
    const server = { fontSize: Number(options.fontSize), fontFamily: options.fontFamily };
    const head = () => shims.themeShim + shims.fontCss + shims.bootCss + shims.prefShim;

    test('no visible frame at ttyd\'s default, the server default or the decoy size', async () => {
        const { context, page } = await bootVendored({ head: head(), server });
        try {
            const frames = await framesUntilSettled(page);
            const last = frames[frames.length - 1];
            assert.ok(last && last.visible, `the pane never became visible: ${JSON.stringify(last)}`);
            assert.deepEqual([last.size, last.fam], [PREF_SIZE, PREF_FAM]);
            assert.deepEqual(wrongVisibleFrames(frames), []);
            // Not vacuous: the decoy step the viewer must not see did happen, hidden. (ttyd's
            // open() default rarely lasts a frame now that apply() runs on window.term's
            // assignment; the detector control below shows the check can go red.)
            assert.ok(frames.some(f => !f.visible && f.size === DECOY), 'no hidden frame at the decoy size: the atlas walk did not run');
            // At the source: the viewer's prefs are pinned where ttyd's SET_PREFERENCES
            // reads them last, so its server default never lands.
            assert.equal(frames.some(f => f.size === server.fontSize), false,
                `a frame at the server default ${server.fontSize}px: SET_PREFERENCES reverted the viewer's size`);
            const q = new URLSearchParams(await page.evaluate(() => location.search));
            assert.deepEqual([q.get('cid'), q.get('fontSize'), q.get('fontFamily')], ['c1', String(PREF_SIZE), PREF_FAM]);
        } finally {
            await context.close();
        }
    });

    test('an apply that lands mid-walk waits for it: no visible decoy frame', async () => {
        const { context, page } = await bootVendored({ head: head(), server, pokes: 4 });
        try {
            const frames = await framesUntilSettled(page);
            const last = frames[frames.length - 1];
            assert.ok(last && last.visible, `the pane never became visible: ${JSON.stringify(last)}`);
            assert.deepEqual(wrongVisibleFrames(frames), []);
            // Revealed when the walk ends, not left for the fallback.
            const waited = frames.find(f => f.visible).at - frames[0].at;
            assert.ok(waited < shims.fallbackMs - 500, `revealed only after ${Math.round(waited)}ms: the walk's end never revealed`);
        } finally {
            await context.close();
        }
    });

    test('negative control: a font load that never settles still reveals the pane after the fallback', async () => {
        const { context, page } = await bootVendored({ head: head(), server, hangFonts: true });
        try {
            const frames = await framesUntilSettled(page);
            const shown = frames.find(f => f.visible);
            assert.ok(shown, 'a font load that never settles left the pane blank');
            const waited = shown.at - frames[0].at;
            assert.ok(waited >= shims.fallbackMs - 100 && waited < shims.fallbackMs + 1000,
                `revealed ${Math.round(waited)}ms after the terminal appeared; the fallback is ${shims.fallbackMs}ms`);
        } finally {
            await context.close();
        }
    });

    test('detector control: without the boot CSS the same check sees the wrong frames', async () => {
        const { context, page } = await bootVendored({ head: shims.themeShim + shims.fontCss + shims.prefShim, server });
        try {
            const frames = await framesUntilSettled(page);
            assert.ok(wrongVisibleFrames(frames).length > 0, 'the frame check saw nothing wrong with the terminal unhidden');
        } finally {
            await context.close();
        }
    });
});

// --- 2. a real ttyd ---------------------------------------------------------------------

const noTtyd = why || ttydMissing();

describe('a pane paints nothing until its final font and size (real ttyd, phone)', { skip: noTtyd }, () => {
    let ttyd = null;
    after(() => ttyd && ttyd.stop());
    const shims = noTtyd ? {} : termShims();

    test('no visible frame before the viewer\'s 12px font, nor at the decoy size', async () => {
        ttyd ||= await startTtyd("printf 'boot\\r\\n'; exec sleep 120");
        const context = await browser.newContext(PHONE);
        try {
            await context.route('**/*', async route => {
                const url = new URL(route.request().url());
                if (url.pathname.startsWith('/static/fonts/')) return fulfillFont(route, url);
                if (url.pathname === '/') {
                    // term_http's order: theme shim, font CSS, boot CSS, preference shim.
                    const resp = await route.fetch();
                    const html = (await resp.text()).replace('</head>',
                        shims.themeShim + shims.fontCss + shims.bootCss + shims.prefShim + '</head>');
                    return route.fulfill({ response: resp, body: html });
                }
                return route.continue();
            });
            await context.addInitScript(bootInit, { prefSize: PREF_SIZE, hangFonts: false });
            const page = await context.newPage();
            await page.goto(ttyd.base + '/');
            const frames = await framesUntilSettled(page);
            const last = frames[frames.length - 1];
            assert.ok(last && last.visible, `the pane never became visible: ${JSON.stringify(last)}`);
            assert.deepEqual([last.size, last.fam], [PREF_SIZE, PREF_FAM]);
            assert.deepEqual(wrongVisibleFrames(frames), []);
            assert.ok(frames.some(f => !f.visible && f.size === DECOY), 'no hidden frame at the decoy size');
            assert.equal(frames.some(f => f.size === 14), false, 'ttyd\'s SET_PREFERENCES landed the server default 14px');
        } finally {
            await context.close();
        }
    });
});
