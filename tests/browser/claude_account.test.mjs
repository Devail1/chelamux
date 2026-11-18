// CMX-395 — the Claude account line under the sidebar head. Layout, so a real Chromium: a
// long account ellipsizes inside the sidebar while the expiry warning beside it stays whole,
// and a dashboard with no answer for the account reads "account: unknown", never a blank.
// Placeholder addresses only (public repo).
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
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

const EXPIRING = {
    '/api/claude-account': {
        state: 'expiring', email: 'a-very-long-mailbox-name-for-testing@example.invalid',
        org_name: 'An Organisation With A Rather Long Name', plan: 'team', auth_method: 'claude.ai',
        expires_at: '2026-11-19T12:00:00+00:00', expires_in_s: 2 * 86400 + 60, warn_days: 3, change: null,
    },
};

function lineLayout(page) {
    return page.evaluate(() => {
        const line = document.getElementById('claude-account');
        const side = document.querySelector('.sidebar').getBoundingClientRect();
        const r = line.getBoundingClientRect();
        const text = line.querySelector('.ca-text');
        const warn = line.querySelector('.ca-warn');
        return {
            tone: line.dataset.tone, words: text.textContent, warn: warn && warn.textContent,
            line: { left: r.left, right: r.right, h: r.height }, side: { left: side.left, right: side.right },
            textEllipsized: text.scrollWidth > text.clientWidth,
            warnClipped: warn ? warn.scrollWidth > warn.clientWidth : null,
            warnRight: warn ? warn.getBoundingClientRect().right : null,
        };
    });
}

describe('CMX-395 desktop 1440×900: an expiring account with a long name', { skip: why }, () => {
    let page, context, errors;
    const ready = setup(async () => {
        ({ page, context, errors } = await openDashboard(browser, { width: 1440, height: 900, api: EXPIRING }));
        await page.waitForFunction(() => document.getElementById('claude-account')?.dataset.tone === 'warn',
            null, { timeout: 15000 });
    });
    after(() => context && context.close());

    test('the account ellipsizes, the warning stays whole, all inside the sidebar', async () => {
        ready();
        const l = await lineLayout(page);
        assert.equal(l.words, 'a-very-long-mailbox-name-for-testing@… · An Organisation With A Rather Long Name');
        assert.equal(l.warn, 'login expires in 2d');
        assert.ok(l.line.h > 0, 'the line has no layout box');
        assert.ok(l.textEllipsized, 'a long account should ellipsize, not push the warning out');
        assert.equal(l.warnClipped, false, 'the warning is clipped');
        assert.ok(l.line.left >= l.side.left - 0.5 && l.line.right <= l.side.right + 0.5,
            `the line ${JSON.stringify(l.line)} spills out of the sidebar ${JSON.stringify(l.side)}`);
        assert.ok(l.warnRight <= l.side.right + 0.5, `the warning ends at ${l.warnRight}, past the sidebar`);
        assert.deepEqual(errors, []);
    });
});

describe('CMX-395 desktop: no account answer reads "account: unknown"', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900, api: { '/api/claude-account': {} } }));
        await page.waitForFunction(() => document.getElementById('claude-account')?.dataset.tone === 'unknown',
            null, { timeout: 15000 });
    });
    after(() => context && context.close());

    test('the words are drawn and visible', async () => {
        ready();
        const l = await lineLayout(page);
        assert.equal(l.words, 'account: unknown');
        assert.ok(l.line.h > 0);
        assert.equal(await page.locator('#claude-account').isVisible(), true);
    });
});
