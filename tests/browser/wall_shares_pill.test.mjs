// CMX-422 — the active-shares pill vs the Wall toolbar's "+ New shell",
// MEASURED in a real Chromium (see dashboard.test.mjs for why a browser).
//
// .safety-float is position:fixed in the top-right corner, which is exactly
// where the Wall toolbar row puts "+ New shell"; with a share live the pill
// was drawn ON TOP of the button. On desktop the row now carries its own copy
// of the pill (#term-shares) in normal flow, left of "+ New shell".
//
//  1. Shared, 1440×900: the pill's box and "+ New shell"'s box don't
//     intersect, the pill is left of the button, at the row's control height.
//  2. ⭐ Both are clickable: the topmost element at each one's centre is itself.
//  3. No share: "+ New shell" sits where it does with a share (flush right in
//     the row) and no pill takes space.
//  4. Off the Wall the floating kill-switch is still on screen (it is a safety
//     control: a live share must never go unseen on another tab).
//  5. Shared, 390×844: the pill and the phone's "+" don't intersect, both
//     clickable.
//
// Harness: tests/browser/fixture.mjs — never a live dashboard or daemon.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, routeFixture, AGENTS, ORIGIN } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// openDashboard, plus a `shared` switch: the fixture always shares @2, so the
// no-share case overrides the two endpoints that say so (a route registered
// later runs first).
async function open({ width, height, shared }) {
    const context = await browser.newContext({ viewport: { width, height } });
    await context.addInitScript(() => {
        try {
            localStorage.setItem('pc_term_mode', 'wall');
            localStorage.setItem('pc_wall_preset', JSON.stringify({ cols: 2, rows: 2 }));
        } catch (e) { /* defaults still boot the wall */ }
    });
    await routeFixture(context);
    if (!shared) {
        const json = body => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
        await context.route(ORIGIN + '/api/term/shared', r => r.fulfill(json({})));
        await context.route(ORIGIN + '/api/agents', r => r.fulfill(json(AGENTS.map(a => ({ ...a, shared: false })))));
    }
    const page = await context.newPage();
    await page.goto(ORIGIN + '/');
    await page.evaluate(() => document.fonts.ready);
    await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
    if (shared) await page.waitForSelector('#btn-shares:not([hidden])', { state: 'attached', timeout: 15000 });
    await stable(page);
    return { context, page };
}

// Same geometry for 500ms straight: the pill lights on a status tick and the
// preset fit lands after first render.
async function stable(page) {
    await page.evaluate(() => { window.__cmx422Sig = null; });
    await page.waitForFunction(() => {
        const sig = [...document.querySelectorAll('.term-toolbar, .term-toolbar button, .safety-float, .safety-float button, .mobile-new-fab')]
            .map(e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join(','); })
            .join('|');
        const now = performance.now();
        if (window.__cmx422Sig !== sig) { window.__cmx422Sig = sig; window.__cmx422At = now; }
        return now - window.__cmx422At >= 500;
    }, null, { timeout: 15000, polling: 100 });
}

// The pill the viewer actually sees (whichever copy is rendered), "+ New
// shell" (or the phone's "+"), and what the browser hit-tests at each centre.
const measure = page => page.evaluate(() => {
    const shown = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const info = e => {
        if (!shown(e)) return null;
        const r = e.getBoundingClientRect();
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { id: e.id, left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height,
            hitsSelf: !!top && (top === e || e.contains(top)) };
    };
    const pills = ['term-shares', 'btn-shares'].map(id => info(document.getElementById(id))).filter(Boolean);
    const newShell = info(document.getElementById('term-new-shell')) || info(document.querySelector('.mobile-new-fab'));
    const row = document.querySelector('.term-toolbar');
    const rr = row.getBoundingClientRect();
    return {
        pills, newShell,
        row: { top: rr.top, bottom: rr.bottom, right: rr.right - parseFloat(getComputedStyle(row).paddingRight) },
        ctl: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ctl-h-sm')),
        gap: parseFloat(getComputedStyle(row).columnGap),
    };
});

const intersects = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

function assertApart(m) {
    assert.ok(m.newShell, '"+ New shell" is not rendered');
    assert.ok(m.pills.length >= 1, 'no active-shares pill is rendered while a session is shared');
    for (const p of m.pills) {
        assert.ok(!intersects(p, m.newShell),
            `#${p.id} ${JSON.stringify(p)} overlaps "+ New shell" ${JSON.stringify(m.newShell)}`);
    }
}

function assertClickable(m) {
    for (const p of m.pills) assert.ok(p.hitsSelf, `#${p.id} is not the topmost element at its own centre`);
    assert.ok(m.newShell.hitsSelf, `"+ New shell" (#${m.newShell.id}) is covered at its own centre`);
}

describe('CMX-422 1440×900, a share live: the pill sits beside "+ New shell"', { skip: why }, () => {
    let context, page, m, offWall;
    const ready = setup(async () => {
        ({ context, page } = await open({ width: 1440, height: 900, shared: true }));
        m = await measure(page);
        await page.evaluate(() => window.chela.selectView('work'));
        await stable(page);
        offWall = await page.evaluate(() => {
            const b = document.getElementById('btn-shares');
            const r = b.getBoundingClientRect();
            const top = r.width ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) : null;
            return { width: r.width, height: r.height, hitsSelf: !!top && b.contains(top) };
        });
    });
    after(() => context && context.close());

    test('exactly one pill shows, and its box does not intersect "+ New shell"', () => {
        ready();
        assert.equal(m.pills.length, 1, `pills shown: ${m.pills.map(p => p.id).join(', ')}`);
        assertApart(m);
    });

    test('the pill is in the row, left of "+ New shell" by the row\'s gap, at the row\'s control height', () => {
        ready();
        const [p] = m.pills;
        assert.equal(p.id, 'term-shares', 'the in-row pill is not the one shown');
        assert.ok(p.top >= m.row.top - 0.5 && p.bottom <= m.row.bottom + 0.5, 'the pill is outside the toolbar row');
        assert.ok(Math.abs(m.newShell.left - p.right - m.gap) <= 0.5,
            `${m.newShell.left - p.right}px between the pill and "+ New shell", the row's gap is ${m.gap}px`);
        assert.ok(Math.abs(p.height - m.ctl) <= 0.5, `pill ${p.height}px ≠ --ctl-h-sm ${m.ctl}px`);
        assert.ok(Math.abs(m.newShell.height - m.ctl) <= 0.5, `"+ New shell" ${m.newShell.height}px ≠ --ctl-h-sm ${m.ctl}px`);
    });

    test('⭐ both are clickable: the topmost element at each centre is itself', () => {
        ready();
        assertClickable(m);
    });

    test('off the Wall, the floating kill-switch is back on screen and clickable', () => {
        ready();
        assert.ok(offWall.width > 0 && offWall.height > 0, 'the floating #btn-shares has no box on another tab');
        assert.ok(offWall.hitsSelf, 'the floating #btn-shares is covered on another tab');
    });
});

describe('CMX-422 1440×900, no share: "+ New shell" keeps its spot', { skip: why }, () => {
    let ctxA, ctxB, none, live;
    const ready = setup(async () => {
        let page;
        ({ context: ctxA, page } = await open({ width: 1440, height: 900, shared: false }));
        none = await measure(page);
        ({ context: ctxB, page } = await open({ width: 1440, height: 900, shared: true }));
        live = await measure(page);
    });
    after(async () => { if (ctxA) await ctxA.close(); if (ctxB) await ctxB.close(); });

    test('no pill takes space and "+ New shell" is flush right in the row', () => {
        ready();
        assert.equal(none.pills.length, 0, `a pill shows with no share: ${none.pills.map(p => p.id)}`);
        assert.ok(Math.abs(none.newShell.right - none.row.right) <= 0.5,
            `"+ New shell" ends at ${none.newShell.right}, the row's content at ${none.row.right}`);
        assert.ok(none.newShell.hitsSelf, '"+ New shell" is covered with no share');
    });

    test('a live share does not move "+ New shell"', () => {
        ready();
        for (const k of ['left', 'top', 'width', 'height']) {
            assert.ok(Math.abs(none.newShell[k] - live.newShell[k]) <= 0.5,
                `"+ New shell" ${k} ${none.newShell[k]} without a share vs ${live.newShell[k]} with one`);
        }
    });
});

describe('CMX-422 390×844, a share live: the pill and the phone "+" stay apart', { skip: why }, () => {
    let context, m;
    const ready = setup(async () => {
        let page;
        ({ context, page } = await open({ width: 390, height: 844, shared: true }));
        m = await measure(page);
    });
    after(() => context && context.close());

    test('the pill does not intersect the phone\'s "+"', () => {
        ready();
        assertApart(m);
    });

    test('⭐ both are clickable', () => {
        ready();
        assertClickable(m);
    });
});
