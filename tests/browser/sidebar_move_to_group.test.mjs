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
// The pre-CMX-82 localStorage keys: the page imports them into the (fixture's) server
// layout on load, as a first device does.
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

// The items of the open menu (CMX-277: the kit's Menu, in a portal), as a viewer sees them:
// null when no menu is displayed.
const rowMenu = page => page.evaluate(() => {
    const m = document.querySelector('[role="menu"]');
    if (!m || getComputedStyle(m).display === 'none') return null;
    return [...m.querySelectorAll('[role^="menuitem"]')].map(i => (i.querySelector('.ov-label') || i).textContent.trim());
});

// Tap the menu item labelled `label` at its on-screen centre — a real touch, hit-tested.
async function tapMenu(page, label) {
    const item = page.locator('[role="menu"] [role^="menuitem"]').filter({ hasText: label }).first();
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
        // The row can be drawn once by Folder before the server layout (the imported
        // Custom groups view) has answered — wait for the Custom groups draw, so the
        // test reads the layout it set up, not that first frame (red on CI, 2026-10-12).
        // Only Custom groups draws an "Ungrouped" group; Folder would draw "x" (cwd /p/x).
        await page.waitForFunction(() => [...document.querySelectorAll('.side-group .group-name')]
            .some(n => n.textContent === 'Ungrouped'), null, { timeout: 15000 });
        await openDrawer(page);
    });
    after(() => context && context.close());

    test('tap ⋯ → "Move to group…" opens its page, and tapping a group moves the row there', async () => {
        ready();
        assert.equal(await groupOf(page, 'c-idle'), 'Ungrouped', 'the fixture row did not start Ungrouped');
        await page.locator(`${ROW} .row-more`).tap();
        assert.deepEqual(await rowMenu(page), ['Move to group…', 'Archive']);   // CMX-81: every row's own Archive
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

    test('a plain session row\'s ⋯ offers no invisible move — only its own Archive (CMX-81)', async () => {
        ready();
        await page.locator(`${ROW} .row-more`).tap();
        assert.deepEqual(await rowMenu(page), ['Archive']);
    });
});
