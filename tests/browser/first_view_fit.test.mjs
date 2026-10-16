// CMX-154 — phone: on first view the terminal is taller than its tile.
//
// Liav, iPhone Safari (2026-10-16), after CMX-102 shipped: keyboard open, a multi-line
// prompt showed only its first 2 lines. The PANE was right — its footer (chela's
// context bar) sat on the keybar — but the terminal INSIDE it kept more rows than its
// box, so the rest of the prompt and Claude's status line were clipped below the
// footer until a scroll re-fitted it. The tile's box changed; xterm did not follow.
//
// The terminal stub here models that iPhone behaviour: like ttyd it fits its rows to
// the iframe when it loads (xterm's fit addon: floor(height / row height)) and exposes
// `window.term.fit()`, but it does NOT re-fit on its own `resize` — the iframe's own
// event is exactly what Safari failed to act on. So every fit below is the
// dashboard's doing. The keyboard is a stand-in visualViewport (headless Chromium has
// none), shrunk by a 336px keyboard.
//
// Guards (each corrupt→RED): first view and tile switch with no resize event, the keyboard
// opening after load or after a switch, and ⭐ the last row ≤ the tile FOOTER's top.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
// Screenshots for a PR: CMX154_SHOTS=<dir> pnpm exec node --test tests/browser/first_view_fit.test.mjs
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const SHOTS = process.env.CMX154_SHOTS || '';
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

const TERM_STUB = `<!doctype html><html><head><style>
html,body{margin:0;height:100%;background:#0d1117;color:#c9d1d9;font:13px/17px monospace;overflow:hidden}
.row{height:17px;white-space:pre;overflow:hidden}
</style></head><body><div id="rows"></div><script>
const TAIL = ['─'.repeat(60), '❯ anything else that needs to be moved from',
  '  the backlog to todo? or anything else to', '  pick up next — line three of the input',
  '  and line four, the one that was hidden', '', '@0 Opus 5.5  dev  18% · 180K/1M'];
function lay() {
  const n = Math.floor(window.innerHeight / 17);
  const rows = [];
  for (let i = 0; i < n; i++) {
    const t = n - TAIL.length;
    rows.push('<div class="row">' + (i >= t ? TAIL[i - t] : (i < t - 1 ? 'scrollback line ' + i : '')) + '</div>');
  }
  document.getElementById('rows').innerHTML = rows.join('');
}
window.term = { fit: lay };
lay();
try { parent.__termBooted(); } catch (e) {}
</script></body></html>`;

// Installed before any page script. `open` = keyboard height at load (no event fired);
// `window.__kbAtBoot` = a keyboard that comes up SILENTLY as a terminal boots (after it
// laid out its rows, before its iframe's load event), as iOS does on first view.
function fakeVisualViewport(open) {
    const vv = new EventTarget();
    window.__termBooted = () => { if (window.__kbAtBoot) { kb = window.__kbAtBoot; window.__kbAtBoot = 0; } };
    vv.offsetTop = 0; vv.offsetLeft = 0; vv.pageTop = 0; vv.pageLeft = 0; vv.scale = 1;
    Object.defineProperty(vv, 'width', { get: () => window.innerWidth });
    let kb = open;
    Object.defineProperty(vv, 'height', { get: () => window.innerHeight - kb });
    window.__setKeyboard = h => { kb = h; vv.dispatchEvent(new Event('resize')); };
    Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => vv });
}

async function measure(page) {
    return page.evaluate(() => {
        const pane = document.querySelector('#term-stage .term-pane');
        const ifr = pane.querySelector('iframe.term-frame');
        const rows = ifr.contentDocument.querySelectorAll('.row');
        const last = rows[rows.length - 1];
        const top = ifr.getBoundingClientRect().top + ifr.clientTop;
        const canvas = document.querySelector('.canvas');
        const se = document.scrollingElement;
        return {
            wid: document.getElementById('term-agent').value,
            rows: rows.length,
            text: Array.from(rows).slice(-7).map(r => r.textContent),
            lastRowBottom: top + last.getBoundingClientRect().bottom,
            frameBottom: ifr.getBoundingClientRect().bottom,
            footerTop: pane.querySelector('.term-ctx-bar').getBoundingClientRect().top,
            barTop: document.getElementById('term-keybar').getBoundingClientRect().top,
            paneHeight: pane.getBoundingClientRect().height,
            scroll: Math.max(se.scrollHeight - se.clientHeight, canvas.scrollHeight - canvas.clientHeight),
        };
    });
}

async function settle(page) {
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50)))));
}

async function framed(page) {
    await page.waitForFunction(() => {
        const f = document.querySelector('#term-stage .term-pane iframe.term-frame');
        return f && f.contentDocument && f.contentDocument.querySelectorAll('.row').length > 0;
    }, null, { timeout: 10000 });
    await settle(page);
}

async function shot(page, name, kb) {
    if (!SHOTS) return;
    if (kb) await page.evaluate(h => {
        const d = document.createElement('div');
        d.id = 'cmx154-fake-kb';
        d.textContent = 'simulated on-screen keyboard (' + h + 'px)';
        d.style.cssText = 'position:fixed;left:0;right:0;bottom:0;height:' + h + 'px;background:#3a3a3c;color:#aaa;display:flex;align-items:center;justify-content:center;font:14px system-ui;z-index:9999';
        document.body.appendChild(d);
    }, kb);
    await page.screenshot({ path: join(SHOTS, name) });
    if (kb) await page.evaluate(() => document.getElementById('cmx154-fake-kb').remove());
}

async function openPhone(kbAtLoad, kbAtBoot = 0) {
    const { context, page, errors } = await openDashboard(browser, { width: W, height: H, touch: true });
    await page.addInitScript(fakeVisualViewport, kbAtLoad);
    await page.addInitScript(h => { window.__kbAtBoot = h; }, kbAtBoot);
    await context.route(u => new URL(u).pathname.startsWith('/term/'), route => route.fulfill({ status: 200, contentType: 'text/html', body: TERM_STUB }));
    await page.reload();
    await framed(page);
    return { context, page, errors };
}

function assertFullyVisible(m, what, { scrolls = false } = {}) {
    assert.ok(m.rows > 7, `${what}: the terminal must still show rows (got ${m.rows})`);
    assert.match(m.text[6], /Opus 5\.5/, `${what}: the last row is Claude's status line`);
    assert.match(m.text[4], /line four/, `${what}: the 4th prompt line is on screen`);
    assert.ok(m.lastRowBottom <= m.footerTop + px,
        `${what}: last row bottom ${m.lastRowBottom} > tile footer top ${m.footerTop} — the terminal is taller than its tile`);
    assert.ok(m.footerTop <= m.barTop + px, `${what}: footer top ${m.footerTop} > keybar top ${m.barTop}`);
    assert.ok(m.frameBottom <= m.footerTop + px, `${what}: frame bottom ${m.frameBottom} runs under the footer (${m.footerTop})`);
    if (!scrolls) assert.ok(m.scroll <= 0, `${what}: the page scrolls by ${m.scroll}px`);
}

describe('CMX-154: phone — the terminal fits its tile on first view and on a tile switch', { skip: why }, () => {
    describe('keyboard comes up as the terminal boots, no resize event fired', () => {
        let ctx, page, errors, first, switched;
        const ready = setup(async () => {
            ({ context: ctx, page, errors } = await openPhone(0, KB));
            first = await measure(page);
            await shot(page, 'cmx154-390-first-view-keyboard-open.png', KB);
            await page.evaluate(kb => { window.__setKeyboard(0); window.__kbAtBoot = kb; }, KB);
            await settle(page);
            await page.click('.term-pill:not(.active)');
            await framed(page);
            switched = await measure(page);
        });
        after(() => ctx && ctx.close());

        test('⭐ first view: the 4-line prompt and the status line are fully visible', () => {
            ready();
            assertFullyVisible(first, 'first view');
        });
        test('⭐ tile switch: the new pane\'s terminal fits its tile too', () => {
            ready();
            assert.notEqual(switched.wid, first.wid, 'the pill tap switched panes');
            assertFullyVisible(switched, 'after a tile switch');
            assert.deepEqual(errors, []);
        });
    });

    describe('keyboard opens after load (Liav\'s screenshot)', () => {
        let ctx, page, errors, closed, open, switchedOpen;
        const ready = setup(async () => {
            ({ context: ctx, page, errors } = await openPhone(0));
            closed = await measure(page);
            await page.evaluate(kb => window.__setKeyboard(kb), KB);
            await settle(page);
            open = await measure(page);
            await shot(page, 'cmx154-390-keyboard-opened-4-line-input.png', KB);
            // Tile switch with the keyboard down, then the keyboard opens on the NEW pane.
            await page.evaluate(() => window.__setKeyboard(0));
            await settle(page);
            await page.click('.term-pill:not(.active)');
            await framed(page);
            await page.evaluate(kb => window.__setKeyboard(kb), KB);
            await settle(page);
            switchedOpen = await measure(page);
        });
        after(() => ctx && ctx.close());

        test('keyboard closed: the unchanged calc() layout, last row visible', () => {
            ready();
            assert.equal(closed.paneHeight, H - 32 - 38 - 47 - 30, 'the closed pane height is the unchanged calc()');
            // The closed layout is CMX-102's calc(), deliberately untouched — it leaves the
            // phone scroller a few px of slack, so only row visibility is asserted here.
            assertFullyVisible(closed, 'keyboard closed', { scrolls: true });
        });
        test('⭐ keyboard opened: the terminal re-fits to the shrunk tile', () => {
            ready();
            assert.ok(open.rows < closed.rows, `rows must drop when the tile shrinks (${closed.rows} → ${open.rows})`);
            assertFullyVisible(open, 'keyboard opened');
        });
        test('⭐ tile switch, then the keyboard opens: the new pane re-fits', () => {
            ready();
            assertFullyVisible(switchedOpen, 'switched, then keyboard opened');
            assert.deepEqual(errors, []);
        });
    });
});
