// CMX-422 — the active-shares pill vs the Wall toolbar's "+ New shell",
// MEASURED in a real Chromium (see dashboard.test.mjs for why a browser).
//
// .safety-float is position:fixed in the top-right corner, which is exactly
// where the Wall toolbar row puts "+ New shell"; with a share live the pill
// was drawn ON TOP of the button. On desktop the row now carries its own copy
// of the pill (#term-shares) in normal flow, left of "+ New shell".
//
//  CMX-135: "+ New shell" is gone and the pending-approvals pill got the same
//  in-row copy (#term-approvals). At 1440 / 725 / 390, with and without a
//  share + approvals: each pill shows exactly when it has something to say;
//  none intersects a row control, the phone's ☰ / "+" or another pill; each
//  is clickable; on desktop each sits in the row (never empty; a phone's is
//  0px). Off the Wall the floating kill-switch is still on screen.
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
async function open({ width, height, shared, approvals = shared }) {
    const context = await browser.newContext({ viewport: { width, height } });
    await context.addInitScript(() => {
        try {
            localStorage.setItem('pc_term_mode', 'wall');
            localStorage.setItem('pc_wall_preset', JSON.stringify({ cols: 2, rows: 2 }));
        } catch (e) { /* defaults still boot the wall */ }
    });
    const requests = approvals ? [{ id: 'r1', kind: 'override', label: 'CMX-1' }, { id: 'r2', kind: 'release', version: '1.0.0' }] : [];
    await routeFixture(context, { '/api/overrides': { requests } });
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
    if (approvals) await page.waitForSelector('#btn-approvals:not([hidden])', { state: 'attached', timeout: 15000 });
    await stable(page);
    return { context, page };
}

// Same geometry for 500ms straight: the pill lights on a status tick and the
// preset fit lands after first render.
async function stable(page) {
    await page.evaluate(() => { window.__cmx422Sig = null; });
    await page.waitForFunction(() => {
        const sig = [...document.querySelectorAll('.term-toolbar, .term-toolbar button, .safety-float, .safety-float button, .mobile-new-fab, .mobile-menu-fab')]
            .map(e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join(','); })
            .join('|');
        const now = performance.now();
        if (window.__cmx422Sig !== sig) { window.__cmx422Sig = sig; window.__cmx422At = now; }
        return now - window.__cmx422At >= 500;
    }, null, { timeout: 15000, polling: 100 });
}

// Every pill copy the viewer actually sees, every other control the row shows
// (plus the phone's ☰ and "+"), and what the browser hit-tests at each centre.
const PILLS = ['term-shares', 'btn-shares', 'term-approvals', 'btn-approvals'];
const measure = page => page.evaluate(PILLS => {
    const shown = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const info = e => {
        const r = e.getBoundingClientRect();
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { id: e.id || e.className, left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height,
            hitsSelf: !!top && (top === e || e.contains(top)) };
    };
    const pills = PILLS.map(id => document.getElementById(id)).filter(shown).map(info);
    const controls = [...document.querySelectorAll('.term-toolbar button, .mobile-new-fab, .mobile-menu-fab')]
        .filter(e => shown(e) && !PILLS.includes(e.id)).map(info);
    const row = document.querySelector('.term-toolbar');
    const rr = row.getBoundingClientRect();
    return {
        pills, controls, rowControls: [...row.querySelectorAll('button')].filter(e => shown(e) && !PILLS.includes(e.id)).length,
        row: { top: rr.top, bottom: rr.bottom, height: rr.height },
        ctl: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ctl-h-sm')),
    };
}, PILLS);

const intersects = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

for (const [width, height] of [[1440, 900], [725, 900], [390, 844]]) {
    for (const live of [true, false]) {
        describe(`CMX-135 ${width}×${height}, ${live ? 'a share + 2 approvals live' : 'nothing live'}`, { skip: why }, () => {
            let context, m, offWall;
            const ready = setup(async () => {
                let page;
                ({ context, page } = await open({ width, height, shared: live }));
                m = await measure(page);
                await page.evaluate(() => window.chela.selectView('work'));
                await stable(page);
                offWall = await measure(page);
            });
            after(() => context && context.close());

            test('each pill shows exactly when it has something to say, one copy of it', () => {
                ready();
                const ids = m.pills.map(p => p.id);
                assert.equal(ids.filter(id => /shares/.test(id)).length, live ? 1 : 0, `shares pills: ${ids}`);
                assert.equal(ids.filter(id => /approvals/.test(id)).length, live ? 1 : 0, `approvals pills: ${ids}`);
            });

            test('⭐ no pill intersects a toolbar control or another pill, and each is clickable', () => {
                ready();
                assert.ok(m.controls.length >= 1, 'no toolbar control or "+" is shown to measure against');
                for (const p of m.pills) {
                    for (const c of [...m.controls, ...m.pills.filter(q => q !== p)]) {
                        assert.ok(!intersects(p, c), `#${p.id} ${JSON.stringify(p)} overlaps ${c.id} ${JSON.stringify(c)}`);
                    }
                    assert.ok(p.hitsSelf, `#${p.id} is not the topmost element at its own centre`);
                }
                for (const c of m.controls) assert.ok(c.hitsSelf, `${c.id} is covered at its own centre`);
            });

            test('desktop: the pills sit in the row at its control height; the row is never empty', () => {
                ready();
                if (width <= 768) {
                    assert.equal(m.row.height, 0, `the phone's empty toolbar row is ${m.row.height}px tall`);
                    return;
                }
                assert.ok(m.rowControls >= 1, 'the Wall toolbar row shows no control of its own');
                for (const p of m.pills) {
                    assert.match(p.id, /^term-/, `#${p.id} floats over the Wall instead of sitting in its row`);
                    assert.ok(p.top >= m.row.top - 0.5 && p.bottom <= m.row.bottom + 0.5, `#${p.id} is outside the toolbar row`);
                    assert.ok(Math.abs(p.height - m.ctl) <= 0.5, `#${p.id} ${p.height}px ≠ --ctl-h-sm ${m.ctl}px`);
                }
            });

            test('off the Wall, the floating copies are back on screen and clickable', () => {
                ready();
                assert.deepEqual(offWall.pills.map(p => p.id), live ? ['btn-shares', 'btn-approvals'] : []);
                for (const p of offWall.pills) assert.ok(p.hitsSelf, `the floating #${p.id} is covered on another tab`);
            });
        });
    }
}

