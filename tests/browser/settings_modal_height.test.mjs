// CMX-310 — the Settings modal keeps ONE box, whatever it shows.
//
// Liav, 10-22: the modal shifted on every tab switch and search keystroke. It is centred on
// `top: 50%` + `translate(-50%, -50%)` and was sized by its content up to `max-height: 86vh`,
// so a shorter tab shrank it and the head and tab rail moved with it. Now its height is a
// fixed rule (desktop `min(640px, 86vh)`, phone `92vh`) and the tab panel scrolls inside.
//
// Measured by a real browser (jsdom has no layout), at desktop and phone size:
//   - the dialog's height is the documented literal, not a content height;
//   - every tab, a search with hits, a search with none, and a cleared search all leave the
//     dialog's, head's and tab rail's boxes exactly where they were;
//   - on every tab the panel's last row is reachable by scrolling the panel, and the footer
//     stays on screen inside the dialog: nothing is clipped without a scroll;
//   - at least one tab is taller than the panel, so the scroll case is really exercised.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
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

const TABS = ['general', 'timing', 'dispatch', 'notifications', 'cost', 'appearance', 'collaboration'];
const PARTS = ['#settings-drawer', '#settings-drawer .settings-modal-head', '#settings-tabs', '#settings-drawer .settings-modal-foot'];

/** The boxes of the modal's fixed parts, rounded to the device pixel. */
function boxes(page) {
    return page.evaluate(sels => sels.map(s => {
        const r = document.querySelector(s).getBoundingClientRect();
        return [s, Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)].join(' ');
    }), PARTS);
}

/** Wait out a re-render and the open transition. */
const still = page => page.waitForTimeout(250);

for (const { width, height, touch, expected } of [
    { width: 1440, height: 900, touch: false, expected: 640 },          // min(640px, 86vh = 774px)
    { width: 1440, height: 700, touch: false, expected: 602 },          // min(640px, 86vh = 602px)
    { width: 390, height: 844, touch: true, expected: 776.48 },         // 92vh
]) {
    describe(`CMX-310 ${width}×${height}: the Settings modal never resizes or moves`, { skip: why }, () => {
        let page, context;
        let first;
        const ready = setup(async () => {
            ({ page, context } = await openDashboard(browser, { width, height, touch }));
            await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
            await page.evaluate(() => window.chela.toggleSettings());
            await page.waitForFunction(() => {
                const m = document.getElementById('settings-drawer');
                return !!m && m.classList.contains('open') && getComputedStyle(m).opacity === '1';
            }, null, { timeout: 5000 });
            await still(page);
            first = await boxes(page);
        });
        after(() => context && context.close());

        test('the dialog is the fixed height, not its content\'s', async () => {
            ready();
            const h = await page.evaluate(() => document.getElementById('settings-drawer').getBoundingClientRect().height);
            assert.ok(Math.abs(h - expected) < 1, `the dialog is ${h}px tall, not ${expected}px`);
        });

        test('every tab leaves the dialog, head, rail and footer where they were', async () => {
            ready();
            for (const tab of [...TABS, ...TABS.slice().reverse()]) {
                await page.locator(`.settings-tab[data-tab="${tab}"]`).click();
                await still(page);
                assert.deepEqual(await boxes(page), first, `switching to ${tab} moved the modal`);
            }
        });

        test('typing a search, a search with no hits, and clearing it leave the boxes unchanged', async () => {
            ready();
            for (const q of ['r', 're', 'remote', 'zzqqxx', '']) {
                await page.locator('#settings-search').fill(q);
                await still(page);
                assert.deepEqual(await boxes(page), first, `the search "${q}" moved the modal`);
            }
        });

        test('every tab\'s last row is reachable by scrolling the panel; the footer stays on screen', async () => {
            ready();
            let scrolled = 0;
            for (const tab of TABS) {
                await page.locator(`.settings-tab[data-tab="${tab}"]`).click();
                await still(page);
                const r = await page.evaluate(() => {
                    const body = document.getElementById('drawer-body');
                    const overflows = body.scrollHeight > body.clientHeight + 1;
                    body.scrollTop = body.scrollHeight;
                    const panel = body.querySelector('.settings-tabpanel.active');
                    const last = [...panel.children].filter(c => c.getClientRects().length).at(-1);
                    const b = body.getBoundingClientRect();
                    const l = last.getBoundingClientRect();
                    const foot = document.querySelector('#settings-drawer .settings-modal-foot').getBoundingClientRect();
                    const modal = document.getElementById('settings-drawer').getBoundingClientRect();
                    return {
                        overflows, overflowY: getComputedStyle(body).overflowY,
                        lastIn: l.height > 0 && l.bottom <= b.bottom + 1,   // scrolled to the end, its bottom is in view
                        footIn: foot.height > 0 && foot.bottom <= modal.bottom + 1 && foot.bottom <= innerHeight + 1,
                    };
                });
                if (r.overflows) {
                    scrolled++;
                    assert.equal(r.overflowY, 'auto', `${tab} overflows a panel that does not scroll`);
                }
                assert.ok(r.lastIn, `${tab}: the last row is not reachable inside the panel`);
                assert.ok(r.footIn, `${tab}: the footer is off the dialog or the screen`);
                await page.evaluate(() => { document.getElementById('drawer-body').scrollTop = 0; });
            }
            assert.ok(scrolled > 0, 'no tab is taller than the panel: the scroll case was never exercised');
        });
    });
}
