// CMX-315 — the Work view on a phone (Liav's approved mockups, 10-23).
//
// Before: a floating ☰ in a row of its own, then the Board/Runs/Schedules control with a
// coloured "+ New task", a row of three coloured action buttons (Init a repo, Pause dispatch,
// Pause all agents), a row of project pills, a Swipe/Rows toggle and a row of column chips:
// six rows of controls before the first card, and each card carried its branch, its ✕ and
// "Close & requeue" on its face.
// After, on a phone only: ONE title bar (☰, the project as the title with a chevron over
// "Work · N running · M queued", +, ⋯), the segmented control, then the work. Rows is the
// default; ⋯ holds the layout and the three actions, Pause all agents asks to confirm; the
// title opens the project switcher sheet. Board's status tabs ARE the swiped columns.
//
// Guards (each corrupt→RED): the floating ☰ is hidden and the bar's ☰ opens the drawer over
// the page; the bar's + and ⋯ are on top (the safety pills moved out of their way); no pill
// row and no coloured button; the switcher is a bottom sheet listing projects with counts; a
// pick retitles the bar; ⋯ → Board draws one column per screen (no peek) under underlined
// tabs; Pause all agents opens a confirm on top; no sideways page scroll. ⭐ The desktop
// negative control keeps its toolbar, pills and full cards.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
// Screenshots for a PR: CMX315_SHOTS=<dir> node --test tests/browser/work_phone.test.mjs
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium, openDashboard } from './fixture.mjs';
import { WORK_PAYLOAD } from './work_phone_data.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const SHOTS = process.env.CMX315_SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => SHOTS && page.screenshot({ path: join(SHOTS, `${name}.png`) });

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// Every safety pill lit (a share, an approval, an access request), so the float is at its widest.
const API = {
    '/api/dispatcher': WORK_PAYLOAD, '/api/agents': [],
    '/api/overrides': { requests: [{ id: 'ov1', kind: 'merge', label: 'CMX-1', task_id: 'CMX-1', question: 'merge it?' }] },
    '/api/share-requests': { requests: [{ id: 'rq1', status: 'pending', kind: 'mount', description: '/srv/data', window: 'b-waiting', wid: '@2' }] },
};

/** Switch to the Work view the way the drawer's nav row does. */
async function openWork(page, ready) {
    await page.evaluate(() => [...document.querySelectorAll('#side-nav [data-view], #side-nav .side-item')].find(e => /Work/.test(e.textContent))?.click());
    await page.waitForSelector(`#panel-work.active ${ready}`, { timeout: 15000 });
    await page.waitForTimeout(300);
}

/** What the eye sees: rendered, with a box, and the topmost element at its centre. */
function seen(page, sel) {
    return page.evaluate(s => {
        const el = document.querySelector(s);
        if (!el) return { exists: false, shown: false, onTop: false };
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        const shown = cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
        const top = shown ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) : null;
        return { exists: true, shown, onTop: !!top && (top === el || el.contains(top)), top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height };
    }, sel);
}

describe('CMX-315 390×844: the phone Work view has one title bar, Rows by default', { skip: why }, () => {
    let ctx, page, errors;
    const ready = setup(async () => {
        ({ context: ctx, page, errors } = await openDashboard(browser, { width: 390, height: 844, deviceScaleFactor: 2, touch: true, api: API }));
        await openWork(page, '.wrow');
        await shot(page, 'after-rows');
    });
    after(() => ctx && ctx.close());

    test('one bar at the top: ☰, the project with a chevron and its counts, + and ⋯, all on top; no floating ☰', async () => {
        ready();
        const bar = await seen(page, '#panel-work .work-bar');
        assert.equal(bar.shown, true);
        assert.ok(bar.top <= 1, `the bar starts ${bar.top}px down, not at the top edge`);
        assert.equal((await seen(page, '.mobile-menu-fab')).shown, false, 'the floating ☰ still shows over the Work view');
        for (const sel of ['.work-bar-menu', '.work-bar-title', '.work-bar-new', '.work-bar-more']) {
            const b = await seen(page, `#panel-work .work-bar ${sel}`);
            assert.equal(b.onTop, true, `${sel} is covered (the safety pills?)`);
            assert.ok(b.height >= 44, `${sel} is ${b.height}px tall, under a 44px target`);
        }
        assert.equal(await page.locator('.work-bar-project').textContent(), 'All projects');
        assert.equal(await page.locator('.work-bar-sub').textContent(), 'Work · 2 running · 8 queued');
        assert.equal(await page.locator('.work-bar-name svg').count(), 1, 'no chevron on the title');
    });

    test('the segmented control sits right under the bar; no pill row, no coloured button, no layout toggle', async () => {
        ready();
        const bar = await seen(page, '#panel-work .work-bar');
        const seg = await seen(page, '#panel-work .work-phone-seg .work-seg');
        assert.ok(seg.top - bar.bottom < 16, `the segments sit ${seg.top - bar.bottom}px under the bar`);
        assert.ok(seg.width > 340, 'the segmented control is not full width');
        const visible = await page.evaluate(() => [...document.querySelectorAll('#panel-work .kanban-filter-chip, #panel-work .btn-primary, #panel-work .btn-warn, #panel-work .kanban-layout-btn, #panel-work .kanban-nav-chip')]
            .filter(e => e.getClientRects().length).length);
        assert.equal(visible, 0);
    });

    test('Rows: groups in reading order with sticky heads; a one-line title per row, the branch nowhere', async () => {
        ready();
        const heads = await page.locator('.wrows-head .wrows-label').allTextContents();
        assert.deepEqual(heads, ['In Progress', 'In Review', 'Todo']);
        assert.equal(await page.locator('.wrows-head').first().evaluate(h => getComputedStyle(h).position), 'sticky');
        const t = await page.locator('.wrow[data-task-id="CMX-303"] .wrow-title').evaluate(e => ({ ws: getComputedStyle(e).whiteSpace, to: getComputedStyle(e).textOverflow, h: e.getBoundingClientRect().height, over: e.scrollWidth > e.clientWidth }));
        assert.deepEqual([t.ws, t.to, t.over], ['nowrap', 'ellipsis', true]);
        assert.ok(t.h < 26, 'a row title wraps');
        assert.equal(await page.locator('.wrow[data-task-id="CMX-303"] .wrow-flag.warn').textContent(), 'rework 1 · risk high · CI failing');
        assert.equal(await page.locator('#work-board').evaluate(p => p.textContent.includes('liavacc/')), false, 'a branch is on the face');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, 'the page scrolls sideways');
    });

    test('the safety pills float bottom right, clear of the bar', async () => {
        ready();
        const float = await seen(page, '.safety-float');
        assert.ok(float.bottom > 844 - 40 && float.top > 600, `the safety pills sit at ${float.top}..${float.bottom}, not at the bottom`);
    });

    test('the bar\'s ☰ opens the drawer over the page, its gear not under the safety pills', async () => {
        ready();
        await page.locator('.work-bar-menu').tap();
        await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5, null, { timeout: 5000 });
        assert.equal((await seen(page, '.sidebar-new-btn')).onTop, true, 'the drawer is under the bar');
        assert.equal((await seen(page, '#btn-primary-menu')).onTop, true, 'the safety pills cover the drawer\'s gear');
        await page.locator('#sidebar-scrim').click({ position: { x: 370, y: 400 } });
        await page.waitForFunction(() => !document.querySelector('.sidebar')?.classList.contains('open'), null, { timeout: 5000 });
    });

    test('the title opens the switcher: a bottom sheet with All projects, then each project and its counts; a pick retitles the bar', async () => {
        ready();
        await page.locator('.work-bar-title').tap();
        const sheet = page.getByRole('dialog', { name: 'Project' });
        await sheet.waitFor({ timeout: 5000 });
        await page.waitForTimeout(250);
        await shot(page, 'after-switcher');
        const box = await sheet.boundingBox();
        assert.ok(Math.abs(box.y + box.height - 844) < 2, 'the switcher is not a bottom sheet');
        assert.deepEqual(await sheet.locator('.wproj-name').allTextContents(), ['All projects', 'chelamux', 'lean-alpha']);
        assert.deepEqual(await sheet.locator('.wproj-needs').allTextContents(), ['1 needs you']);
        await sheet.getByRole('radio').nth(2).tap();
        await sheet.waitFor({ state: 'detached', timeout: 5000 });
        assert.equal(await page.locator('.work-bar-project').textContent(), 'lean-alpha');
        assert.deepEqual(await page.locator('.wrow').evaluateAll(rs => rs.map(r => r.dataset.taskId)), ['LAP-9', 'LAP-10', 'LAP-11']);
        await page.locator('.work-bar-title').tap();
        await page.getByRole('dialog', { name: 'Project' }).getByRole('radio').first().tap();
        await page.getByRole('dialog', { name: 'Project' }).waitFor({ state: 'detached', timeout: 5000 });
    });

    test('⋯ holds the layout and the three actions; Pause all agents opens a confirm on top', async () => {
        ready();
        await page.locator('.work-bar-more').tap();
        const sheet = page.getByRole('dialog', { name: 'Work options' });
        await sheet.waitFor({ timeout: 5000 });
        await page.waitForTimeout(250);
        await shot(page, 'after-menu');
        assert.deepEqual(await sheet.locator('.wopt-label').allTextContents(), ['Init a repo', 'Pause dispatch', 'Pause all agents']);
        await sheet.getByRole('button', { name: /Pause all agents/ }).tap();
        const confirm = page.getByRole('dialog', { name: 'Pause all agents?' });
        await confirm.waitFor({ timeout: 5000 });
        const centreInside = await confirm.evaluate(d => { const r = d.getBoundingClientRect(); return d.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); });
        assert.equal(centreInside, true, 'the confirm is not on top');
        await shot(page, 'after-confirm');
        await confirm.getByRole('button', { name: 'Cancel' }).tap();
        await confirm.waitFor({ state: 'detached', timeout: 5000 });
    });

    test('⋯ → Board: the status tabs are the columns, one screen wide, no header, no peek; a tab tap swipes there', async () => {
        ready();
        await page.locator('.work-bar-more').tap();
        const sheet = page.getByRole('dialog', { name: 'Work options' });
        await sheet.getByRole('button', { name: 'Board' }).tap();
        await sheet.getByRole('button', { name: 'Close' }).tap();
        await page.waitForSelector('#panel-work .wcard', { timeout: 5000 });
        await page.locator('.wtab[data-col="tr-in-progress"]').tap();
        await page.waitForTimeout(700);
        await shot(page, 'after-board');
        const geo = await page.evaluate(() => {
            const b = document.getElementById('kanban-board').getBoundingClientRect();
            const cols = [...document.querySelectorAll('#kanban-board .wcol')].map(c => c.getBoundingClientRect());
            const on = cols.filter(r => r.right > b.left + 1 && r.left < b.right - 1);
            const active = document.querySelector('.wtab.active');
            return { bw: b.width, widths: cols.map(r => Math.round(r.width)), onScreen: on.length, active: active?.dataset.col, underline: getComputedStyle(active).borderBottomColor, heads: document.querySelectorAll('#kanban-board .kanban-col-head').length };
        });
        assert.deepEqual(geo.widths, geo.widths.map(() => Math.round(geo.bw)), 'a column is not one board wide');
        assert.equal(geo.onScreen, 1, 'the next column peeks in');
        assert.equal(geo.active, 'tr-in-progress');
        assert.notEqual(geo.underline, 'rgba(0, 0, 0, 0)', 'the active tab has no underline');
        assert.equal(geo.heads, 0);
        const title = await page.locator('.wcard[data-task-id="CMX-303"] .wcard-title').evaluate(e => getComputedStyle(e).webkitLineClamp);
        assert.equal(title, '2');
        assert.equal(await page.locator('.wcard .kanban-delete-btn, .wcard .kanban-requeue-btn').count(), 0);
        assert.deepEqual(errors, []);
    });
});

describe('CMX-315 negative control, 1440×900: the desktop Work view is unchanged', { skip: why }, () => {
    let ctx, page;
    const ready = setup(async () => {
        ({ context: ctx, page } = await openDashboard(browser, { width: 1440, height: 900, api: API }));
        await openWork(page, '.kanban-card');
    });
    after(() => ctx && ctx.close());

    test('the toolbar, the project pills and the full cards; no title bar, no compact card', async () => {
        ready();
        for (const sel of ['.work-toolbar #dispatch-hold-btn', '.work-toolbar #fleet-pause-btn', '.work-toolbar #new-task-btn', '.kanban-filter-chip']) {
            assert.equal((await seen(page, sel)).shown, true, `${sel} is gone from the desktop`);
        }
        assert.equal(await page.locator('.work-bar, .wcard, .wrow').count(), 0);
        assert.ok(await page.locator('.kanban-card .kanban-delete-btn').count() > 0, 'the desktop card lost its ✕');
    });
});
