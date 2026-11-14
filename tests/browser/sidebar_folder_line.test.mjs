// CMX-377 (Liav, 11-14) — the sidebar row's FOLDER LINE on a phone-width viewport, in a real
// Chromium: jsdom has no layout, so these are the facts only a layout engine can measure.
//   - ungrouped, every row draws its folder line UNDER its title, inside the row;
//   - neither line overlaps the row's ⋯ button, which stays hit-testable;
//   - a long title and a long folder each truncate on their own: no horizontal scroll,
//     not in the drawer and not on the page;
//   - grouped by folder (the default), no folder line is drawn at all.
// The jsdom half (features/sidebar/tests/Sidebar.test.tsx) holds what each row SAYS.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard, DRAWER_OPENER } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const W = 390, H = 844;
const LONG = 'a-very-long-session-title-that-never-fits-the-phone-drawer-on-one-line';
const AGENTS = [
    { name: 'home', window_id: '@1', online: true, session_status: 'idle', cwd: '/home/u', cwd_is_home: true, ai_title: LONG },
    { name: 'deep', window_id: '@2', online: true, session_status: 'busy', ai_title: 'Short',
      cwd: '/srv/an-extremely-long-folder-name-that-will-certainly-not-fit-on-one-phone-line' },
    { name: 'wt', window_id: '@3', online: true, session_status: 'idle', ai_title: LONG,
      cwd: '/home/u/.chela/worktrees/chelamux/CMX-374' },
];
const api = { '/api/agents': AGENTS };
const view = groupBy => ({ chela_sb_view: JSON.stringify({ groupBy, activity: 'all' }) });

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

describe(`CMX-377 phone ${W}×${H}, ungrouped: the folder line`, { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: W, height: H, touch: true, storage: view('none'), api }));
        await page.waitForSelector('.agent-row[data-agent="wt"] .ar-folder-line', { state: 'attached', timeout: 15000 });
        await openDrawer(page);
    });
    after(() => context && context.close());

    test('every row draws its folder line, under its title and inside the row', async () => {
        ready();
        const rows = await page.evaluate(() => [...document.querySelectorAll('.sidebar .agent-row')].map(r => {
            const box = el => el && el.getBoundingClientRect();
            const row = box(r), name = box(r.querySelector('.agent-row-name')), folder = box(r.querySelector('.ar-folder-line'));
            return { agent: r.dataset.agent, text: r.querySelector('.ar-folder-line')?.textContent ?? null, row, name, folder };
        }));
        assert.deepEqual(rows.map(r => [r.agent, r.text]).sort(), [
            ['deep', 'an-extremely-long-folder-name-that-will-certainly-not-fit-on-one-phone-line'],
            ['home', '~'], ['wt', 'chelamux · CMX-374'],
        ]);
        for (const r of rows) {
            assert.ok(r.folder.height > 0, `${r.agent}: the folder line has no height`);
            assert.ok(r.folder.top >= r.name.bottom - 0.5, `${r.agent}: the folder line overlaps the title line`);
            assert.ok(r.folder.bottom <= r.row.bottom + 0.5 && r.folder.right <= r.row.right + 0.5,
                `${r.agent}: the folder line spills out of its row`);
        }
    });

    test('neither line overlaps the ⋯ button, which stays hit-testable', async () => {
        ready();
        const out = await page.evaluate(() => [...document.querySelectorAll('.sidebar .agent-row')].map(r => {
            const more = r.querySelector('.row-more').getBoundingClientRect();
            const hits = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
            const name = r.querySelector('.agent-row-name').getBoundingClientRect();
            const folder = r.querySelector('.ar-folder-line').getBoundingClientRect();
            const top = document.elementFromPoint(more.left + more.width / 2, more.top + more.height / 2);
            return { agent: r.dataset.agent, name: hits(name, more), folder: hits(folder, more), hit: !!top?.closest('.row-more') };
        }));
        for (const o of out) assert.deepEqual(o, { agent: o.agent, name: false, folder: false, hit: true });
    });

    test('long titles and folders truncate on their own: no horizontal scroll', async () => {
        ready();
        const m = await page.evaluate(() => {
            const list = document.querySelector('#sidebar-agents');
            const clipped = sel => [...document.querySelectorAll(sel)].map(e => e.scrollWidth > e.clientWidth);
            return {
                page: document.documentElement.scrollWidth - window.innerWidth,
                list: list.scrollWidth - list.clientWidth,
                sidebar: document.querySelector('.sidebar').scrollWidth - document.querySelector('.sidebar').clientWidth,
                names: clipped('.sidebar .agent-row[data-agent="home"] .agent-row-name'),
                folders: clipped('.sidebar .agent-row[data-agent="deep"] .ar-folder-line'),
                ellipsis: getComputedStyle(document.querySelector('.ar-folder-line')).textOverflow,
            };
        });
        assert.deepEqual(m, { page: 0, list: 0, sidebar: 0, names: [true], folders: [true], ellipsis: 'ellipsis' });
    });
});

describe(`CMX-377 phone ${W}×${H}, grouped by folder: no folder line`, { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: W, height: H, touch: true, storage: view('folder'), api }));
        await page.waitForSelector('.agent-row[data-agent="wt"]', { state: 'attached', timeout: 15000 });
    });
    after(() => context && context.close());

    test('the group header names the folder, so no row repeats it', async () => {
        ready();
        const n = await page.evaluate(() => [document.querySelectorAll('.sidebar .agent-row').length,
            document.querySelectorAll('.sidebar .ar-folder-line').length]);
        assert.deepEqual(n, [3, 0]);
    });
});
