// CMX-390 (Liav, 11-17): child sessions nest under their parent, indented with a guide line.
// At phone width the indent must never push a row's ⋯ off-screen: it stops growing after
// three levels (Sidebar.tsx INDENT_LEVELS, style.css `.tree-kids`). jsdom has no layout, so
// this is measured in a real Chromium; what each row SAYS is the jsdom half,
// chela/dashboard/frontend/src/features/sidebar/tests/tree.test.tsx.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard, DRAWER_OPENER } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const W = 390, H = 844;
const LONG = 'a-very-long-session-title-that-never-fits-the-phone-drawer-on-one-line';
// A chain six deep, every title too long for the drawer: the deepest rows are the ones an
// uncapped indent would push off the right edge.
const DEPTH = 6;
const AGENTS = Array.from({ length: DEPTH }, (_, i) => ({
    name: `s${i}`, window_id: `@${i + 1}`, online: true, claude_running: true, session_status: 'idle',
    cwd: '/srv/code/alpha', ai_title: `${i} ${LONG}`, session_id: `S-${i}`, parent_session_id: i ? `S-${i - 1}` : null,
}));
const storage = { chela_sb_view: JSON.stringify({ groupBy: 'none', activity: 'all' }) };

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

describe(`CMX-390 phone ${W}×${H}: a deep tree of child sessions`, { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: W, height: H, touch: true, storage,
            api: { '/api/agents': AGENTS } }));
        await page.waitForSelector(`.agent-row[data-agent="s${DEPTH - 1}"]`, { state: 'attached', timeout: 15000 });
        await page.locator(DRAWER_OPENER).tap();
        await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
            null, { timeout: 5000 });
    });
    after(() => context && context.close());

    test('each child is indented under its parent, and the indent stops growing after three levels', async () => {
        ready();
        const lefts = await page.evaluate(n => Array.from({ length: n }, (_, i) =>
            document.querySelector(`.agent-row[data-agent="s${i}"]`).getBoundingClientRect().left), DEPTH);
        const step = lefts[1] - lefts[0];
        assert.ok(step >= 8, `a child is indented (${step}px)`);
        assert.ok(lefts[2] - lefts[1] >= 8 && lefts[3] - lefts[2] >= 8, `levels 2 and 3 indent further: ${lefts}`);
        assert.deepEqual(lefts.slice(3).map(x => Math.round(x - lefts[3])), [0, 0, 0], `capped from level 3: ${lefts}`);
    });

    test('every row\'s ⋯ stays on screen, inside its row and hit-testable; no horizontal scroll', async () => {
        ready();
        const m = await page.evaluate(n => {
            const rows = Array.from({ length: n }, (_, i) => {
                const r = document.querySelector(`.agent-row[data-agent="s${i}"]`);
                const row = r.getBoundingClientRect(), more = r.querySelector('.row-more').getBoundingClientRect();
                const top = document.elementFromPoint(more.left + more.width / 2, more.top + more.height / 2);
                return { i, inRow: more.right <= row.right + 0.5, onScreen: more.right <= window.innerWidth, hit: !!top?.closest('.row-more') };
            });
            const list = document.querySelector('#sidebar-agents');
            return { rows, page: document.documentElement.scrollWidth - window.innerWidth, list: list.scrollWidth - list.clientWidth };
        }, DEPTH);
        for (const r of m.rows) assert.deepEqual(r, { i: r.i, inRow: true, onScreen: true, hit: true });
        assert.deepEqual([m.page, m.list], [0, 0]);
    });

    test('the parent\'s chevron folds its whole subtree', async () => {
        ready();
        await page.locator('.agent-row[data-agent="s0"] .tree-toggle').tap();
        await page.waitForFunction(() => !document.querySelector('.agent-row[data-agent="s1"]'), null, { timeout: 5000 });
        const left = await page.evaluate(() => [...document.querySelectorAll('#sidebar-agents .agent-row')].map(r => r.dataset.agent));
        assert.deepEqual(left, ['s0']);
    });
});
