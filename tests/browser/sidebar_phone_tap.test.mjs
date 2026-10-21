// CMX-301 — on an iPhone, tapping a session row in the sidebar drawer showed the
// row's "window: cv" tooltip and never opened the session.
//
// Cause: the touch tap-tip (shared/popover installTapTips, nav.js's before CMX-219).
// On touchend it walks up to the nearest [title] and preventDefault()s, which
// cancels the click — unless the target sits in something it recognises as
// interactive (button, a, [onclick], [role=button]). The legacy row carried an
// onclick attribute; the React row (CMX-276) keeps its onClick in React, so the
// tap-tip took every tap on the row's titled name. Fixed by also leaving alone
// anything drawn with the pointer cursor (what is clickable is).
//
// Driven with tap() on a touch context, against the committed bundle: drawer open
// → tap a row ⇒ that session is the one full-width pane and the drawer is shut;
// tap the SAME row again ⇒ still open (the desktop toggle never fires on a
// phone). Desktop keeps CMX-139's toggle: a click on a pane open on the wall
// minimizes it, a second click brings it back.
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

const W = 390, H = 844;

async function openDrawer(page) {
    await page.locator('#btn-menu-mobile').tap();
    await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
        null, { timeout: 5000 });
}

// What is on screen: the drawer, any tap-tip, and the pane(s) actually drawn.
function screenState(page) {
    return page.evaluate(() => {
        const sb = document.querySelector('.sidebar').getBoundingClientRect();
        const panes = [...document.querySelectorAll('#term-stage iframe.term-frame')]
            .map(f => ({ src: decodeURIComponent(new URL(f.src).pathname), w: f.getBoundingClientRect().width }))
            .filter(p => p.w > 0);
        return {
            drawerOpen: document.querySelector('.sidebar').classList.contains('open') || sb.right > 1,
            tip: document.querySelector('.tap-tip:not(.ui-floating)')?.textContent ?? null,
            panes,
        };
    });
}

// The tap-tip shown right after a tap (it hides itself after 4 s), or null.
const tipNow = page => page.evaluate(() => document.querySelector('.tap-tip:not(.ui-floating)')?.textContent ?? null);

async function tapRow(page, name) {
    // The row's name span carries title="window: …": the exact element the tap-tip took.
    await page.locator(`#sidebar-agents .agent-row[data-agent="${name}"] .agent-row-name`).tap();
    const tip = await tipNow(page);
    assert.equal(tip, null, `the tap popped the tap-tip "${tip}" instead of opening the session`);
}

async function waitShown(page, wid) {
    await page.waitForFunction(w => {
        const sb = document.querySelector('.sidebar').getBoundingClientRect();
        const f = [...document.querySelectorAll('#term-stage iframe.term-frame')]
            .find(x => decodeURIComponent(new URL(x.src).pathname) === `/term/${w}/`);
        return sb.right <= 1 && f && f.getBoundingClientRect().width > 0;
    }, wid, { timeout: 5000 }).catch(() => {});
}

describe(`CMX-301 phone ${W}×${H}, touch: tapping a session row opens it`, { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: W, height: H, touch: true }));
        await page.waitForSelector('#sidebar-agents .agent-row', { state: 'attached', timeout: 15000 });
    });
    after(() => context && context.close());

    test('the row name carries a title (else the tap below proves nothing)', async () => {
        ready();
        assert.equal(await page.evaluate(() =>
            document.querySelector('#sidebar-agents .agent-row[data-agent="c-idle"] .agent-row-name')?.getAttribute('title')),
        'window: c-idle');
    });

    test('tap a row ⇒ that session is the one full-width pane, the drawer shut, no tooltip', async () => {
        ready();
        await openDrawer(page);
        await tapRow(page, 'c-idle');
        await waitShown(page, '@3');
        const s = await screenState(page);
        assert.equal(s.tip, null, `the tap popped the tap-tip "${s.tip}" instead of opening the session`);
        assert.equal(s.drawerOpen, false, 'the drawer is still open after the tap');
        assert.deepEqual(s.panes.map(p => p.src), ['/term/@3/'], `panes on screen: ${JSON.stringify(s.panes)}`);
        assert.ok(s.panes[0].w >= W - 40, `the pane is ${s.panes[0].w}px wide, not full-width at ${W}px`);
    });

    test('tap the SAME row again ⇒ it is still open (no toggle-off on a phone)', async () => {
        ready();
        await openDrawer(page);
        await tapRow(page, 'c-idle');
        await waitShown(page, '@3');
        const s = await screenState(page);
        assert.equal(s.tip, null, `the tap popped the tap-tip "${s.tip}"`);
        assert.equal(s.drawerOpen, false, 'the drawer is still open after the second tap');
        assert.deepEqual(s.panes.map(p => p.src), ['/term/@3/'], `panes on screen: ${JSON.stringify(s.panes)}`);
    });

    test('tap another row ⇒ it switches to that session', async () => {
        ready();
        await openDrawer(page);
        await tapRow(page, 'a-working');
        await waitShown(page, '@1');
        const s = await screenState(page);
        assert.equal(s.drawerOpen, false);
        assert.deepEqual(s.panes.map(p => p.src), ['/term/@1/'], `panes on screen: ${JSON.stringify(s.panes)}`);
    });

    test('a titled readout that is not clickable still gets its tap-tip', async () => {
        ready();
        await openDrawer(page);
        // The sidebar foot's CPU readout: titled, not interactive.
        await page.locator('#res-cpu').tap();
        assert.equal(await tipNow(page), 'CPU: 8%', 'no tap-tip on the CPU readout: the bail is too wide');
    });
});

describe('CMX-301 desktop 1440×900: a row click on a pane open on the wall still toggles it (CMX-139)', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900 }));
        await page.waitForSelector('#term-stage .grid-stack-item[gs-id="@3"]', { state: 'attached', timeout: 15000 });
    });
    after(() => context && context.close());

    const minimized = () => page.evaluate(() =>
        document.querySelector('#term-stage .grid-stack-item[gs-id="@3"]')?.dataset.minimized === '1');

    test('click ⇒ minimized; click again ⇒ back on the wall', async () => {
        ready();
        assert.equal(await minimized(), false, '@3 did not start open on the wall');
        await page.locator('#sidebar-agents .agent-row[data-agent="c-idle"]').click();
        await page.waitForFunction(() =>
            document.querySelector('#term-stage .grid-stack-item[gs-id="@3"]')?.dataset.minimized === '1',
        null, { timeout: 5000 }).catch(() => {});
        assert.equal(await minimized(), true, 'a click on an open pane\'s row did not minimize it');
        await page.locator('#sidebar-agents .agent-row[data-agent="c-idle"]').click();
        await page.waitForFunction(() =>
            document.querySelector('#term-stage .grid-stack-item[gs-id="@3"]')?.dataset.minimized !== '1',
        null, { timeout: 5000 }).catch(() => {});
        assert.equal(await minimized(), false, 'a second click did not restore the pane');
    });
});
