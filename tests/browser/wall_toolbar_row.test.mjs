// CMX-414 — the Wall's top toolbar row (the layout toggle + "+ New shell"),
// MEASURED in a real Chromium (see dashboard.test.mjs for why a browser).
//
//  1. At 1440×900 the collapsed row is EXACTLY the sidebar head's height, so
//     the first pane's top edge lands on the head's bottom rule.
//  2. The layout toggle and "+ New shell" render at one height, the
//     --ctl-h-sm token's.
//  3. The row's controls sit one pane-gap above the first pane — the same gap
//     the Wall leaves between two panes.
//  4. ⭐ Expanded, the row still shows every one of its controls, unclipped,
//     without changing height.
//  5. Phone: the row stays an empty strip, so the agent pills (CMX-386/404) sit
//     exactly where they did.
//
// Harness: tests/browser/fixture.mjs — never a live dashboard or daemon.
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

// Same geometry for 500ms straight: the preset fit lands AFTER first render.
async function stable(page) {
    await page.evaluate(() => { window.__cmx414Sig = null; });
    await page.waitForFunction(() => {
        const sig = [...document.querySelectorAll('.term-toolbar, .term-toolbar button, #term-stage, #term-stage .grid-stack-item, #term-switcher')]
            .map(e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join(','); })
            .join('|');
        const now = performance.now();
        if (window.__cmx414Sig !== sig) { window.__cmx414Sig = sig; window.__cmx414At = now; }
        return now - window.__cmx414At >= 500;
    }, null, { timeout: 15000, polling: 100 });
}

async function openWall(viewport, storage = {}) {
    const { context, page, errors } = await openDashboard(browser, { ...viewport, storage });
    if (viewport.width > 768) {
        await page.waitForFunction(() => document.querySelectorAll('#term-stage .grid-stack-item').length >= 4,
            null, { timeout: 15000 });
    }
    await stable(page);
    return { context, page, errors };
}

const geometry = page => page.evaluate(() => {
    const rect = e => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height, width: r.width }; };
    const row = document.querySelector('.term-toolbar');
    const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    // Every control the row shows, each checked for clipping: its whole box
    // inside the row's box and the viewport, and the browser's own hit-test
    // STACK at its centre containing it — an ancestor's overflow clip removes
    // an element from that stack. (The stack, not the top hit: .safety-float
    // is a position:fixed overlay that already sits over "+ New shell" on dev
    // whenever a session is shared — a separate issue, not a clip.)
    const controls = [...row.querySelectorAll('button')].filter(shown).map(b => {
        const r = rect(b);
        const stack = document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { id: b.id || b.getAttribute('aria-label') || b.title, ...r, hitsSelf: stack.some(h => h === b || b.contains(h)) };
    });
    const panes = [...document.querySelectorAll('#term-stage .grid-stack-item-content')]
        .filter(e => e.getClientRects().length).map(rect);
    // The Wall's own inter-pane vertical gap: pane tops strictly below another
    // pane in the same column, minus that pane's bottom.
    const gaps = [];
    for (const a of panes) for (const b of panes) {
        if (b.top > a.bottom - 1 && Math.abs(a.left - b.left) < 2) gaps.push(b.top - a.bottom);
    }
    const sw = document.getElementById('term-switcher');
    const canvas = document.getElementById('canvas');
    return {
        head: rect(document.querySelector('.sidebar-head')),
        row: rect(row), rowShown: shown(row), controls,
        toggle: rect(document.getElementById('term-grid-toggle')),
        newShell: rect(document.getElementById('term-new-shell')),
        ctlToken: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ctl-h-sm')),
        expanded: document.getElementById('term-grid-toggle').getAttribute('aria-expanded'),
        firstPaneTop: panes.length ? Math.min(...panes.map(p => p.top)) : null,
        paneGap: gaps.length ? Math.min(...gaps) : null,
        switcherTop: sw.getClientRects().length ? sw.getBoundingClientRect().top : null,
        canvasPadTop: parseFloat(getComputedStyle(canvas).paddingTop),
        paneBottom: panes.length ? Math.max(...panes.map(p => p.bottom)) : null,
        canvasContentBottom: canvas.getBoundingClientRect().bottom - parseFloat(getComputedStyle(canvas).paddingBottom),
        newFabShown: shown(document.querySelector('.mobile-new-fab')),
        vw: innerWidth,
    };
});

describe('CMX-414 1440×900: the collapsed toolbar row matches the sidebar head', { skip: why }, () => {
    let context, g;
    const ready = setup(async () => {
        // Focus ON + no stored choice ⇒ the layout toolbar starts collapsed.
        ({ context } = await openWall({ width: 1440, height: 900 }, { pc_wall_focus: '1' }));
        g = await geometry(context.pages()[0]);
    });
    after(() => context && context.close());

    test('the row is collapsed and on screen', () => {
        ready();
        assert.equal(g.expanded, 'false');
        assert.ok(g.rowShown && g.row.height > 0, 'the toolbar row is not rendered');
    });

    test('the row is exactly as tall as the sidebar head (±1px) and aligned with it', () => {
        ready();
        assert.ok(Math.abs(g.row.height - g.head.height) <= 1,
            `toolbar row ${g.row.height}px vs sidebar head ${g.head.height}px`);
        assert.ok(Math.abs(g.row.top - g.head.top) <= 1, `row starts at ${g.row.top}, head at ${g.head.top}`);
    });

    test('the toggle and "+ New shell" render at one height: --ctl-h-sm', () => {
        ready();
        assert.ok(g.ctlToken > 0, '--ctl-h-sm is not defined');
        assert.ok(Math.abs(g.toggle.height - g.newShell.height) <= 0.5,
            `toggle ${g.toggle.height}px vs "+ New shell" ${g.newShell.height}px`);
        assert.ok(Math.abs(g.toggle.height - g.ctlToken) <= 0.5, `toggle ${g.toggle.height}px ≠ --ctl-h-sm ${g.ctlToken}px`);
        assert.ok(Math.abs(g.newShell.height - g.ctlToken) <= 0.5, `"+ New shell" ${g.newShell.height}px ≠ --ctl-h-sm ${g.ctlToken}px`);
    });

    test('controls → first pane is the Wall\'s own pane gap, and the first pane starts on the head\'s rule', () => {
        ready();
        assert.ok(g.paneGap !== null, 'the fixture wall has no vertically stacked panes to measure a gap from');
        const gap = g.firstPaneTop - Math.max(g.toggle.bottom, g.newShell.bottom);
        assert.ok(Math.abs(gap - g.paneGap) <= 1, `${gap}px from the controls to the first pane vs ${g.paneGap}px between panes`);
        assert.ok(Math.abs(g.firstPaneTop - g.head.bottom) <= 1,
            `the first pane starts at ${g.firstPaneTop}, the sidebar head ends at ${g.head.bottom}`);
    });
});

describe('CMX-414 1440×900: the height the row gives back goes to the panes', { skip: why }, () => {
    let context, g;
    const ready = setup(async () => {
        ({ context } = await openWall({ width: 1440, height: 900 }));
        g = await geometry(context.pages()[0]);
    });
    after(() => context && context.close());

    // _wallFill leaves a fixed 4px hair and GridStack a 6px item margin under
    // the last row — and nothing else. A floored per-row height used to strand
    // up to rows−1 more px down there, so the pixels the tighter row freed at
    // the top just reappeared as dead space at the bottom.
    test('the Wall ends exactly the fill hair + one pane margin above the canvas bottom', () => {
        ready();
        const slack = g.canvasContentBottom - g.paneBottom;
        assert.ok(Math.abs(slack - (4 + 6)) <= 1, `${slack}px of dead space under the Wall (expected 10px)`);
    });
});

describe('CMX-414 1440×900: ⭐ the EXPANDED toolbar still shows every control, unclipped', { skip: why }, () => {
    let context, page, collapsed, open;
    const ready = setup(async () => {
        ({ context, page } = await openWall({ width: 1440, height: 900 }, { pc_wall_focus: '1' }));
        collapsed = await geometry(page);
        await page.click('#term-grid-toggle');
        await stable(page);
        open = await geometry(page);
    });
    after(() => context && context.close());

    test('expanded: toggle, 6 presets, lock, auto, focus and "+ New shell" all on screen', () => {
        ready();
        assert.equal(open.expanded, 'true');
        const ids = open.controls.map(c => c.id);
        for (const id of ['term-grid-toggle', 'term-lock-btn', 'term-auto-btn', 'term-focus-btn', 'term-new-shell']) {
            assert.ok(ids.includes(id), `${id} is not shown expanded (shown: ${ids.join(', ')})`);
        }
        assert.ok(open.controls.length >= 11, `only ${open.controls.length} controls are shown expanded`);
    });

    test('no expanded control is clipped: inside the row, inside the viewport, and hit-testable', () => {
        ready();
        for (const c of open.controls) {
            assert.ok(c.height > 0 && c.width > 0, `${c.id} has no box`);
            assert.ok(c.top >= open.row.top - 0.5 && c.bottom <= open.row.bottom + 0.5,
                `${c.id} (${c.top}–${c.bottom}) spills out of the row (${open.row.top}–${open.row.bottom})`);
            assert.ok(c.left >= 0 && c.right <= open.vw, `${c.id} runs off the viewport`);
            assert.ok(c.hitsSelf, `${c.id} is clipped at its centre`);
        }
    });

    test('expanding does not change the row\'s height or move the Wall', () => {
        ready();
        assert.ok(Math.abs(open.row.height - collapsed.row.height) <= 0.5,
            `row ${collapsed.row.height}px collapsed vs ${open.row.height}px expanded`);
        assert.ok(Math.abs(open.firstPaneTop - collapsed.firstPaneTop) <= 0.5, 'the Wall jumped on expand');
    });
});

describe('CMX-414 390×844: the phone layout is unchanged', { skip: why }, () => {
    let context, g;
    const ready = setup(async () => {
        ({ context } = await openWall({ width: 390, height: 844 }));
        g = await geometry(context.pages()[0]);
    });
    after(() => context && context.close());

    test('no toolbar control shows on a phone, but the "+" FAB does', () => {
        ready();
        assert.equal(g.controls.length, 0, `phone shows toolbar controls: ${g.controls.map(c => c.id)}`);
        assert.ok(g.newFabShown, 'the phone "+" (CMX-386) is gone');
    });

    test('the pill switcher sits where it always has: the empty row\'s 12px under the canvas padding', () => {
        ready();
        assert.ok(g.switcherTop !== null, 'the pill switcher is not rendered');
        assert.ok(Math.abs(g.switcherTop - (g.canvasPadTop + 12)) <= 0.5,
            `pills at ${g.switcherTop}px, expected ${g.canvasPadTop + 12}px`);
    });
});
