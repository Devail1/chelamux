// CMX-242 — the tile footer and the Minimized tray, MEASURED in a real Chromium (jsdom has no
// layout engine: "visible", "covered" and "one row" are not DOM facts).
//
//  1. The footer (window id, model, context %) shows in EVERY pane mode: a wall tile, a
//     maximized one, the phone's single pane and that pane filling the screen (Liav
//     2026-10-19: he prefers it to the Claude Code status line).
//  2. The context fill strip along the footer's bottom edge is never under the focus ring:
//     the ring is a 2px inset overlay above the footer, so the strip sits inside it, on the
//     focused pane and on an unfocused one. (Geometry, not elementFromPoint: the ring is a
//     pointer-events:none ::before and the strip is pointer-events:none too, so hit-testing
//     can see neither.)
//  3. Ten minimized panes keep the tray ONE row: as tall as a tray with one chip, its chips
//     scroll sideways (scrollWidth > clientWidth), a vertical wheel scrolls them, and a fade
//     marks the edge that hides more.
//
// Harness: tests/browser/fixture.mjs — never a live dashboard or daemon.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

const wids = n => Array.from({ length: n }, (_, i) => `@${i + 1}`);
const fleet = n => wids(n).map(wid => ({
    name: `agent-${wid.slice(1)}`, window_id: wid, online: true, session_status: 'idle', cwd: '/p/x',
}));
const context = n => wids(n).map(wid => ({
    window_id: wid, used_pct: 42, used: '84K', total: '200K', model: 'opus-5', cost_usd: 1.5, branch: 'cmx-242',
}));
const api = n => ({ '/api/agents': fleet(n), '/api/agents/context': context(n), '/api/summary': { windows_total: n } });

// The footer of `wid`'s pane, as a user sees it: shown, on screen, and saying what it says.
const footer = (page, wid) => page.evaluate(w => {
    const bar = document.querySelector(`.term-ctx-bar[data-ctx-for="${w}"]`);
    if (!bar) return null;
    const r = bar.getBoundingClientRect();
    const text = sel => {
        const el = bar.querySelector(sel);
        return el && !el.hidden && el.getClientRects().length ? el.textContent.trim() : null;
    };
    const cs = getComputedStyle(bar);
    return {
        shown: cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0,
        onScreen: r.top >= 0 && r.bottom <= innerHeight + 0.5 && r.left >= 0 && r.right <= innerWidth + 0.5,
        wid: text('.gs-wid'), model: text('.gs-model'), ctx: text('.gs-ctx'),
    };
}, wid);

function assertFooter(f, wid, mode) {
    assert.ok(f, `${mode}: ${wid} has no footer`);
    assert.ok(f.shown, `${mode}: ${wid}'s footer is hidden`);
    assert.ok(f.onScreen, `${mode}: ${wid}'s footer is off screen`);
    assert.equal(f.wid, wid, `${mode}: the footer does not show the window id`);
    assert.equal(f.model, 'opus-5', `${mode}: the footer does not show the model`);
    assert.equal(f.ctx, '42% · 84K/200K', `${mode}: the footer does not show the context %`);
}

const settle = page => page.waitForFunction(() => document.querySelectorAll('.term-ctx-bar .gs-ctx:not([hidden])').length > 0,
    null, { timeout: 15000 });

describe('CMX-242 1440×900: the footer shows on a wall tile and on a MAXIMIZED one', { skip: why }, () => {
    let ctx, page, normal, maxed;
    const ready = setup(async () => {
        ({ context: ctx, page } = await openDashboard(browser, { width: 1440, height: 900, api: api(4) }));
        await settle(page);
        normal = await footer(page, '@1');
        await page.click('.grid-stack-item[gs-id="@1"] .gs-max-btn');
        await page.waitForFunction(() => document.body.classList.contains('pane-is-maximized'), null, { timeout: 5000 });
        maxed = await footer(page, '@1');
    });
    after(() => ctx && ctx.close());

    test('a wall tile', () => { ready(); assertFooter(normal, '@1', 'wall tile'); });
    test('the same tile maximized', () => { ready(); assertFooter(maxed, '@1', 'maximized'); });
});

describe('CMX-242 390×844: the phone pane shows its footer, filling the screen too', { skip: why }, () => {
    let ctx, page, single, full;
    const ready = setup(async () => {
        ({ context: ctx, page } = await openDashboard(browser, { width: 390, height: 844, api: api(2), touch: true }));
        await settle(page);
        const wid = await page.$eval('#term-stage .term-pane .term-ctx-bar', b => b.dataset.ctxFor);
        single = { wid, f: await footer(page, wid) };
        await page.click('#term-stage .term-pane .gs-mobile-full-btn');
        await page.waitForFunction(() => document.body.classList.contains('term-mobile-full'), null, { timeout: 5000 });
        full = await footer(page, wid);
    });
    after(() => ctx && ctx.close());

    test('the single pane', () => { ready(); assertFooter(single.f, single.wid, 'phone'); });
    test('the single pane filling the screen', () => { ready(); assertFooter(full, single.wid, 'phone-full'); });
});

describe('CMX-242 1440×900: the focus ring never covers the context fill', { skip: why }, () => {
    let ctx, page, boxes;
    const ready = setup(async () => {
        ({ context: ctx, page } = await openDashboard(browser, { width: 1440, height: 900, api: api(4) }));
        await settle(page);
        // A real focus: click into @1's terminal; the focus ring follows it (terminals.js's poll).
        await page.click('.grid-stack-item[gs-id="@1"] iframe.term-frame');
        await page.waitForFunction(() => document.querySelector('.grid-stack-item[gs-id="@1"] .grid-stack-item-content.term-focused'),
            null, { timeout: 5000 });
        // the ring fades in (a 0.15s box-shadow transition): measure it drawn
        await page.waitForFunction(() => /\b2px\b/.test(getComputedStyle(
            document.querySelector('.grid-stack-item[gs-id="@1"] .grid-stack-item-content'), '::before').boxShadow), null, { timeout: 5000 });
        boxes = await page.evaluate(() => {
            // the ring is the ::before overlay's inset box-shadow; its spread is its width
            const ringOf = el => Math.max(0, ...(getComputedStyle(el, '::before').boxShadow.match(/-?[\d.]+px/g) || []).map(parseFloat));
            return ['@1', '@2'].map(wid => {
                const content = document.querySelector(`.grid-stack-item[gs-id="${wid}"] .grid-stack-item-content`);
                const fill = content.querySelector('.term-ctx-fill').getBoundingClientRect();
                const c = content.getBoundingClientRect();
                return { wid, focused: content.classList.contains('term-focused'), ring: ringOf(content),
                    fill: { left: fill.left, right: fill.right, top: fill.top, bottom: fill.bottom, width: fill.width },
                    box: { left: c.left, right: c.right, bottom: c.bottom } };
            });
        });
    });
    after(() => ctx && ctx.close());

    test('the focused pane wears the 2px ring, the other none', () => {
        ready();
        const [f, u] = boxes;
        assert.ok(f.focused && f.ring === 2, `@1 is not the 2px-ringed focused pane: ${JSON.stringify(f)}`);
        assert.ok(!u.focused, '@2 is focused too');
    });

    for (const i of [0, 1]) {
        test(`${i ? 'unfocused' : 'focused'} pane: the fill strip lies inside the ring's band`, () => {
            ready();
            const ring = boxes[0].ring;   // the focused ring's width: what would cover the strip
            const { wid, fill, box } = boxes[i];
            assert.ok(fill.width > 0, `${wid}: the fill has no width, the check is vacuous`);
            assert.ok(fill.bottom <= box.bottom - ring + 0.5, `${wid}: the fill's bottom ${fill.bottom} is under the ring (pane bottom ${box.bottom}, ring ${ring}px)`);
            assert.ok(fill.left >= box.left + ring - 0.5, `${wid}: the fill starts under the ring's left edge`);
            assert.ok(fill.right <= box.right - ring + 0.5, `${wid}: the fill runs under the ring's right edge`);
        });
    }
});

// The tray of `n` minimized panes (of 12 on the Wall).
async function tray(n) {
    const { context: ctx, page } = await openDashboard(browser, {
        width: 1440, height: 900, api: api(12),
        storage: { pc_wall_preset: JSON.stringify({ cols: 2, rows: 1 }), pc_wall_minimized: JSON.stringify(wids(n)) },
    });
    await page.waitForFunction(k => document.querySelectorAll('#term-min-dock .min-chip').length === k, n, { timeout: 15000 });
    const m = () => page.evaluate(() => {
        const dock = document.getElementById('term-min-dock');
        const strip = dock.querySelector('.min-dock-chips');
        return {
            h: dock.getBoundingClientRect().height, label: dock.querySelector('.min-dock-label').textContent,
            scrollW: strip.scrollWidth, clientW: strip.clientWidth, left: strip.scrollLeft, cls: strip.className,
        };
    });
    return { ctx, page, m };
}

describe('CMX-242 1440×900: ten minimized panes keep the tray ONE row that scrolls sideways', { skip: why }, () => {
    let one, ten, first, wheeled;
    const ready = setup(async () => {
        one = await tray(1);
        ten = await tray(10);
        first = await ten.m();
        await ten.page.hover('#term-min-dock .min-dock-chips');
        await ten.page.mouse.wheel(0, 300);   // a VERTICAL wheel
        await ten.page.waitForFunction(() => document.querySelector('#term-min-dock .min-dock-chips').scrollLeft > 0,
            null, { timeout: 5000 });
        wheeled = await ten.m();
    });
    after(async () => { await one?.ctx.close(); await ten?.ctx.close(); });

    test('the tray is exactly as tall with ten chips as with one', async () => {
        ready();
        const single = await one.m();
        assert.equal(first.h, single.h, `ten chips: ${first.h}px, one chip: ${single.h}px`);
    });

    test('the chips overflow sideways instead of wrapping', () => {
        ready();
        assert.ok(first.scrollW > first.clientW, `no horizontal overflow (${first.scrollW} <= ${first.clientW})`);
    });

    test('the label counts them', () => { ready(); assert.equal(first.label, 'Minimized (10)'); });

    test('a fade marks the hidden end, and a vertical wheel scrolls the row sideways', () => {
        ready();
        assert.match(first.cls, /\bfade-end\b/);
        assert.doesNotMatch(first.cls, /\bfade-start\b/, 'nothing is hidden at the start yet');
        assert.ok(wheeled.left > 0, 'the wheel did not scroll the tray');
        assert.match(wheeled.cls, /\bfade-start\b/, 'scrolled: the start hides chips now');
    });
});
