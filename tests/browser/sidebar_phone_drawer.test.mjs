// CMX-404 — on a real iPhone the sidebar drawer's foot (with the Settings/menu
// button) sat behind Safari's bottom toolbar, and the New session + Jump row was
// missing: Settings was unreachable on the phone.
//
// What a real Chromium can measure here:
//   - a persisted DESKTOP collapse (`chela_sidebar_collapsed`, body.sidebar-collapsed)
//     must not reach into the open phone drawer: .sidebar-quick and its New session
//     button are drawn, on screen, hit-testable;
//   - at 390×664 — 844 minus the ~180px Safari's toolbars eat — the drawer's foot
//     and its menu button sit wholly inside the viewport;
//   - the accepted desktop case: at 1440×900 the same flag still hides .sidebar-quick
//     (that is the icon rail).
// What it CANNOT: headless Chromium has no browser toolbar, so 100vh == 100dvh here
// and a `.sidebar { height: 100vh }` regression is invisible to it. That half is
// held on the source by tests/sidebar_phone_css.test.mjs.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const px = 1;
const COLLAPSED = { chela_sidebar_collapsed: '1' };

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

async function openDrawer(page) {
    await page.locator('#btn-menu-mobile').click();
    await page.waitForFunction(() => {
        const r = document.querySelector('.sidebar').getBoundingClientRect();
        return r.left >= -1 && r.width > 0 && r.left < window.innerWidth;
    }, null, { timeout: 5000 });
    // The drawer's transform transition (0.18s) — wait for it to finish.
    await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
        null, { timeout: 5000 });
}

// Drawn, inside the viewport, and what the browser hit-tests at its centre.
async function assertOnScreen(page, selector, vw, vh) {
    const loc = page.locator(selector).first();
    assert.ok(await loc.isVisible(), `${selector} is not visible`);
    const b = await loc.boundingBox();
    assert.ok(b && b.width > 0 && b.height > 0, `${selector} has no layout box`);
    assert.ok(b.x >= -px && b.y >= -px && b.x + b.width <= vw + px && b.y + b.height <= vh + px,
        `${selector} ${JSON.stringify(b)} is not inside the ${vw}×${vh} viewport`);
    const hit = await page.evaluate(([sel, x, y]) => {
        const el = document.elementFromPoint(x, y);
        return !!(el && el.closest(sel));
    }, [selector, b.x + b.width / 2, b.y + b.height / 2]);
    assert.ok(hit, `${selector} is covered or not hit-testable at its centre`);
}

for (const [width, height] of [[390, 844], [390, 664]]) {
    describe(`CMX-404 phone ${width}×${height}, desktop collapse persisted: the open drawer is whole`, { skip: why }, () => {
        let page, context;
        const ready = setup(async () => {
            ({ page, context } = await openDashboard(browser, { width, height, storage: COLLAPSED }));
            await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
            await openDrawer(page);
        });
        after(() => context && context.close());

        test('the persisted flag really is on the body (else this proves nothing)', async () => {
            ready();
            assert.ok(await page.evaluate(() => document.body.classList.contains('sidebar-collapsed')));
        });

        test('.sidebar-quick is displayed and the New session button is on screen', async () => {
            ready();
            const display = await page.evaluate(() => getComputedStyle(document.querySelector('.sidebar-quick')).display);
            assert.notEqual(display, 'none', '.sidebar-quick is display:none inside the phone drawer');
            await assertOnScreen(page, '.sidebar-new-btn', width, height);
            await assertOnScreen(page, '.sidebar-jump', width, height);
        });

        test('the drawer foot and its Settings/menu button sit inside the viewport', async () => {
            ready();
            const foot = await page.locator('.sidebar-foot').boundingBox();
            assert.ok(foot && foot.height > 0, '.sidebar-foot has no layout box');
            assert.ok(foot.y >= 0 && foot.y + foot.height <= height + px,
                `.sidebar-foot ${JSON.stringify(foot)} runs past the ${height}px viewport bottom`);
            await assertOnScreen(page, '#btn-primary-menu', width, height);
        });

        test('no desktop icon-rail styling inside the drawer: the rows keep their labels', async () => {
            ready();
            const s = await page.evaluate(() => {
                const cs = el => el && getComputedStyle(el);
                return {
                    foot: cs(document.querySelector('.sidebar-foot')).flexDirection,
                    label: cs(document.querySelector('.side-item-label'))?.display,
                };
            });
            assert.equal(s.foot, 'row', 'the drawer foot stacked into the rail column');
            assert.notEqual(s.label, 'none', 'the nav labels are hidden as in the rail');
        });
    });
}

describe('CMX-404 desktop 1440×900, collapsed: the icon rail still hides .sidebar-quick (accepted)', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900, storage: COLLAPSED }));
        await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
    });
    after(() => context && context.close());

    test('.sidebar-quick is display:none and the rail is 60px', async () => {
        ready();
        const r = await page.evaluate(() => ({
            collapsed: document.body.classList.contains('sidebar-collapsed'),
            quick: getComputedStyle(document.querySelector('.sidebar-quick')).display,
            rail: document.querySelector('.sidebar').getBoundingClientRect().width,
        }));
        assert.ok(r.collapsed, 'the persisted flag did not reach the body');
        assert.equal(r.quick, 'none', 'the desktop rail shows .sidebar-quick');
        assert.ok(Math.abs(r.rail - 60) <= px, `rail is ${r.rail}px, not 60`);
    });
});
