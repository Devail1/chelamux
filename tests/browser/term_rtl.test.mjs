// CMX-302 — Hebrew in a wall pane must read right to left, output and input box alike.
//
// Liav, iPhone Safari (2026-10-21): Hebrew in Claude's reply AND in the prompt box rendered
// letter-reversed. A terminal stores Hebrew in logical order (tmux's capture-pane shows it
// so), and xterm.js has no bidi of its own: the WebGL and canvas renderers paint each cell
// left to right, so a Hebrew run reads backwards. Only the DOM renderer leaves a row's
// text to the browser, whose bidi algorithm lays the run out right to left. ttyd defaults
// to WebGL; Hebrew read correctly only on a page that had lost its WebGL context (ttyd
// then disposes the addon and xterm falls back to DOM) until that page was reloaded.
// scripts/agent-terminals.sh now passes `--client-option rendererType=dom`.
//
// What this reads back: each character's on-screen x position (a DOM Range per char),
// sorted left to right. A canvas renderer has no DOM text at all, which fails as loudly
// as a reversed row.
//   1. The vendored xterm.js (no ttyd needed, runs in CI): the renderer ttyd is told to
//      use, with the font stack ttyd is told to use and the @font-face rules term_http
//      injects. Desktop and a phone-sized touch viewport.
//   2. A REAL ttyd launched with every --client-option agent-terminals.sh passes, its page
//      rewritten the way term_http rewrites it. Skipped when no ttyd is installed (CI has
//      none); negative control: drop rendererType=dom and this goes red.
// What it cannot: iOS WebKit itself (Playwright drives Chromium here). Liav confirms on
// the phone after the ttyds are respawned.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium } from './fixture.mjs';
import { VENDOR, fulfillFont, startTtyd, termShims, ttydClientOptions, ttydMissing } from './term_page.mjs';

// A pane's reply line, and Claude Code's prompt box with Hebrew typed into it.
const OUTPUT = 'abc שלום עולם xyz';
const INPUT = '│ > כתוב לי מכתב │';
// The same rows as a reader sees them left to right, spaces dropped: each Hebrew run
// mirrored as a whole (word order too), the Latin and box characters where they were.
const OUTPUT_SEEN = 'abcםלועםולשxyz';
const INPUT_SEEN = '│>בתכמילבותכ│';

const VIEWPORTS = [
    { name: 'desktop', viewport: { width: 1280, height: 400 } },
    { name: 'phone', viewport: { width: 390, height: 300 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true },
];

// The non-space characters of every xterm row on the page, each row left to right by
// where the browser actually drew it.
function seenRows() {
    return [...document.querySelectorAll('.xterm-rows > div')].map(row => {
        const cells = [];
        const walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
        for (let n = walk.nextNode(); n; n = walk.nextNode()) {
            for (let i = 0; i < n.data.length; i++) {
                if (!n.data[i].trim()) continue;
                const r = document.createRange();
                r.setStart(n, i);
                r.setEnd(n, i + 1);
                cells.push([n.data[i], r.getBoundingClientRect().left]);
            }
        }
        return cells.sort((a, b) => a[1] - b[1]).map(c => c[0]).join('');
    }).filter(Boolean);
}

async function settledRows(page) {
    // The font shim re-applies fonts on an interval and walks the size once; poll until
    // the expected rows appear or the budget runs out, then report what was drawn.
    let rows = [];
    for (let i = 0; i < 40; i++) {
        rows = await page.evaluate(seenRows);
        if (rows.includes(OUTPUT_SEEN) && rows.includes(INPUT_SEEN)) break;
        await page.waitForTimeout(250);
    }
    return rows;
}

const options = ttydClientOptions();
const { browser, why } = await launchChromium({
    // The real-ttyd page is served through request routing, and its WebSocket goes to
    // loopback; Chromium's local-network-access check would refuse that pairing.
    args: ['--disable-features=LocalNetworkAccessChecks'],
});
after(() => browser && browser.close());

const FIXTURE = 'http://chela.test';

describe('Hebrew in a wall pane renders right to left (vendored xterm.js)', { skip: why }, () => {
    const { fontCss } = why ? {} : termShims();
    for (const vp of VIEWPORTS) {
        test(`${vp.name}: output and input rows read in visual order`, async () => {
            const context = await browser.newContext(vp);
            try {
                await context.route('**/*', route => {
                    const url = new URL(route.request().url());
                    if (url.origin !== FIXTURE) return route.abort('blockedbyclient');
                    if (url.pathname.startsWith('/static/fonts/')) return fulfillFont(route, url);
                    if (url.pathname === '/vendor/xterm.js') return route.fulfill({ body: readFileSync(join(VENDOR, 'xterm.js')), contentType: 'text/javascript' });
                    if (url.pathname === '/vendor/xterm.css') return route.fulfill({ body: readFileSync(join(VENDOR, 'xterm.css')), contentType: 'text/css' });
                    return route.fulfill({
                        contentType: 'text/html',
                        body: '<!doctype html><html><head><meta charset="utf-8">'
                            + '<link rel="stylesheet" href="/vendor/xterm.css">' + fontCss
                            + '<script src="/vendor/xterm.js"></script></head>'
                            + '<body style="margin:0"><div id="t" style="width:100vw;height:100vh"></div></body></html>',
                    });
                });
                const page = await context.newPage();
                await page.goto(FIXTURE + '/');
                await page.evaluate(async ({ opts, lines }) => {
                    const size = Number(opts.fontSize);
                    const fams = opts.fontFamily.split(',').map(f => f.trim()).filter(f => f !== 'monospace');
                    await Promise.all(fams.map(f => document.fonts.load(`${size}px '${f}'`, 'aשׁ').catch(() => {})));
                    const term = new window.Terminal({ fontFamily: opts.fontFamily, fontSize: size, cols: 40, rows: 4 });
                    term.open(document.getElementById('t'));
                    // ttyd's setRendererType('dom') loads no addon: xterm keeps its DOM renderer.
                    await new Promise(res => term.write(lines.join('\r\n'), res));
                    await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
                }, { opts: options, lines: [OUTPUT, INPUT] });
                const rows = await settledRows(page);
                assert.equal(options.rendererType, 'dom',
                    `agent-terminals.sh launches ttyd with rendererType=${options.rendererType}: a canvas renderer has no bidi`);
                assert.deepEqual(rows.slice(0, 2), [OUTPUT_SEEN, INPUT_SEEN]);
            } finally {
                await context.close();
            }
        });
    }
});

// --- 2. a real ttyd -----------------------------------------------------------------

const noTtyd = why || ttydMissing();

describe('Hebrew in a wall pane renders right to left (real ttyd, agent-terminals.sh options)', { skip: noTtyd }, () => {
    let ttyd = null;
    after(() => ttyd && ttyd.stop());
    const shims = noTtyd ? {} : termShims();

    for (const vp of VIEWPORTS) {
        test(`${vp.name}: output and input rows read in visual order`, async () => {
            ttyd ||= await startTtyd(`printf '%s\\r\\n%s' '${OUTPUT}' '${INPUT}'; exec sleep 120`, options);
            const base = ttyd.base;
            const context = await browser.newContext(vp);
            try {
                await context.route('**/*', async route => {
                    const url = new URL(route.request().url());
                    if (url.pathname.startsWith('/static/fonts/')) return fulfillFont(route, url);
                    if (url.pathname === '/') {
                        // term_http: the font CSS + preference shim go in before </head>.
                        const resp = await route.fetch();
                        const html = (await resp.text()).replace('</head>', shims.fontCss + shims.prefShim + '</head>');
                        return route.fulfill({ response: resp, body: html });
                    }
                    return route.continue();
                });
                const page = await context.newPage();
                await page.goto(base + '/');
                const rows = await settledRows(page);
                assert.deepEqual(rows.slice(0, 2), [OUTPUT_SEEN, INPUT_SEEN],
                    'a ttyd launched with agent-terminals.sh options drew Hebrew left to right '
                    + '(no DOM rows at all means a canvas renderer)');
            } finally {
                await context.close();
            }
        });
    }
});
