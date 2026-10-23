// CMX-316 — the Work view on desktop (Liav's approved mockup, 10-23).
//
// Before: two rows of controls above the board (Board/Runs/Schedules with four coloured
// buttons, then a row of project pills), every column a share of the width with a "Hidden
// columns" panel beside them, and full cards carrying their branch, ✕ and Close & requeue.
// After, wider than a phone: ONE header row (the project switcher, the segmented control, Pause
// dispatch as a quiet outline, New task as the one primary button, ⋯); Todo, In Progress and
// In Review as fixed-width columns with Backlog and Done as narrow rails at the edges, opened
// by a click and remembered across a reload; the compact card; Rows as a sortable table.
//
// Guards (each corrupt→RED): one header row whose controls share one line, on top (not under
// the safety pills); the switcher is a dropdown anchored under the title; Backlog and Done are
// 44px rails at the board's edges and Done never leaves the screen; a rail opens on a click and
// stays open across a reload; no hidden-columns panel, no branch on a card; Rows is a table
// with the listed column heads; no sideways page scroll.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
// Screenshots for a PR: CMX316_SHOTS=<dir> node --test tests/browser/work_desktop.test.mjs
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium, openDashboard } from './fixture.mjs';
import { WORK_PAYLOAD } from './work_phone_data.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const SHOTS = process.env.CMX316_SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => SHOTS && page.screenshot({ path: join(SHOTS, `${name}.png`) });

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// A share lit, so the safety pills float in the header row's corner.
const API = {
    '/api/dispatcher': WORK_PAYLOAD, '/api/agents': [],
    '/api/share-requests': { requests: [{ id: 'rq1', status: 'pending', kind: 'mount', description: '/srv/data', window: 'b-waiting', wid: '@2' }] },
};

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

describe('CMX-316 1440×900: the desktop Work view has one header row, rails and the compact card', { skip: why }, () => {
    let ctx, page, errors;
    const ready = setup(async () => {
        ({ context: ctx, page, errors } = await openDashboard(browser, { width: 1440, height: 900, api: API }));
        await openWork(page, '.wcard');
        await shot(page, 'after-board');
    });
    after(() => ctx && ctx.close());

    test('ONE header row: switcher, segments, Pause dispatch, New task and ⋯ on one line, each on top; no pill row', async () => {
        ready();
        const parts = ['.work-head-title', '#work-seg', '#dispatch-hold-btn', '#new-task-btn', '#work-more-btn'];
        const boxes = [];
        for (const sel of parts) {
            const b = await seen(page, `#panel-work .work-head ${sel}`);
            assert.equal(b.shown, true, `${sel} is not shown`);
            assert.equal(b.onTop, true, `${sel} is covered (the safety pills?)`);
            boxes.push(b);
        }
        const mids = boxes.map(b => (b.top + b.bottom) / 2);
        assert.ok(Math.max(...mids) - Math.min(...mids) < 6, `the header's controls are not on one line: ${mids}`);
        for (let i = 1; i < boxes.length; i++) assert.ok(boxes[i].left >= boxes[i - 1].right, `${parts[i]} is not right of ${parts[i - 1]}`);
        assert.equal(await page.locator('#panel-work .kanban-filter-chip, #panel-work .work-toolbar, #kanban-hidden').count(), 0);
        assert.deepEqual(await page.locator('#panel-work .work-head .btn-primary').evaluateAll(bs => bs.map(b => b.id)), ['new-task-btn']);
        const hold = await page.locator('#dispatch-hold-btn').evaluate(b => ({ bg: getComputedStyle(b).backgroundColor, border: getComputedStyle(b).borderTopWidth, text: b.textContent }));
        assert.deepEqual([hold.bg, hold.text], ['rgba(0, 0, 0, 0)', 'Pause dispatch'], 'Pause dispatch is not a quiet outline');
        assert.notEqual(hold.border, '0px');
        assert.equal(await page.locator('.work-head-sub').textContent(), '2 running · 8 queued · dispatch on');
        // the board starts right under the header
        const head = await seen(page, '.work-head');
        const board = await seen(page, '#kanban-board');
        assert.ok(board.top - head.bottom < 40, `the board starts ${board.top - head.bottom}px under the header`);
    });

    test('Backlog and Done are 44px rails at the board\'s edges; Todo, In Progress and In Review are 320px columns; Done is on screen', async () => {
        ready();
        const geo = await page.evaluate(() => [...document.querySelectorAll('#kanban-board .kanban-rail, #kanban-board .kanban-col')].map(e => {
            const r = e.getBoundingClientRect();
            return { col: e.dataset.col, rail: e.classList.contains('kanban-rail'), w: Math.round(r.width), left: r.left, right: r.right };
        }));
        assert.deepEqual(geo.map(g => [g.col, g.rail, g.w]), [
            ['tr-backlog', true, 44], ['tr-todo', false, 320], ['tr-in-progress', false, 320], ['tr-in-review', false, 320], ['tr-done', true, 44],
        ]);
        for (let i = 1; i < geo.length; i++) assert.ok(geo[i].left > geo[i - 1].right, 'the board is not left to right');
        assert.ok(geo.at(-1).right <= 1440, 'Done is cut off');
        const label = await page.locator('.kanban-rail[data-col="tr-done"] .kanban-rail-label').evaluate(e => getComputedStyle(e).writingMode);
        assert.equal(label, 'vertical-rl');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, 'the page scrolls sideways');
    });

    test('the compact card: two-line title, an id / PR / state line, one flags line; no branch on the face', async () => {
        ready();
        const card = page.locator('#kanban-board .wcard[data-task-id="CMX-303"]');
        assert.equal(await card.locator('.wcard-title').evaluate(e => getComputedStyle(e).webkitLineClamp), '2');
        assert.deepEqual(await card.locator('.wcard-meta > span').allTextContents(), ['CMX-303', '#838', ' running']);
        assert.equal((await card.locator('.wcard-flags').textContent()).replace(/\s+/g, ' ').trim(), 'rework 1 risk high CI failing');
        assert.equal(await page.locator('#kanban-board').evaluate(b => b.textContent.includes('liavacc/')), false, 'a branch is on a card');
        assert.equal(await page.locator('#kanban-board .kanban-delete-btn, #kanban-board .kanban-requeue-btn').count(), 0);
    });

    test('the title opens the switcher: a dropdown under it, with a search field, All projects first and each project\'s counts', async () => {
        ready();
        await page.locator('.work-head-title').click();
        const pop = page.getByRole('dialog', { name: 'Switch project' });
        await pop.waitFor({ timeout: 5000 });
        await page.waitForTimeout(250);
        await shot(page, 'after-switcher');
        const [t, p] = [await seen(page, '.work-head-title'), await pop.boundingBox()];
        assert.ok(p.y >= t.bottom && p.y - t.bottom < 16, 'the switcher is not anchored under the title');
        assert.ok(Math.abs(p.x - t.left) < 12, 'the switcher is not aligned to the title');
        assert.ok(p.width < 400, 'the switcher is a sheet, not a dropdown');
        assert.equal(await pop.getByRole('searchbox', { name: 'Search projects' }).count(), 1);
        assert.deepEqual(await pop.locator('.wproj-name').allTextContents(), ['All projects', 'chelamux', 'lean-alpha']);
        assert.deepEqual(await pop.locator('.wproj-counts').allTextContents(), ['2 running · 8 queued', '2 running · 6 queued', '0 running · 2 queued']);
        await pop.getByRole('radio').nth(2).click();
        await pop.waitFor({ state: 'detached', timeout: 5000 });
        assert.equal(await page.locator('.work-head-project').textContent(), 'lean-alpha');
        await page.locator('.work-head-title').click();
        await page.getByRole('dialog', { name: 'Switch project' }).getByRole('radio').first().click();
        await page.getByRole('dialog', { name: 'Switch project' }).waitFor({ state: 'detached', timeout: 5000 });
    });

    test('a click opens the Done rail as a column at the right edge, and it is still open after a reload', async () => {
        ready();
        await page.locator('.kanban-rail[data-col="tr-done"]').click();
        await page.waitForSelector('.kanban-col.kanban-col-tr-done', { timeout: 5000 });
        const done = await seen(page, '.kanban-col.kanban-col-tr-done');
        assert.ok(done.right <= 1440 && Math.round(done.width) === 320, `Done opened at ${done.left}..${done.right}`);
        await page.reload();
        await openWork(page, '.wcard');
        assert.equal(await page.locator('.kanban-col.kanban-col-tr-done').count(), 1, 'Done folded back to a rail on reload');
        await page.locator('.kanban-col-tr-done .kanban-col-collapse').click();
        await page.waitForSelector('.kanban-rail[data-col="tr-done"]', { timeout: 5000 });
    });

    test('⋯ → Rows: a table with ID, title, state, PR, CI, risk and age, grouped by status; Pause all agents confirms on top', async () => {
        ready();
        await page.locator('#work-more-btn').click();
        const menu = page.locator('.work-head-menu');
        await menu.waitFor({ timeout: 5000 });
        await page.waitForTimeout(200);
        await shot(page, 'after-menu');
        await menu.locator('[data-layout="rows"]').click();
        await page.keyboard.press('Escape');
        await page.waitForSelector('table#work-table', { timeout: 5000 });
        await page.waitForTimeout(200);
        await shot(page, 'after-rows');
        assert.deepEqual(await page.locator('#work-table thead th').allTextContents(), ['ID', 'Title', 'State', 'PR', 'CI', 'Risk', 'Age', 'Actions']);
        assert.deepEqual(await page.locator('#work-table .wtable-group-btn .wrows-label').allTextContents(), ['In Progress', 'In Review', 'Todo']);
        const row = await page.locator('#work-table .wtable-row[data-task-id="CMX-303"] td').allTextContents();
        assert.deepEqual(row.slice(0, 6), ['CMX-303', 'Nightly sweep 1/3: the scheduler (covered window, key store, daemon wiring)', 'running', '#838', 'failing', 'high']);
        await page.locator('#work-table .wtable-sort[data-sort="id"]').click();
        assert.equal(await page.locator('#work-table th.wtable-th-id').getAttribute('aria-sort'), 'ascending');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false, 'the page scrolls sideways');
        // back to the Board, then ⋯ Pause all agents asks first, over the page
        await page.locator('#work-more-btn').click();
        await menu.locator('[data-layout="board"]').click();
        await menu.locator('[data-action="pause-all"]').click();
        const confirm = page.getByRole('dialog', { name: 'Pause all agents?' });
        await confirm.waitFor({ timeout: 5000 });
        const centreInside = await confirm.evaluate(d => { const r = d.getBoundingClientRect(); return d.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); });
        assert.equal(centreInside, true, 'the confirm is not on top');
        await confirm.getByRole('button', { name: 'Cancel' }).click();
        await confirm.waitFor({ state: 'detached', timeout: 5000 });
        assert.deepEqual(errors, []);
    });
});
