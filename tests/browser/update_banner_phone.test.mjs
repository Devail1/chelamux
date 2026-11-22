// CMX-465 — the update banner at phone width: its button never wraps.
//
// Liav's iPhone (390 CSS px, 2026-11-22): "2 services run old code" squeezed its button
// until "Update & restart" broke over three lines ("Update / & / restart"). The banner was
// `position: fixed; left: 50%`, so its shrink-to-fit width was capped at HALF the viewport
// (~195px at 390). Now a full-width dock is fixed and the banner centres inside it; a
// container query on that dock drops the buttons under the message when the row is too
// narrow for both. jsdom has no layout engine, so this is measured in a real Chromium:
//   - at 360 / 390 / 430 px, as a touch device: the button's height equals ONE line
//     (the same button with a one-letter label), its label is one line box, and it sits
//     inside the banner, which sits inside the viewport;
//   - the dismiss ✕ is a 44×44 touch target;
//   - 360 and 390 stack the button under the message; 430 keeps one row;
//   - at 1440 with a mouse the banner is the one-row, 24px-✕ banner it always was.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

// The payload from the report: two stale services and new commits to pull, so the label is
// the long one ("Update & restart", features/update/flow.ts staleOffer).
const STALE = { ok: true, stale_ok: true, stale_services: ['chela-collab', 'chela-telegram'], behind: 2 };

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// `reload`: the server also runs a newer build than the page, so Reload sits beside Restart.
async function measure({ width, touch, reload = false }) {
    const api = { '/api/update/status': STALE, ...(reload ? { '/api/version': { version: 'n3wer0000000' } } : {}) };
    const { context, page, errors } = await openDashboard(browser, { width, height: 800, touch, api });
    try {
        await page.waitForSelector(reload ? '#update-banner-reload' : '#update-banner-restart', { timeout: 15000 });
        return await page.evaluate(() => {
            const rect = el => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; };
            const banner = document.getElementById('update-banner');
            const btn = document.getElementById('update-banner-restart');
            // One line of this button: the same button, same place, a one-letter label.
            const probe = btn.cloneNode(false);
            probe.removeAttribute('id');
            probe.textContent = 'X';
            btn.after(probe);
            const oneLine = probe.getBoundingClientRect().height;
            probe.remove();
            const range = document.createRange();
            range.selectNodeContents(btn);
            const lineTops = new Set([...range.getClientRects()].map(r => Math.round(r.top)));
            const reloadBtn = document.getElementById('update-banner-reload');
            const css = getComputedStyle(btn);
            return {
                coarse: matchMedia('(pointer: coarse)').matches,
                label: btn.textContent, oneLine, lines: lineTops.size,
                whiteSpace: css.whiteSpace, flexShrink: css.flexShrink,
                reload: reloadBtn && rect(reloadBtn),
                vw: document.documentElement.clientWidth,
                banner: rect(banner), btn: rect(btn),
                msg: rect(document.getElementById('update-banner-msg')),
                close: rect(document.getElementById('update-banner-close')),
            };
        });
    } finally {
        assert.deepEqual(errors, []);
        await context.close();
    }
}

for (const width of [360, 390, 430]) {
    describe(`CMX-465 ${width}px phone: "Update & restart" stays one line inside the banner`, { skip: why }, () => {
        let m;
        const ready = setup(async () => { m = await measure({ width, touch: true }); });

        test('the button is one line tall and its label one line box', () => {
            ready();
            assert.equal(m.label, 'Update & restart');
            assert.deepEqual([m.whiteSpace, m.flexShrink], ['nowrap', '0']);
            assert.equal(m.lines, 1);
            assert.ok(m.oneLine > 0);
            assert.ok(Math.abs(m.btn.height - m.oneLine) < 1, `button ${m.btn.height}px tall, one line is ${m.oneLine}px`);
        });

        test('the button fits inside the banner, and the banner inside the viewport', () => {
            ready();
            const { btn, banner } = m;
            assert.ok(btn.left >= banner.left && btn.right <= banner.right, `button ${btn.left}..${btn.right} vs banner ${banner.left}..${banner.right}`);
            assert.ok(btn.top >= banner.top && btn.bottom <= banner.bottom, `button ${btn.top}..${btn.bottom} vs banner ${banner.top}..${banner.bottom}`);
            assert.ok(banner.left >= 0 && banner.right <= m.vw, `banner ${banner.left}..${banner.right} in a ${m.vw}px viewport`);
        });

        test('the dismiss ✕ is a 44px touch target', () => {
            ready();
            assert.equal(m.coarse, true, 'the fixture must emulate a touch screen, else this measures a mouse layout');
            assert.ok(m.close.width >= 44 && m.close.height >= 44, `✕ is ${m.close.width}×${m.close.height}`);
        });

        test('the message keeps a readable width', () => {
            ready();
            assert.ok(m.msg.width >= 120, `message column is ${m.msg.width}px`);
        });

        if (width < 400) {
            test('too narrow for one row: the button drops under the message, right-aligned', () => {
                ready();
                assert.ok(m.btn.top >= m.msg.bottom, `button top ${m.btn.top} vs message bottom ${m.msg.bottom}`);
                assert.ok(m.btn.top >= m.close.bottom, `button top ${m.btn.top} vs ✕ bottom ${m.close.bottom}`);
                assert.ok(m.banner.right - m.btn.right < 12, `button ends ${m.banner.right - m.btn.right}px from the banner's right edge`);
            });
        } else {
            test('room for one row: message, button and ✕ side by side', () => {
                ready();
                assert.ok(m.btn.top < m.msg.bottom && m.btn.bottom > m.msg.top, 'button beside the message');
                assert.ok(m.close.left >= m.btn.right, '✕ right of the button');
            });
        }
    });
}

for (const width of [360, 390, 430]) {
    describe(`CMX-465 ${width}px phone, Restart AND Reload: both stay one line, under the message`, { skip: why }, () => {
        let m;
        const ready = setup(async () => { m = await measure({ width, touch: true, reload: true }); });

        test('both buttons one line tall, inside the banner, on a row under the message', () => {
            ready();
            assert.equal(m.lines, 1);
            for (const b of [m.btn, m.reload]) {
                assert.ok(Math.abs(b.height - m.oneLine) < 1, `button ${b.height}px tall, one line is ${m.oneLine}px`);
                assert.ok(b.left >= m.banner.left && b.right <= m.banner.right && b.bottom <= m.banner.bottom, 'button inside the banner');
                assert.ok(b.top >= m.msg.bottom && b.top >= m.close.bottom, 'button under the message and the ✕');
            }
            assert.ok(m.banner.left >= 0 && m.banner.right <= m.vw);
            assert.ok(m.msg.width >= 120, `message column is ${m.msg.width}px`);
        });
    });
}

describe('CMX-465 1440px desktop: the banner is unchanged', { skip: why }, () => {
    let m;
    const ready = setup(async () => { m = await measure({ width: 1440, touch: false }); });

    test('one row, centred, a 24px ✕', () => {
        ready();
        assert.equal(m.lines, 1);
        assert.ok(Math.abs(m.btn.height - m.oneLine) < 1);
        assert.ok(m.btn.top < m.msg.bottom && m.btn.bottom > m.msg.top, 'button beside the message');
        assert.equal(m.coarse, false);
        assert.equal(m.close.height, 24);
        assert.ok(m.banner.height < 50, `banner is ${m.banner.height}px tall`);
        assert.ok(Math.abs((m.banner.left + m.banner.right) / 2 - m.vw / 2) <= 1, 'banner centred');
        // the whole message on one line: nothing squeezes it on a desktop
        assert.ok(m.msg.height < 1.6 * 13 * 1.2 + 2, `message is ${m.msg.height}px tall`);
    });
});
