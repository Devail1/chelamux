// CMX-74 — Liav, on an iPhone: "what is this move to group? it doesn't do anything".
// Two causes, both measured here in a real Chromium emulating a phone (touch, 390×844):
//   - the row menu offered "Move to group…" under every Group-by mode, but only Custom
//     groups DRAWS custom groups — a move anywhere else was saved and invisible;
//   - under Custom groups too, tapping it drew the move page into a popover the tap's
//     own handler had just hidden — the menu simply vanished.
// The jsdom half (tests/sidebar_view.test.mjs) holds the same two facts on the model;
// this file proves them with real taps, layout and hit-testing.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const W = 390, H = 844;
const ROW = '.agent-row[data-agent="c-idle"]';
const CUSTOM = {
    chela_sb_view: JSON.stringify({ groupBy: 'custom' }),
    chela_sb_custom_groups: JSON.stringify({ groups: [{ id: 'g1', name: 'Infra' }], assign: {} }),
};

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

async function openDrawer(page) {
    await page.locator('#btn-menu-mobile').tap();
    await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
        null, { timeout: 5000 });
}

// The rows of the open row menu, as a viewer sees them: only when it is displayed.
const rowMenu = page => page.evaluate(() => {
    const m = document.getElementById('row-menu');
    if (!m || getComputedStyle(m).display === 'none') return null;
    return [...m.querySelectorAll('.popover-item')].map(i => (i.querySelector('.vm-label') || i).textContent.trim());
});

// Tap the menu row labelled `label` at its on-screen centre — a real touch, hit-tested.
async function tapMenu(page, label) {
    const item = page.locator('#row-menu .popover-item').filter({ hasText: label }).first();
    assert.ok(await item.isVisible(), `"${label}" is not visible in the row menu`);
    await item.tap();
}

const groupOf = (page, agent) => page.evaluate(a => {
    const row = document.querySelector(`.agent-row[data-agent="${a}"]`);
    const g = row && row.closest('.side-group');
    return g ? g.querySelector('.group-name').textContent : null;
}, agent);

describe(`CMX-74 phone ${W}×${H}, Group by Custom groups: Move to group… works by tap`, { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: W, height: H, storage: CUSTOM, touch: true }));
        await page.waitForSelector(ROW, { state: 'attached', timeout: 15000 });
        await openDrawer(page);
    });
    after(() => context && context.close());

    test('tap ⋯ → "Move to group…" opens its page, and tapping a group moves the row there', async () => {
        ready();
        assert.equal(await groupOf(page, 'c-idle'), 'Ungrouped', 'the fixture row did not start Ungrouped');
        await page.locator(`${ROW} .row-more`).tap();
        assert.deepEqual(await rowMenu(page), ['Move to group…']);
        await tapMenu(page, 'Move to group…');
        assert.deepEqual(await rowMenu(page), ['Move to group', 'Infra', 'Ungrouped', 'New group…'],
            'tapping "Move to group…" did not leave its page on screen');
        await tapMenu(page, 'Infra');
        assert.equal(await rowMenu(page), null, 'choosing a group should close the menu');
        assert.equal(await groupOf(page, 'c-idle'), 'Infra', 'the row did not move into the chosen group');
    });
});

describe(`CMX-74 phone ${W}×${H}, Group by Folder (default): no dead Move item`, { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: W, height: H, touch: true }));
        await page.waitForSelector(ROW, { state: 'attached', timeout: 15000 });
        await openDrawer(page);
    });
    after(() => context && context.close());

    test('a plain session row has no ⋯ — its only item would have been the invisible move', async () => {
        ready();
        assert.equal(await page.locator(`${ROW} .row-more`).count(), 0);
    });
});
