// CMX-306 — on an iPhone, Settings opened from the drawer's gear came up BEHIND the
// still-open drawer (only its "…cations", "Cost" tabs and its × peeked out to the
// right), and the ⌘K palette could not be closed at all.
//
// Causes: (1) the kit's Settings surface draws its box inside the drawer's own scrim
// (.drawer-scrim, z-index 40), under the phone drawer (45); (2) the palette had no
// close button, and its backdrop had no click handler of its own, which iOS needs
// before it sends the click Radix waits for after a touch (the legacy overlays had an
// inline onclick). Fixed in the kit (shared/ui overlays.tsx): every Dialog and Sheet is
// ONE layer (.ui-overlay, --z-overlay) above the drawer (--z-drawer), each draws a
// close button (.ui-close, 44px on a touch screen), the backdrop closes it on a tap,
// and opening any of them shuts the drawer (onOverlayOpen → sidebar closeSidebar()).
//
// Driven with tap() on a touch context at iPhone width, against the committed bundle,
// one row per ported overlay: open it the way a phone does (from the drawer where its
// opener lives there) ⇒ the drawer is shut and the dialog is on top (elementFromPoint at
// its centre and at its × land inside it), its × is a ≥44px target ⇒ a tap on it closes
// it; reopened, a tap on the backdrop closes it.
//
// The harness is Chromium with touch emulation (tests/browser/fixture.mjs): it is the
// browser CI installs; WebKit is not installed here.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
// Screenshots for a PR: CMX306_SHOTS=<dir> pnpm exec node --test tests/browser/overlays_phone.test.mjs
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium, openDashboard, OPEN_DRAWER_IN_PAGE } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const SHOTS = process.env.CMX306_SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

const W = 390, H = 844;

// Something waiting in every queue a pill or a sheet reads, and a board with one card.
const API = {
    '/api/overrides': { requests: [{ id: 'ov1', kind: 'merge', label: 'CMX-1', task_id: 'CMX-1', question: 'merge it?' }] },
    '/api/share-requests': { requests: [{ id: 'rq1', status: 'pending', kind: 'mount', description: '/srv/data', window: 'b-waiting', wid: '@2' }] },
    '/api/dispatcher': { configured: true, workflows: [{ path: '/x/WORKFLOW.md', project_key: 'CMX', tracker_kind: 'linear',
        open_tasks: [{ id: 'CMX-1', title: 'a queued task' }] }] },
};

const drawerShut = page => page.evaluate(() => {
    const sb = document.querySelector('.sidebar');
    return !sb.classList.contains('open') && sb.getBoundingClientRect().right <= 1;
});

async function openDrawer(page) {
    // The ☰'s own click, dispatched: with three safety pills lit (this fixture's queues), the
    // float reaches over the ☰ at 390px, which is not what this suite is about.
    await page.evaluate(`if (!document.querySelector('.sidebar').classList.contains('open')) (${OPEN_DRAWER_IN_PAGE})()`);
    await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
        null, { timeout: 5000 });
}

async function gearItem(page, label) {
    await openDrawer(page);
    await page.locator('#btn-primary-menu').tap();
    await page.getByRole('menuitem', { name: label }).tap();
}

// A palette row, run by a tap: the drawer, the gear, "Jump to…", the query, the first row.
async function paletteRow(page, query) {
    await gearItem(page, 'Jump to…');
    await page.locator('#palette-input').fill(query);
    await page.locator('#palette .palette-item').first().tap();
}

const dialog = (page, name) => page.getByRole('dialog', typeof name === 'string' ? { name, exact: true } : { name });

/** Where the dialog is, what is drawn at its centre and its ×, and the ×'s size. */
function measure(dlg) {
    return dlg.evaluate(d => {
        const r = d.getBoundingClientRect();
        const at = (x, y) => document.elementFromPoint(x, y);
        const cx = Math.min(Math.max(r.left + r.width / 2, 1), innerWidth - 1);
        const cy = Math.min(Math.max(r.top + Math.min(r.height, innerHeight - r.top) / 2, 1), innerHeight - 1);
        const close = d.querySelector('.ui-close');
        const c = close?.getBoundingClientRect();
        // the backdrop: the first spot on a coarse grid where a tap lands on the overlay itself
        const overlay = d.parentElement;
        let backdrop = null;
        for (let y = 4; y < innerHeight && !backdrop; y += 20) {
            for (let x = 4; x < innerWidth && !backdrop; x += 20) if (at(x, y) === overlay) backdrop = { x, y };
        }
        return {
            centreInside: d.contains(at(cx, cy)),
            close: c ? { w: c.width, h: c.height, onTop: close.contains(at(c.left + c.width / 2, c.top + c.height / 2)) } : null,
            backdrop,
        };
    });
}

// Between rows: nothing left open (Escape is the keyboard's close; the rows test touch).
async function reset(page) {
    for (let i = 0; i < 4 && await page.locator('[role="dialog"]').count(); i++) await page.keyboard.press('Escape');
    await page.evaluate(() => { if (document.querySelector('.sidebar').classList.contains('open')) document.getElementById('sidebar-scrim').click(); });
}

// One row per ported overlay: how a phone opens it, its accessible name, and whether any of
// its backdrop shows on a phone (Settings is a full-screen sheet there: no backdrop to tap).
const OVERLAYS = [
    { name: 'Settings', open: p => gearItem(p, 'Settings'), backdrop: false },
    { name: 'Command palette', open: p => gearItem(p, 'Jump to…'), backdrop: true },
    { name: 'Keyboard shortcuts', open: p => paletteRow(p, 'Keyboard shortcuts'), backdrop: true },
    // Its click, not a tap: with the safety pills lit the float (z 47) is drawn over the
    // drawer's head (45), right where this button sits; a follow-up, not this suite's subject.
    { name: 'Decisions', open: async p => { await openDrawer(p); await p.locator('#btn-decisions').evaluate(b => b.click()); }, backdrop: true },
    { name: 'New task', open: p => paletteRow(p, 'New task'), backdrop: true },
    { name: 'Add Scheduled Task', open: p => paletteRow(p, 'Add scheduled task'), backdrop: true },
    { name: 'Share dialog', dialog: /^Share /, open: p => paletteRow(p, 'Share Refactor'), backdrop: true },
    { name: 'Pending approvals', open: async p => { await openDrawer(p); await p.locator('#btn-approvals').tap(); }, backdrop: true },
    { name: 'Access requests', open: async p => { await openDrawer(p); await p.locator('#btn-access-req').tap(); }, backdrop: true },
    { name: 'Active shares', open: async p => { await openDrawer(p); await p.locator('#btn-shares').tap(); }, backdrop: true },
    // the Wall's footer: before the row below leaves the Wall for the Work view
    { name: 'Changed files', open: p => p.locator('.gs-files:visible').first().tap(), backdrop: true },
    // last: it leaves the Wall for the Work view
    { name: 'a queued task', open: async p => { await paletteRow(p, 'Work'); await p.getByText('a queued task').first().tap(); }, backdrop: true },
];

describe(`CMX-306 phone ${W}×${H}, touch: every overlay opens on top and closes by touch`, { skip: why }, () => {
    let page, context, errors;
    const ready = setup(async () => {
        ({ page, context, errors } = await openDashboard(browser, { width: W, height: H, touch: true, api: API }));
        page.setDefaultTimeout(5000);
        await page.waitForSelector('#sidebar-agents .agent-row', { state: 'attached', timeout: 15000 });
    });
    after(() => context && context.close());

    for (const o of OVERLAYS) {
        test(`${o.name}: opened from the drawer ⇒ the drawer shuts and it is on top; its × and its backdrop close it`, async () => {
            ready();
            await reset(page);
            await o.open(page);
            const dlg = dialog(page, o.dialog ?? o.name);
            await dlg.waitFor({ state: 'visible', timeout: 5000 });
            await page.waitForFunction(() => {
                const sb = document.querySelector('.sidebar');
                return !sb.classList.contains('open') && sb.getBoundingClientRect().right <= 1;
            }, null, { timeout: 3000 }).catch(() => {});
            await page.waitForTimeout(250); // the open animations settle
            if (SHOTS) await page.screenshot({ path: join(SHOTS, `after-${o.name.replace(/\W+/g, '-')}.png`) });
            assert.equal(await drawerShut(page), true, `${o.name} opened with the drawer still open`);
            const m = await measure(dlg);
            assert.ok(m, `${o.name}: no dialog by that name`);
            assert.equal(m.centreInside, true, `${o.name}: something else is drawn over its centre`);
            assert.ok(m.close, `${o.name}: no close button`);
            assert.ok(m.close.w >= 44 && m.close.h >= 44, `${o.name}: its × is ${m.close.w}×${m.close.h}, under 44px`);
            assert.equal(m.close.onTop, true, `${o.name}: its × is covered`);

            await dlg.locator('.ui-close').tap();
            await dlg.waitFor({ state: 'detached', timeout: 3000 }).catch(() => {});
            assert.equal(await dlg.count(), 0, `${o.name}: a tap on its × left it open`);

            if (!o.backdrop) return;
            await o.open(page);
            await dlg.waitFor({ state: 'visible', timeout: 5000 });
            await page.waitForTimeout(250);
            const again = await measure(dlg);
            assert.ok(again.backdrop, `${o.name}: no backdrop left to tap at ${W}px`);
            await page.touchscreen.tap(again.backdrop.x, again.backdrop.y);
            await dlg.waitFor({ state: 'detached', timeout: 3000 }).catch(() => {});
            assert.equal(await dlg.count(), 0, `${o.name}: a tap on its backdrop left it open`);
        });
    }

    // The layer order on its own, apart from the drawer shutting: an overlay up, the drawer
    // forced open over it ⇒ where the two overlap, the overlay is what is drawn.
    for (const o of OVERLAYS.filter(x => ['Settings', 'Command palette', 'Pending approvals'].includes(x.name))) {
        test(`${o.name} is drawn above a drawer that is open at the same time`, async () => {
            ready();
            await reset(page);
            await o.open(page);
            const dlg = dialog(page, o.dialog ?? o.name);
            await dlg.waitFor({ state: 'visible', timeout: 5000 });
            await page.evaluate(`if (!document.querySelector('.sidebar').classList.contains('open')) (${OPEN_DRAWER_IN_PAGE})()`);
            await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().left) < 0.5,
                null, { timeout: 5000 });
            const hit = await dlg.evaluate(d => {
                const r = d.getBoundingClientRect();
                const sb = document.querySelector('.sidebar').getBoundingClientRect();
                const x = (Math.max(r.left, sb.left) + Math.min(r.right, sb.right)) / 2;
                const y = (Math.max(r.top, sb.top) + Math.min(r.bottom, sb.bottom, innerHeight)) / 2;
                // A modal dialog turns pointer events off on the page under it, and
                // elementFromPoint skips such an element: hit-test the drawer for the measure.
                const side = document.querySelector('.sidebar');
                side.style.pointerEvents = 'auto';
                const at = document.elementFromPoint(x, y);
                side.style.pointerEvents = '';
                return { overlap: r.left < sb.right && r.right > sb.left, inside: d.contains(at), at: at?.className?.baseVal ?? at?.className };
            });
            assert.equal(hit.overlap, true, `${o.name}: the drawer and the dialog do not overlap, so this measures nothing`);
            assert.equal(hit.inside, true, `${o.name}: the drawer (${hit.at}) is drawn over it`);
            await page.evaluate(() => { if (document.querySelector('.sidebar').classList.contains('open')) document.getElementById('sidebar-scrim').click(); });
        });
    }

    test('no page errors', () => {
        ready();
        assert.deepEqual(errors, []);
    });
});
