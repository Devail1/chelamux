// CMX-380 (Liav, 11-14): drag a sidebar group to reorder it. On a phone the drag starts on a
// LONG PRESS, so a plain swipe keeps scrolling the drawer. Measured here in a real Chromium
// emulating a phone (touch, 390×844), with real touch input (CDP Input.dispatchTouchEvent:
// the browser's own gesture handling decides scroll vs drag, as on the device), in a drawer
// scrolled down a long list. The pointer logic itself is held in jsdom by
// chela/dashboard/frontend/src/features/sidebar/tests/drag.test.tsx.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
//
// CMX-435: no sleeps. Each step awaits the event it depends on: the swipe's glide ENDING
// (a `scrollend` after the finger lifts: a finger that lands on a still-gliding list gets an uncancelable touch
// sequence, so the next test's long press could not hold the list; that was the CI flake),
// the drag starting, the layout POST landing, the grip's opacity transition finishing.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard, DRAWER_OPENER } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const W = 390, H = 844;
// 14 folders of two sessions each: a list far taller than the drawer.
const FOLDERS = Array.from({ length: 14 }, (_, i) => `f${String(i).padStart(2, '0')}`);
const AGENTS = FOLDERS.flatMap((f, i) => [1, 2].map(n => ({
    name: `${f}-${n}`, window_id: `@${i * 2 + n}`, online: true, claude_running: true, session_status: 'idle',
    ai_title: `${f} session ${n}`, cwd: `/srv/code/${f}`,
})));
const STORAGE = { chela_sb_view: JSON.stringify({ groupBy: 'folder', activity: 'all', sort: 'name' }) };
const LONG_PRESS_MS = 400;

// The drawer's scroller (the first scrolling ancestor of the list), found in the page.
const SCROLLER_IN_PAGE = `(() => {
    let sc = document.getElementById('sidebar-agents');
    while (sc && !(['auto', 'scroll'].includes(getComputedStyle(sc).overflowY) && sc.scrollHeight > sc.clientHeight)) sc = sc.parentElement;
    return sc;
})()`;

/** Arm a watch on the drawer's scroller for the touch scroll about to run; await the returned
 * function to wait until it (and the glide after the finger lifts) has come to rest. Only a
 * `scrollend` AFTER the touchend counts: the browser sends a touch scroll's scrollend once the
 * glide is over, while an earlier one still in the queue (the setup's scrollIntoView) is not
 * this scroll's. */
async function watchScrollEnd(page) {
    await page.evaluate(`(() => {
        const sc = ${SCROLLER_IN_PAGE};
        if (!sc) throw new Error('the drawer has no scroller: the list does not overflow it');
        const w = window.__cmx435Scroll = { lifted: false, rest: false };
        window.addEventListener('touchend', () => { w.lifted = true; }, { capture: true, once: true });
        const end = () => { if (!w.lifted) return; w.rest = true; sc.removeEventListener('scrollend', end); };
        sc.addEventListener('scrollend', end);
    })()`);
    return () => page.waitForFunction(() => window.__cmx435Scroll.rest === true, null, { timeout: 10000 });
}

/** The next layout write the page sends (POST /api/sidebar/layout), as the server answered it.
 * Armed before the drop and awaited after it: a handler is attached at once, so an assertion
 * that fails in between is reported as itself, not as this promise's unhandled rejection. */
function nextLayoutWrite(page) {
    const p = page.waitForResponse(
        r => r.url().endsWith('/api/sidebar/layout') && r.request().method() === 'POST', { timeout: 10000 });
    p.catch(() => {});
    return p;
}

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

async function openDrawer(page) {
    await page.locator(DRAWER_OPENER).tap();
    await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
        null, { timeout: 5000 });
}

// The drawer's scroller, its scrollTop, and the groups in drawn order.
const listState = page => page.evaluate(`(() => {
    const sc = ${SCROLLER_IN_PAGE};
    return {
        scrollTop: sc ? Math.round(sc.scrollTop) : null,
        groups: [...document.querySelectorAll('#sidebar-agents > .side-group')].map(g => g.dataset.g.replace('/srv/code/', '')),
    };
})()`);

const headCenter = (page, f) => page.evaluate(k => {
    const r = document.querySelector(`.group-head[data-g="/srv/code/${k}"]`).getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), top: r.top, bottom: r.bottom };
}, f);

const serverOrder = page => page.evaluate(async () => {
    const r = await fetch('/api/sidebar/layout');
    const { layout } = await r.json();
    return (layout?.order ?? []).map(k => k.replace('/srv/code/', ''));
});

describe(`CMX-380 phone ${W}×${H}: long-press drag in a scrolled drawer`, { skip: why }, () => {
    let page, context, cdp;
    const touch = (type, pts) => cdp.send('Input.dispatchTouchEvent', {
        type, touchPoints: pts.map(([x, y]) => ({ x, y, id: 1, radiusX: 2, radiusY: 2, force: 1 })),
    });
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: W, height: H, storage: STORAGE, touch: true,
            api: { '/api/agents': AGENTS } }));
        cdp = await context.newCDPSession(page);
        await page.waitForSelector('.group-head[data-g="/srv/code/f13"]', { state: 'attached', timeout: 15000 });
        await page.waitForFunction(() => window.chelaBridge?.sidebarLayout.get().loaded, null, { timeout: 15000 });
        await openDrawer(page);
        // scroll the drawer down: the drag below runs in a scrolled list
        await page.evaluate(() => document.querySelector('.group-head[data-g="/srv/code/f09"]').scrollIntoView({ block: 'center' }));
    });
    after(() => context && context.close());

    test('the list is scrolled, and a plain swipe scrolls it: no drag, nothing written', async () => {
        ready();
        const s0 = await listState(page);
        assert.ok(s0.scrollTop > 100, `the drawer is not scrolled (scrollTop ${s0.scrollTop})`);
        const c = await headCenter(page, 'f09');
        const settled = await watchScrollEnd(page);
        await touch('touchStart', [[c.x, c.y]]);
        for (let i = 1; i <= 10; i++) await touch('touchMove', [[c.x, c.y + i * 15]]);
        await touch('touchEnd', []);
        // the swipe flicks: the list glides on after the finger lifts, until it comes to rest
        await settled();
        const s1 = await listState(page);
        assert.ok(s1.scrollTop < s0.scrollTop - 50, `a swipe down did not scroll the list (${s0.scrollTop} → ${s1.scrollTop})`);
        assert.deepEqual(s1.groups, s0.groups);
        assert.deepEqual(await serverOrder(page), []);
        assert.equal(await page.evaluate(() => document.body.classList.contains('sidebar-dragging')), false);
    });

    test('a long press then a drag moves f09 above f07, holds the list still, and persists the order', async () => {
        ready();
        await page.evaluate(() => document.querySelector('.group-head[data-g="/srv/code/f09"]').scrollIntoView({ block: 'center' }));
        const s0 = await listState(page);
        assert.ok(s0.scrollTop > 100, `the drawer is not scrolled (scrollTop ${s0.scrollTop})`);
        const from = await headCenter(page, 'f09');
        const to = await headCenter(page, 'f07');
        const pressed = Date.now();
        await touch('touchStart', [[from.x, from.y]]);
        await page.waitForFunction(() => document.body.classList.contains('sidebar-dragging'), null, { timeout: 10000 })
            .catch(() => assert.fail('the long press did not start a drag'));
        assert.ok(Date.now() - pressed >= LONG_PRESS_MS, `the drag started before the long press was up (${Date.now() - pressed} ms)`);
        const steps = 12;
        for (let i = 1; i <= steps; i++) {
            await touch('touchMove', [[from.x, Math.round(from.y + ((to.top + 4) - from.y) * i / steps)]]);
        }
        const mid = await page.evaluate(() => ({
            line: [...document.querySelectorAll('.side-group.drop-before')].map(g => g.dataset.g.replace('/srv/code/', '')),
        }));
        assert.deepEqual(mid.line, ['f07'], 'no drop line above f07');
        // the drag held the list still: it did not scroll under the finger
        assert.equal((await listState(page)).scrollTop, s0.scrollTop);
        const wrote = nextLayoutWrite(page);
        await touch('touchEnd', []);
        await page.waitForFunction(() => !document.body.classList.contains('sidebar-dragging'));
        const want = [...s0.groups];
        want.splice(want.indexOf('f09'), 1);
        want.splice(want.indexOf('f07'), 0, 'f09');
        assert.deepEqual((await listState(page)).groups, want);
        await wrote;
        assert.deepEqual(await serverOrder(page), want);
        // the long press opened neither the row menu nor a session
        assert.equal(await page.evaluate(() => !!document.querySelector('[role="menu"]')), false);
    });

    test('no horizontal scroll while the grip and drop styles are on the page', async () => {
        ready();
        const m = await page.evaluate(() => ({
            page: document.documentElement.scrollWidth - window.innerWidth,
            sidebar: document.querySelector('.sidebar').scrollWidth - document.querySelector('.sidebar').clientWidth,
        }));
        assert.deepEqual(m, { page: 0, sidebar: 0 });
    });
});

describe('CMX-380 desktop 1440×900: a mouse drag reorders, a click under the threshold folds', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900, storage: STORAGE,
            api: { '/api/agents': AGENTS.slice(0, 6) } }));
        await page.waitForSelector('.group-head[data-g="/srv/code/f02"]', { state: 'attached', timeout: 15000 });
        await page.waitForFunction(() => window.chelaBridge?.sidebarLayout.get().loaded, null, { timeout: 15000 });
    });
    after(() => context && context.close());

    test('the grip shows on hover only', async () => {
        ready();
        // the grip's opacity once its transition (if any) has finished
        const op = async () => {
            await page.waitForFunction(() => document.querySelector('.group-head[data-g="/srv/code/f01"] .drag-grip')
                .getAnimations().every(a => a.playState === 'finished'), null, { timeout: 10000 });
            return page.evaluate(() => getComputedStyle(document.querySelector('.group-head[data-g="/srv/code/f01"] .drag-grip')).opacity);
        };
        await page.mouse.move(5, 890);
        assert.equal(await op(), '0');
        const c = await headCenter(page, 'f01');
        await page.mouse.move(c.x, c.y);
        assert.notEqual(await op(), '0');
    });

    test('a click under the threshold folds the group; a drag moves f02 above f00', async () => {
        ready();
        const c = await headCenter(page, 'f02');
        await page.mouse.move(c.x, c.y);
        await page.mouse.down();
        await page.mouse.move(c.x + 2, c.y + 1);
        await page.mouse.up();
        assert.equal(await page.evaluate(() => document.querySelector('.side-group[data-g="/srv/code/f02"]').classList.contains('collapsed')), true);
        await page.mouse.click(c.x, c.y); // unfold
        const to = await headCenter(page, 'f00');
        await page.mouse.move(c.x, c.y);
        await page.mouse.down();
        await page.mouse.move(c.x, c.y - 10, { steps: 3 });
        await page.mouse.move(c.x, to.top + 3, { steps: 8 });
        const wrote = nextLayoutWrite(page);
        await page.mouse.up();
        assert.deepEqual((await listState(page)).groups, ['f02', 'f00', 'f01']);
        assert.equal(await page.evaluate(() => document.querySelector('.side-group[data-g="/srv/code/f02"]').classList.contains('collapsed')), false,
            'the click that ended the drag folded the group');
        await wrote;
        assert.deepEqual(await serverOrder(page), ['f02', 'f00', 'f01']);
    });
});
