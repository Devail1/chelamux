// CMX-291 — the build/version badge (CMX-178's island) drew over the sidebar foot's
// resources strip: "Disk 42%" was unreadable under it (Liav, 2026-10-20). The build string
// lives in the Settings modal now, and the strip has the foot to itself.
//
// Measured by a real Chromium, because both halves are layout: after boot no badge is
// anywhere in the sidebar and the open Settings modal shows the build string; and at the
// sidebar's widths (264px on a desktop, the 82vw phone drawer at a 320px viewport) the
// strip's box meets no other foot element and no readout is clipped. /api/resources
// answers 100% for all three, the widest the strip can get.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { ASSET_VERSION, launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const FULL = { '/api/resources': { cpu_pct: 100, mem_pct: 100, disk_pct: 100 } };

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

async function waitForReadouts(page) {
    await page.waitForFunction(() => document.querySelectorAll('.sidebar-foot .res-item:not([hidden])').length === 3,
        null, { timeout: 15000 });
}

// The strip's box against every other drawn element in the foot (the island's slot
// included, wherever it is), and each readout against the strip and the foot's content box.
function footLayout(page) {
    return page.evaluate(() => {
        const box = e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
        const foot = document.querySelector('.sidebar-foot');
        const strip = document.getElementById('resources-strip');
        const fr = foot.getBoundingClientRect(), cs = getComputedStyle(foot);
        const others = [...foot.querySelectorAll('*')]
            .filter(e => !strip.contains(e) && !e.contains(strip) && !e.closest('svg'))
            .map(e => ({ id: e.id || e.className, ...box(e) }))
            .filter(b => b.w > 0 && b.h > 0);
        const items = [...strip.querySelectorAll('.res-item:not([hidden])')]
            .map(e => ({ id: e.id, ...box(e), clipped: e.scrollWidth > e.clientWidth }));
        return {
            strip: box(strip), stripClipped: strip.scrollWidth > strip.clientWidth, others, items,
            content: { left: fr.left + parseFloat(cs.paddingLeft), right: fr.right - parseFloat(cs.paddingRight) },
        };
    });
}

function meets(a, b) {
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

function assertStripAlone(l, where) {
    assert.ok(l.strip.w > 0 && l.strip.h > 0, `${where}: the resources strip has no layout box`);
    assert.equal(l.items.length, 3, `${where}: ${l.items.length} readouts drawn, not 3`);
    for (const o of l.others) {
        assert.ok(!meets(l.strip, o), `${where}: the resources strip ${JSON.stringify(l.strip)} meets ${o.id} ${JSON.stringify(o)}`);
    }
    assert.ok(!l.stripClipped, `${where}: the resources strip's content is wider than its box`);
    for (const it of l.items) {
        assert.ok(!it.clipped, `${where}: ${it.id} is clipped`);
        assert.ok(it.x >= l.strip.x - 0.5 && it.x + it.w <= l.strip.x + l.strip.w + 0.5,
            `${where}: ${it.id} ${JSON.stringify(it)} spills out of the strip ${JSON.stringify(l.strip)}`);
        assert.ok(it.x >= l.content.left - 0.5 && it.x + it.w <= l.content.right + 0.5,
            `${where}: ${it.id} ${JSON.stringify(it)} is outside the foot's content box ${JSON.stringify(l.content)}`);
    }
}

describe('CMX-291 desktop 1440×900: the build string is in Settings, not the sidebar', { skip: why }, () => {
    let page, context, errors;
    const ready = setup(async () => {
        ({ page, context, errors } = await openDashboard(browser, { width: 1440, height: 900, api: FULL }));
        await waitForReadouts(page);
        // Booted: the island has mounted and fetched /api/version by now.
        await page.locator('#version-island .version-badge').waitFor({ state: 'attached', timeout: 15000 });
    });
    after(() => context && context.close());

    test('after boot no version badge is anywhere in the sidebar', async () => {
        ready();
        assert.equal(await page.locator('aside.sidebar #version-island').count(), 0, 'the island slot is in the sidebar');
        assert.equal(await page.locator('aside.sidebar .version-badge').count(), 0, 'a version badge is in the sidebar');
        assert.doesNotMatch(await page.locator('aside.sidebar').innerText(), /build /, 'the sidebar shows a build string');
    });

    test('the open Settings modal shows the build string, selectable', async () => {
        ready();
        await page.evaluate(() => window.chela.toggleSettings());
        await page.waitForFunction(() => {
            const m = document.getElementById('settings-drawer');
            return m.classList.contains('open') && getComputedStyle(m).opacity === '1';
        }, null, { timeout: 5000 });
        const badge = page.locator('#settings-drawer #version-island .version-badge');
        assert.equal(await badge.count(), 1, 'the Settings modal has no version badge');
        assert.equal(await badge.textContent(), `build ${ASSET_VERSION}`);
        assert.ok(await badge.isVisible(), 'the version badge is not on screen in the open modal');
        const b = await badge.boundingBox(), m = await page.locator('#settings-drawer').boundingBox();
        assert.ok(b.y >= m.y && b.y + b.height <= m.y + m.height + 1, 'the badge is drawn outside the modal');
        assert.notEqual(await badge.evaluate(e => getComputedStyle(e).userSelect), 'none', 'the build string is not selectable');
        await page.evaluate(() => window.chela.toggleSettings());
    });

    test('at the 264px sidebar the resources strip meets no other foot element, nothing clipped', async () => {
        ready();
        const w = await page.evaluate(() => document.querySelector('aside.sidebar').getBoundingClientRect().width);
        assert.equal(Math.round(w), 264, `the sidebar is ${w}px wide, not 264`);
        assertStripAlone(await footLayout(page), 'desktop 264px');
        assert.deepEqual(errors, []);
    });
});

describe('CMX-291 phone 320×568: the narrowest drawer keeps the strip whole', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 320, height: 568, api: FULL, touch: true }));
        await waitForReadouts(page);
        await page.locator('#btn-menu-mobile').click();
        await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
            null, { timeout: 5000 });
    });
    after(() => context && context.close());

    test('the resources strip meets no other foot element, nothing clipped', async () => {
        ready();
        const w = await page.evaluate(() => document.querySelector('aside.sidebar').getBoundingClientRect().width);
        assert.ok(w < 264, `the drawer is ${w}px — not narrower than the desktop column, so this proves nothing new`);
        assertStripAlone(await footLayout(page), `phone drawer ${w}px`);
    });
});
