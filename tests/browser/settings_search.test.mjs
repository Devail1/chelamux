// CMX-396 — the Settings search box, measured by a real browser (Playwright/
// Chromium) at desktop and phone widths: the input is on screen, a query's rows
// are on screen (inside the modal's scroll area, not clipped or display:none),
// unrelated rows are gone, Tab walks into the filtered controls, and Esc clears
// before it closes. The filtering LOGIC is guarded in jsdom
// (tests/settings_search.test.mjs); this file is only what jsdom cannot see.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const px = 1;

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

async function box(page, selector) {
    const b = await page.locator(selector).first().boundingBox();
    assert.ok(b, `${selector} has no layout box (not rendered)`);
    return b;
}

function inside(inner, outer, what) {
    assert.ok(inner.width > 0 && inner.height > 0, `${what} has no size: ${JSON.stringify(inner)}`);
    assert.ok(inner.x >= outer.x - px && inner.y >= outer.y - px
        && inner.x + inner.width <= outer.x + outer.width + px
        && inner.y + inner.height <= outer.y + outer.height + px,
        `${what} ${JSON.stringify(inner)} is not inside ${JSON.stringify(outer)}`);
}

for (const [width, height] of [[1440, 900], [390, 844]]) {
    describe(`CMX-396 ${width}×${height}: Settings search`, { skip: why }, () => {
        let page, context;
        const view = { x: 0, y: 0, width, height };
        const ready = setup(async () => {
            ({ page, context } = await openDashboard(browser, { width, height }));
            await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
        });
        after(() => context && context.close());

        test('Ctrl+, opens Settings with the search box on screen and focused', async () => {
            ready();
            await page.locator('body').click({ position: { x: 2, y: 2 } }).catch(() => {});
            await page.evaluate(() => document.activeElement && document.activeElement.blur());
            await page.keyboard.press('Control+Comma');
            await page.waitForFunction(() => {
                const m = document.getElementById('settings-drawer');
                return m.classList.contains('open') && getComputedStyle(m).opacity === '1';
            }, null, { timeout: 5000 });
            const input = await box(page, '#settings-search');
            inside(input, view, '#settings-search');
            inside(input, await box(page, '#settings-drawer'), '#settings-search (vs the modal)');
            assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id),
                'settings-search', 'the search box is not focused after Ctrl+,');
        });

        test('"remote" puts the Remote Control row on screen and hides unrelated rows', async () => {
            ready();
            await page.evaluate(() => window.chela.selectSettingsTab('appearance'));
            await page.locator('#settings-search').fill('remote');
            const row = page.locator('#remote-control-toggle').locator('xpath=ancestor::div[contains(@class,"s-row")][1]');
            const rb = await row.boundingBox();
            assert.ok(rb, 'the Remote Control row has no layout box while searching "remote"');
            inside(rb, view, 'Remote Control row');
            inside(rb, await box(page, '#drawer-body'), 'Remote Control row (vs the scroll area)');
            assert.equal(await page.locator('#theme-select').isVisible(), false, 'Theme is still visible');
            assert.equal(await page.locator('#update-apply-btn').isVisible(), false, 'Update is still visible');
            assert.equal(await page.locator('.settings-tab.active').count(), 0, 'a tab still reads as selected');
        });

        test('Tab from the search box reaches the filtered control', async () => {
            ready();
            await page.locator('#settings-search').focus();
            let reached = false;
            for (let i = 0; i < 4 && !reached; i++) {
                await page.keyboard.press('Tab');
                reached = await page.evaluate(() => document.activeElement && document.activeElement.id === 'remote-control-toggle');
            }
            assert.ok(reached, 'Tab never reached #remote-control-toggle in the filtered view');
        });

        test('no match shows the empty state; Esc clears, then Esc closes', async () => {
            ready();
            await page.locator('#settings-search').fill('zzqqxx');
            const empty = page.locator('#settings-search-empty');
            assert.equal(await empty.isVisible(), true, 'no empty state');
            inside(await empty.boundingBox(), view, '#settings-search-empty');
            await page.locator('#settings-search').focus();
            await page.keyboard.press('Escape');
            assert.equal(await page.locator('#settings-search').inputValue(), '', 'Esc did not clear the query');
            assert.equal(await page.locator('#settings-drawer.open').count(), 1, 'the first Esc closed Settings');
            assert.equal(await page.locator('#theme-select').isVisible(), true, 'clearing did not restore the Appearance tab');
            await page.keyboard.press('Escape');
            assert.equal(await page.locator('#settings-drawer.open').count(), 0, 'the second Esc did not close Settings');
        });
    });
}
