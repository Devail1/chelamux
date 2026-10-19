// CMX-102 — phone, keyboard open: the keybar covered the terminal's bottom rows.
//
// Liav, iPhone Safari (2026-10-12): typing a multi-line prompt into an agent showed
// only its first two lines; Claude's status line was cut in half by the keybar, and
// the rest appeared only after scrolling the page. The pane was taller than the box
// left between its top and the keybar.
//
// What a real Chromium can measure (jsdom has no layout): at 390×844, as a touch
// device, with the on-screen keyboard SIMULATED — headless Chromium has none, so
// window.visualViewport is replaced before the page's scripts run by a stand-in whose
// height shrinks by a 336px keyboard (an iPhone's, accessory row included) and which
// fires `resize` like the real one does. Then:
//   - the pane's bottom edge sits exactly on the keybar's top edge;
//   - the LAST terminal row's bottom is ≤ the keybar's top. The ttyd iframe is a stub
//     that lays out monospace rows the way xterm's fit addon does (floor(height / row
//     height) rows, the last one Claude's status line) and re-lays them on resize;
//   - neither the document nor the phone's scroller (.canvas) can scroll vertically;
//   - keyboard closed: the override is gone (the CSS calc() is back in charge).
// What it CANNOT: iOS's own viewport panning and dvh/innerHeight disagreement. Liav
// confirms those on the phone after deploy.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
// Screenshots for a PR: CMX102_SHOTS=<dir> pnpm exec node --test tests/browser/keybar_pane_fit.test.mjs
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const SHOTS = process.env.CMX102_SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const W = 390;
const H = 844;
const KB = 336;
const px = 1;

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// A stand-in for ttyd's xterm: rows of 17px monospace text, as many as FIT the
// iframe (xterm's fit addon floors height / row height), re-laid on resize. The
// bottom rows are a 4-line Claude prompt and its status line — what Liav was typing.
const TERM_STUB = `<!doctype html><html><head><style>
html,body{margin:0;height:100%;background:#0d1117;color:#c9d1d9;font:13px/17px monospace;overflow:hidden}
.row{height:17px;white-space:pre;overflow:hidden}
</style></head><body><div id="rows"></div><script>
const TAIL = ['─'.repeat(60), '❯ ok, one more thing that has been bugging me',
  '  for a while, is when im writing multiline', '  the later lines are hidden until I scroll',
  '  — line four of the input', '', '@2 Opus 5.5  master  18% · 180K/1M'];
function lay() {
  const n = Math.floor(window.innerHeight / 17);
  const rows = [];
  for (let i = 0; i < n; i++) {
    const t = n - TAIL.length;
    rows.push('<div class="row">' + (i >= t ? TAIL[i - t] : (i < t - 1 ? 'scrollback line ' + i : '')) + '</div>');
  }
  document.getElementById('rows').innerHTML = rows.join('');
}
addEventListener('resize', lay); lay();
</script></body></html>`;

// Installed before any page script: terminals.js binds to visualViewport at load.
function fakeVisualViewport() {
    const vv = new EventTarget();
    vv.offsetTop = 0; vv.offsetLeft = 0; vv.pageTop = 0; vv.pageLeft = 0; vv.scale = 1;
    Object.defineProperty(vv, 'width', { get: () => window.innerWidth });
    let kb = 0;
    Object.defineProperty(vv, 'height', { get: () => window.innerHeight - kb });
    window.__setKeyboard = h => { kb = h; vv.dispatchEvent(new Event('resize')); };
    Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => vv });
}

async function openPhone() {
    const { context, page, errors } = await openDashboard(browser, { width: W, height: H, touch: true });
    return { context, page, errors };
}

async function measure(page) {
    return page.evaluate(() => {
        const pane = document.querySelector('#term-stage .term-pane');
        const ifr = pane && pane.querySelector('iframe.term-frame');
        const bar = document.getElementById('term-keybar');
        const rows = ifr && ifr.contentDocument ? ifr.contentDocument.querySelectorAll('.row') : [];
        const last = rows.length ? rows[rows.length - 1] : null;
        const canvas = document.querySelector('.canvas');
        const se = document.scrollingElement;
        return {
            paneBottom: pane ? pane.getBoundingClientRect().bottom : null,
            paneHeight: pane ? pane.getBoundingClientRect().height : null,
            frameBottom: ifr ? ifr.getBoundingClientRect().bottom : null,
            lastRowBottom: last ? ifr.getBoundingClientRect().top + ifr.clientTop + last.getBoundingClientRect().bottom : null,
            lastRowText: last ? last.textContent : null,
            rows: rows.length,
            barTop: bar.getBoundingClientRect().top,
            kbFit: document.documentElement.classList.contains('kb-fit'),
            docScroll: se.scrollHeight - se.clientHeight,
            canvasScroll: canvas.scrollHeight - canvas.clientHeight,
        };
    });
}

async function settle(page) {
    // two frames: the fit, then the iframe's own resize → re-lay of its rows
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50)))));
}

// Two layouts. `as-built`: the dashboard exactly as served. `unlisted chrome`: 40px
// more chrome above the pane than the CSS calc() knows about — what the iPhone had
// (there the pane overshot the keybar by ~40px: the context bar plus half of Claude's
// status line; here, as-built, the calc only misses by ~10px, which lands in the
// context bar and so never reaches a terminal row). The fit is measured, so it must
// hold for both; the calc-only layout fails the second.
const VARIANTS = [
    { name: 'as-built', css: '', tag: '' },
    { name: 'unlisted chrome (+40px above the pane)', css: '.term-toolbar { padding-top: 40px !important; }', tag: '-extra-chrome' },
];

for (const v of VARIANTS) describe(`CMX-102 [${v.name}]: phone keyboard open — the pane ends at the keybar, its last row visible`, { skip: why }, () => {
    let ctx, page, errors, closed, open, reclosed;
    const ready = setup(async () => {
        ({ context: ctx, page, errors } = await openPhone());
        await page.addInitScript(fakeVisualViewport);
        // The stub terminal: registered after the fixture's routes, so it wins for /term/.
        await ctx.route(u => new URL(u).pathname.startsWith('/term/'), route => route.fulfill({ status: 200, contentType: 'text/html', body: TERM_STUB }));
        await page.reload();
        if (v.css) await page.addStyleTag({ content: v.css });
        await page.waitForSelector('#term-stage.term-single .term-pane iframe.term-frame', { timeout: 10000 });
        await page.waitForFunction(() => {
            const f = document.querySelector('#term-stage .term-pane iframe.term-frame');
            return f && f.contentDocument && f.contentDocument.querySelectorAll('.row').length > 0;
        }, null, { timeout: 10000 });
        await settle(page);
        closed = await measure(page);
        if (SHOTS) await page.screenshot({ path: join(SHOTS, `cmx102-390-keyboard-closed${v.tag}.png`) });
        await page.evaluate(kb => window.__setKeyboard(kb), KB);
        await settle(page);
        open = await measure(page);
        if (SHOTS) {
            // Paint a stand-in keyboard where the real one would be, for the PR screenshot only.
            await page.evaluate(kb => {
                const d = document.createElement('div');
                d.id = 'cmx102-fake-kb';
                d.textContent = 'simulated on-screen keyboard (' + kb + 'px)';
                d.style.cssText = 'position:fixed;left:0;right:0;bottom:0;height:' + kb + 'px;background:#3a3a3c;color:#aaa;display:flex;align-items:center;justify-content:center;font:14px system-ui;z-index:9999';
                document.body.appendChild(d);
            }, KB);
            await page.screenshot({ path: join(SHOTS, `cmx102-390-keyboard-open-4-line-input${v.tag}.png`) });
            await page.evaluate(() => document.getElementById('cmx102-fake-kb').remove());
        }
        await page.evaluate(() => window.__setKeyboard(0));
        await settle(page);
        reclosed = await measure(page);
    });
    after(() => ctx && ctx.close());

    test('keyboard open: the pane\'s bottom edge is the keybar\'s top edge', () => {
        ready();
        assert.ok(open.kbFit, 'html.kb-fit must be set while the keyboard is up');
        assert.ok(Math.abs(open.barTop - (H - KB - 47)) <= 2 * px, `keybar pinned above the keyboard (top ${open.barTop})`);
        assert.ok(Math.abs(open.paneBottom - open.barTop) <= px,
            `pane bottom ${open.paneBottom} must sit on the keybar top ${open.barTop}`);
    });

    test('⭐ keyboard open: the last terminal row (Claude\'s status line) is fully above the keybar', () => {
        ready();
        assert.ok(open.rows > 4, `the terminal must still show rows (got ${open.rows})`);
        assert.match(open.lastRowText, /Opus 5\.5/, 'the last row is the status line');
        assert.ok(open.frameBottom <= open.barTop + px, `iframe bottom ${open.frameBottom} > keybar top ${open.barTop}`);
        assert.ok(open.lastRowBottom <= open.barTop + px,
            `last row bottom ${open.lastRowBottom} > keybar top ${open.barTop}: the bottom row is behind the keybar`);
    });

    test('keyboard open: the page cannot scroll vertically', () => {
        ready();
        assert.ok(open.docScroll <= 0, `document scrolls by ${open.docScroll}px`);
        assert.ok(open.canvasScroll <= 0, `.canvas scrolls by ${open.canvasScroll}px`);
    });

    test('keyboard closed: no override, same layout before and after', () => {
        ready();
        assert.ok(!closed.kbFit && !reclosed.kbFit, 'html.kb-fit must be absent with the keyboard closed');
        assert.equal(reclosed.paneBottom, closed.paneBottom, 'closing the keyboard restores the closed layout');
        // Closed = the CSS calc(), byte-for-byte what shipped before CMX-102: 100dvh minus
        // the listed chrome (32 + 47 + 30; no safe-area inset in headless Chromium). CMX-202
        // dropped the pill strip's 38px from that list.
        assert.equal(closed.paneHeight, H - 32 - 47 - 30, 'the closed pane height is the calc()');
        assert.deepEqual(errors, []);
    });
});
