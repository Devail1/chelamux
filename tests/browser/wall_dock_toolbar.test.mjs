// CMX-397 — the Wall's minimized dock and its collapsible layout toolbar,
// MEASURED in a real Chromium (see dashboard.test.mjs for why a browser).
//
//  1. The dock is ONE slim row (≈30px, not ≈38px) sitting ≈8px off the viewport
//     bottom (not 18px), and the panes still fill EXACTLY down to it: no overlap,
//     no gap beyond the dock's own top margin + the fill's hair. And with nothing
//     minimized, the dock is gone and the Wall fills to the bottom as before.
//  2. The "Grid:" toolbar collapses to one control that is narrower than the
//     full row, WITHOUT moving the Wall (panes start at the same y), and every
//     preset still works from the expanded row.
//
// Harness: tests/browser/fixture.mjs — never a live dashboard or daemon.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { ORIGIN, launchChromium, routeFixture } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// A 1440×900 Wall with the given localStorage seeded before boot.
async function openWall(storage) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(s => {
        try { for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); } catch (e) { /* noop */ }
    }, { pc_term_mode: 'wall', ...storage });
    await routeFixture(context);
    const page = await context.newPage();
    await page.goto(ORIGIN + '/');
    await page.waitForFunction(() => document.querySelectorAll('#term-stage .grid-stack-item').length >= 4,
        null, { timeout: 15000 });
    await stable(page);
    return { context, page };
}

// Same geometry (panes, dock, toolbar) for 500ms straight: the preset fit and
// the dock refit land AFTER first render. The signature is reset first, so a
// wait right after a click always observes a full 500ms from NOW rather than
// inheriting a quiet period that ended before the click's reflow began.
async function stable(page) {
    await page.evaluate(() => { window.__cmx397Sig = null; });
    await page.waitForFunction(() => {
        const sig = [...document.querySelectorAll('#canvas, #term-stage .grid-stack-item, #term-min-dock, #term-wall-grid')]
            .map(e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join(','); })
            .join('|');
        const now = performance.now();
        if (window.__cmx397Sig !== sig) { window.__cmx397Sig = sig; window.__cmx397At = now; }
        return now - window.__cmx397At >= 500;
    }, null, { timeout: 15000, polling: 100 });
}

const geometry = page => page.evaluate(() => {
    const dock = document.getElementById('term-min-dock');
    const d = dock.getBoundingClientRect();
    const canvas = document.getElementById('canvas');
    const cb = canvas.getBoundingClientRect();
    const panes = [...document.querySelectorAll('#term-stage .grid-stack-item')]
        .filter(e => e.getClientRects().length).map(e => e.getBoundingClientRect());
    const chips = [...dock.querySelectorAll('.min-chip')].map(c => ({
        h: c.getBoundingClientRect().height, font: getComputedStyle(c).fontSize,
    }));
    return {
        dockShown: dock.getClientRects().length > 0 && d.height > 0,
        dockTop: d.top, dockBottom: d.bottom, dockH: d.height, chips,
        paneCount: panes.length,
        paneTop: Math.min(...panes.map(r => r.top)),
        paneBottom: Math.max(...panes.map(r => r.bottom)),
        canvasContentBottom: cb.bottom - parseFloat(getComputedStyle(canvas).paddingBottom),
        canvasPadding: getComputedStyle(canvas).padding,
        canvasScrolls: canvas.scrollHeight > canvas.clientHeight + 1,
        vh: innerHeight,
    };
});

describe('CMX-397 1440×900: the minimized dock is one slim row near the bottom, panes fill to it', { skip: why }, () => {
    let context, g;
    const ready = setup(async () => {
        // 2 panes on the wall (a 2-column preset: one row, full height) + 2 chips.
        ({ context } = await openWall({
            pc_wall_preset: JSON.stringify({ cols: 2, rows: 1 }),
            pc_wall_minimized: JSON.stringify(['@3', '@4']),
        }));
        g = await geometry(context.pages()[0]);
    });
    after(() => context && context.close());

    test('the dock shows 2 chips beside 2 wall panes', () => {
        ready();
        assert.ok(g.dockShown, 'the dock is showing');
        assert.equal(g.chips.length, 2, 'two minimized chips');
        assert.equal(g.paneCount, 2, 'two panes left on the wall');
    });

    test('the dock is ≤ 32px tall (one row of chips, not ~38px)', () => {
        ready();
        assert.ok(g.dockH <= 32, `dock is ${g.dockH}px tall`);
    });

    test('the chips keep 12px text and a ≥ 24px click target', () => {
        ready();
        for (const c of g.chips) {
            assert.equal(c.font, '12px');
            assert.ok(c.h >= 24, `a chip is only ${c.h}px tall — too small to hit`);
        }
    });

    test('the gap under the dock is ≤ 10px (not the canvas\'s 18px)', () => {
        ready();
        const under = g.vh - g.dockBottom;
        assert.ok(under >= 0, `the dock overflows the viewport by ${-under}px`);
        assert.ok(under <= 10, `${under}px of dead space under the dock`);
    });

    test('the last pane row ends within 8px ABOVE the dock\'s top — no overlap, no gap', () => {
        ready();
        const gap = g.dockTop - g.paneBottom;
        assert.ok(gap >= 0, `panes overlap the dock by ${-gap}px`);
        assert.ok(gap <= 8, `${gap}px gap between the last pane row and the dock`);
    });

    test('the canvas does not scroll, and its top/left/right padding is unchanged', () => {
        ready();
        assert.equal(g.canvasScrolls, false, 'the Wall pushed the canvas into a scroll');
        assert.equal(g.canvasPadding, '18px 20px');
    });
});

describe('CMX-397 1440×900: with NOTHING minimized the dock is gone and the Wall fills to the bottom', { skip: why }, () => {
    let context, g;
    const ready = setup(async () => {
        ({ context } = await openWall({ pc_wall_preset: JSON.stringify({ cols: 2, rows: 1 }) }));
        g = await geometry(context.pages()[0]);
    });
    after(() => context && context.close());

    test('no dock, four panes', () => {
        ready();
        assert.equal(g.dockShown, false, 'the dock must be hidden with no minimized pane');
        assert.equal(g.paneCount, 4);
    });

    test('the panes reach the canvas content bottom (within the fill\'s hair)', () => {
        ready();
        const gap = g.canvasContentBottom - g.paneBottom;
        assert.ok(gap >= 0, `panes overrun the canvas padding by ${-gap}px`);
        assert.ok(gap <= 8, `${gap}px gap under the Wall with no dock`);
        assert.equal(g.canvasScrolls, false);
    });
});

describe('CMX-397 1440×900: the layout toolbar collapses without moving the Wall', { skip: why }, () => {
    let context, page;
    const ready = setup(async () => {
        // Liav's default: Focus ON, no stored expand/collapse choice.
        ({ context, page } = await openWall({
            pc_wall_preset: JSON.stringify({ cols: 2, rows: 1 }),
            pc_wall_focus: '1',
        }));
    });
    after(() => context && context.close());

    const bar = () => page.evaluate(() => {
        const b = document.getElementById('term-wall-grid').getBoundingClientRect();
        const panes = [...document.querySelectorAll('#term-stage .grid-stack-item')]
            .filter(e => e.getClientRects().length).map(e => e.getBoundingClientRect());
        return {
            width: b.width,
            expanded: document.getElementById('term-grid-toggle').getAttribute('aria-expanded'),
            presetsShown: [...document.querySelectorAll('#term-grid-presets .gl-btn')]
                .filter(e => e.getClientRects().length).length,
            paneTop: Math.min(...panes.map(r => r.top)),
        };
    });

    test('collapsed: narrower than the full row, and the panes start at the same y', async () => {
        ready();
        const collapsed = await bar();
        assert.equal(collapsed.expanded, 'false', 'Focus ON + no stored choice starts collapsed');
        assert.equal(collapsed.presetsShown, 0, 'no preset is on screen while collapsed');
        await page.click('#term-grid-toggle');
        await stable(page);
        const open = await bar();
        assert.equal(open.expanded, 'true');
        assert.equal(open.presetsShown, 6, 'expanded, all six presets are on screen');
        assert.ok(collapsed.width < open.width / 2,
            `collapsed toolbar (${collapsed.width}px) is not much narrower than the full row (${open.width}px)`);
        assert.ok(Math.abs(collapsed.paneTop - open.paneTop) <= 0.5,
            `the Wall jumped: panes start at ${collapsed.paneTop} collapsed vs ${open.paneTop} expanded`);
    });

    test('every preset still works from the expanded row: it re-lays the Wall and collapses', async () => {
        ready();
        // Focus owns the layout, so turn it off (from the expanded row) to see presets act.
        if ((await bar()).expanded !== 'true') await page.click('#term-grid-toggle');
        await page.click('#term-focus-btn');
        await stable(page);
        const presets = await page.$$eval('#term-grid-presets .gl-btn',
            bs => bs.map(b => b.getAttribute('onclick').match(/\((\d+), (\d+)/).slice(1).map(Number)));
        assert.equal(presets.length, 6);
        for (const [i, [cols, rows]] of presets.entries()) {
            if ((await bar()).expanded !== 'true') await page.click('#term-grid-toggle');
            await page.click(`#term-grid-presets .gl-btn[data-preset="${i}"]`);
            await stable(page);
            const r = await page.evaluate(() => {
                const panes = [...document.querySelectorAll('#term-stage .grid-stack-item')]
                    .filter(e => e.getClientRects().length).map(e => e.getBoundingClientRect());
                const distinct = xs => xs.sort((a, b) => a - b).filter((x, k, s) => k === 0 || x - s[k - 1] > 2).length;
                return {
                    cols: distinct(panes.map(p => p.left)),
                    expanded: document.getElementById('term-grid-toggle').getAttribute('aria-expanded'),
                    stored: JSON.parse(localStorage.getItem('pc_wall_preset')),
                };
            });
            assert.deepEqual(r.stored, { cols, rows }, `preset ${i} was not applied`);
            assert.equal(r.cols, Math.min(cols, 4), `preset ${i} (${cols}×${rows}) laid out ${r.cols} columns`);
            assert.equal(r.expanded, 'false', `choosing preset ${i} did not collapse the row`);
        }
    });
});
