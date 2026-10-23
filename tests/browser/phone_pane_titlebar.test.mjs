// CMX-313 — the phone pane looks like the Claude app (Liav, 10-23).
//
// Before: a ☰ and a "+" floated in a row of their own above the pane, and the pane had a
// margin, a rounded border and a focus ring; its header ended in ⋮, maximize and ✕.
// After, on a phone only: ONE title bar, the pane's own header, with the drawer's
// hamburger on its left, the title over its subtitle, the status chip and ⋮ on the right.
// Close is a row of the ⋮ menu, there is no maximize, no "+" (New session is the drawer's
// button), and the pane's box is the viewport's width, from the top edge to the keybar.
//
// Guards (each corrupt→RED): the floating opener hides only while a pane is open (with no
// pane it is the only way into the drawer), the header's hamburger opens the drawer ABOVE
// the pane, Close reaches the kill confirm, the pane box is full-bleed with no border,
// radius or focus ring. ⭐ The desktop negative control: the same fixture at 1440×900
// keeps its window controls, its rounded single pane and no hamburger in a header.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
// Screenshots for a PR: CMX313_SHOTS=<dir> node --test tests/browser/phone_pane_titlebar.test.mjs
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const SHOTS = process.env.CMX313_SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => SHOTS && page.screenshot({ path: join(SHOTS, `${name}.png`) });

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

const PANE = '#term-stage.term-single .term-pane';
const settle = page => page.waitForSelector(`${PANE} .gs-head`, { timeout: 15000 });

/** What the eye sees: rendered, with a box, and the topmost element at its centre. */
function seen(page, sel) {
    return page.evaluate(s => {
        const el = document.querySelector(s);
        if (!el) return { exists: false, shown: false, onTop: false };
        const r = el.getBoundingClientRect();
        const shown = getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden' && r.width > 0 && r.height > 0;
        const top = shown ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) : null;
        return { exists: true, shown, onTop: !!top && (top === el || el.contains(top)) };
    }, sel);
}

function paneBox(page) {
    return page.evaluate(sel => {
        const pane = document.querySelector(sel);
        const r = pane.getBoundingClientRect();
        const cs = getComputedStyle(pane);
        const bar = document.getElementById('term-keybar')?.getBoundingClientRect();
        return {
            left: r.left, top: r.top, width: r.width, bottom: r.bottom,
            keybarTop: bar ? bar.top : null, vw: document.documentElement.clientWidth,
            border: cs.borderTopWidth, radius: cs.borderTopLeftRadius, marginLeft: cs.marginLeft,
            ring: getComputedStyle(pane, '::before').boxShadow, glow: cs.boxShadow,
        };
    }, PANE);
}

describe('CMX-313 390×844: the phone pane is the Claude app\'s full-bleed pane', { skip: why }, () => {
    let ctx, page;
    const ready = setup(async () => {
        ({ context: ctx, page } = await openDashboard(browser, { width: 390, height: 844, deviceScaleFactor: 2, touch: true }));
        await settle(page);
        await page.waitForTimeout(300);
        await shot(page, 'after-pane');
    });
    after(() => ctx && ctx.close());

    test('no floating ☰ and no "+" while the pane is open', async () => {
        ready();
        assert.equal((await seen(page, '.mobile-menu-fab')).shown, false, 'the floating ☰ still shows over the pane');
        assert.equal(await page.locator('#btn-new-mobile, .mobile-new-fab').count(), 0, 'the phone "+" is still on the page');
    });

    test('the hamburger is inside the pane\'s title bar, on its left', async () => {
        ready();
        const b = await page.evaluate(sel => {
            const head = document.querySelector(`${sel} .gs-head`);
            const ham = head.querySelector('.gs-head-menu');
            const label = head.querySelector('.gs-label');
            return ham && { ham: ham.getBoundingClientRect().right, label: label.getBoundingClientRect().left,
                headTop: head.getBoundingClientRect().top, aria: ham.getAttribute('aria-label') };
        }, PANE);
        assert.ok(b, 'no hamburger in the pane title bar');
        assert.equal(b.aria, 'Open menu');
        assert.ok(b.ham <= b.label, `the hamburger (right ${b.ham}) is not left of the title (left ${b.label})`);
        assert.equal((await seen(page, `${PANE} .gs-head-menu`)).onTop, true, 'the hamburger is covered');
    });

    test('the title bar has no ✕ and no maximize, and keeps the status and ⋮', async () => {
        ready();
        for (const sel of ['.gs-kill-btn', '.gs-max-btn', '.gs-min-btn', '.gs-mobile-full-btn']) {
            assert.equal(await page.locator(`${PANE} .gs-head ${sel}`).count(), 0, `${sel} is still in the phone title bar`);
        }
        assert.equal((await seen(page, `${PANE} .gs-status-chip .gs-dot`)).shown, true, 'the status indicator is gone');
        assert.equal((await seen(page, `${PANE} .gs-head .gs-menu-btn`)).shown, true, 'the ⋮ is gone');
        assert.equal((await seen(page, `${PANE} .gs-head .pane-subtitle`)).shown, true, 'the subtitle line is gone');
        const lines = await page.evaluate(sel => {
            const t = document.querySelector(`${sel} .pane-title`).getBoundingClientRect();
            const s = document.querySelector(`${sel} .pane-subtitle`).getBoundingClientRect();
            return { titleBottom: t.bottom, subTop: s.top };
        }, PANE);
        assert.ok(lines.subTop >= lines.titleBottom - 1, `the subtitle is not under the title: ${JSON.stringify(lines)}`);
    });

    test('the pane box is the viewport\'s width, from the top edge to the keybar, with no border, radius or ring', async () => {
        ready();
        // a real focus would draw the ring; force the class so the rule is what is measured
        await page.evaluate(sel => document.querySelector(sel).classList.add('term-focused', 'term-waiting'), PANE);
        await page.waitForTimeout(250);   // the ring's 0.15s transition
        const b = await paneBox(page);
        assert.equal(b.left, 0);
        assert.equal(b.top, 0);
        assert.equal(b.width, 390);
        assert.equal(b.width, b.vw, 'the pane is not the viewport\'s width');
        assert.ok(b.keybarTop !== null && Math.abs(b.bottom - b.keybarTop) <= 1, `the pane ends at ${b.bottom}, the keybar starts at ${b.keybarTop}`);
        assert.equal(b.border, '0px');
        assert.equal(b.radius, '0px');
        assert.equal(b.marginLeft, '0px');
        assert.equal(b.ring, 'none', `the focus ring still shows: ${b.ring}`);
        assert.equal(b.glow, 'none', `the waiting glow still shows: ${b.glow}`);
        await page.evaluate(sel => document.querySelector(sel).classList.remove('term-focused', 'term-waiting'), PANE);
    });

    test('a long UNSANDBOXED banner never squeezes the title out of the bar', async () => {
        ready();
        const w = await page.evaluate(sel => {
            const banner = document.querySelector(`${sel} .gs-unsafe-banner`);
            banner.hidden = false;
            banner.textContent = 'UNSANDBOXED · typing allowed for another 59 minutes';
            const title = document.querySelector(`${sel} .pane-title`).getBoundingClientRect();
            const end = document.querySelector(`${sel} .gs-head-end`).getBoundingClientRect();
            const out = { title: title.width, endRight: end.right };
            banner.hidden = true;
            banner.textContent = '';
            return out;
        }, PANE);
        assert.ok(w.title >= 100, `the title is squeezed to ${w.title}px`);
        assert.ok(w.endRight <= 390, `the bar's right side overflows the screen (${w.endRight}px)`);
    });

    test('Close is a row of the ⋮ menu and asks first', async () => {
        ready();
        await page.locator(`${PANE} .gs-menu-btn`).click();
        const row = page.locator('.pane-overflow-menu .gs-kill-item');
        await row.waitFor({ timeout: 5000 });
        assert.equal((await row.textContent()).trim(), 'Close session');
        await shot(page, 'after-menu');
        await row.click();
        const confirm = page.locator(`${PANE} .pane-kill-confirm`);
        await confirm.waitFor({ timeout: 5000 });
        assert.match(await confirm.textContent(), /^Kill /);
        await confirm.getByText('Cancel').click();
        assert.equal(await page.locator(`${PANE} .pane-kill-confirm`).count(), 0);
    });

    test('the hamburger opens the drawer over the pane, and New session is in it', async () => {
        ready();
        await page.locator(`${PANE} .gs-head-menu`).click();
        await page.waitForFunction(() => document.querySelector('.sidebar')?.classList.contains('open'), null, { timeout: 5000 });
        await page.waitForTimeout(300);   // the 0.18s slide
        assert.equal(await page.locator(`${PANE} .gs-head-menu`).getAttribute('aria-expanded'), 'true');
        assert.equal((await seen(page, '.sidebar-new-btn')).onTop, true, 'the drawer\'s New session is not on top of the pane');
        await shot(page, 'after-drawer');
        await page.locator('#sidebar-scrim').click({ position: { x: 370, y: 400 } });
        await page.waitForFunction(() => !document.querySelector('.sidebar')?.classList.contains('open'), null, { timeout: 5000 });
    });
});

describe('CMX-313 390×844 with no pane: the floating ☰ stays, the only way into the drawer', { skip: why }, () => {
    let ctx, page;
    const ready = setup(async () => {
        ({ context: ctx, page } = await openDashboard(browser, { width: 390, height: 844, touch: true, api: { '/api/agents': [] } }));
        await page.waitForFunction(() => document.getElementById('term-stage')?.textContent.includes('No active agents'), null, { timeout: 15000 });
    });
    after(() => ctx && ctx.close());

    test('the ☰ shows and is on top', async () => {
        ready();
        const fab = await seen(page, '.mobile-menu-fab');
        assert.equal(fab.shown, true, 'no way into the drawer without a pane');
        assert.equal(fab.onTop, true);
    });
});

describe('CMX-313 negative control, 1440×900: the desktop pane is unchanged', { skip: why }, () => {
    let wall, single;
    const ready = setup(async () => {
        wall = await openDashboard(browser, { width: 1440, height: 900 });
        await wall.page.waitForSelector('.grid-stack-item .gs-head .gs-kill-btn', { timeout: 15000 });
        single = await openDashboard(browser, { width: 1440, height: 900, storage: { pc_term_mode: 'single' } });
        await settle(single.page);
    });
    after(async () => { await wall?.context.close(); await single?.context.close(); });

    test('a wall tile keeps minimize, maximize and ✕, and no hamburger', async () => {
        ready();
        const tile = '.grid-stack-item[gs-id="@1"] .gs-head';
        for (const sel of ['.gs-min-btn', '.gs-max-btn', '.gs-kill-btn']) {
            assert.equal((await seen(wall.page, `${tile} ${sel}`)).shown, true, `${sel} is gone from a desktop tile`);
        }
        assert.equal(await wall.page.locator(`${tile} .gs-head-menu`).count(), 0);
        assert.equal(await wall.page.locator('.gs-head-bar').count(), 0);
    });

    test('the desktop ⋮ menu has no Close row', async () => {
        ready();
        await wall.page.locator('.grid-stack-item[gs-id="@1"] .gs-menu-btn').click();
        await wall.page.locator('.pane-overflow-menu').waitFor({ timeout: 5000 });
        assert.equal(await wall.page.locator('.pane-overflow-menu .gs-kill-item').count(), 0);
        await wall.page.keyboard.press('Escape');
    });

    test('the desktop single pane keeps its rounded, bordered box in the canvas and its controls', async () => {
        ready();
        const b = await paneBox(single.page);
        assert.equal(b.radius, '8px');
        assert.equal(b.border, '1px');
        assert.ok(b.left > 0, 'the desktop single pane went full-bleed');
        for (const sel of ['.gs-max-btn', '.gs-kill-btn']) {
            assert.equal((await seen(single.page, `${PANE} .gs-head ${sel}`)).shown, true, `${sel} is gone from the desktop single pane`);
        }
        assert.equal(await single.page.locator(`${PANE} .gs-head-menu`).count(), 0);
    });
});
