// CMX-215 (CMX-212 P01) — the page boots from ONE module script, the Vite bundle the manifest
// names (the React islands + the legacy static/js graph), in a real Chromium on the fixture.
// No unbundled legacy module is fetched any more, legacy and the islands both render from it,
// and CMX-426's "chela was updated — Reload" still fires from the bundled version.js.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { AGENTS, ASSET_VERSION, launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const MANIFEST = JSON.parse(readFileSync(new URL('../../chela/dashboard/static/app/manifest.json', import.meta.url), 'utf8'));
const BUNDLE = `/static/v/${ASSET_VERSION}/app/${MANIFEST['src/main.tsx'].file}`;

test('the bundle is the only module the page loads, and both legacy and the islands boot from it', { skip: why }, async () => {
    const { context, page, misses, errors } = await openDashboard(browser, { width: 1440, height: 900 });
    try {
        await page.waitForFunction(n => document.querySelectorAll('.agent-row').length >= n, AGENTS.length, { timeout: 15000 });
        await page.locator('#version-island .version-badge').waitFor({ timeout: 15000 });
        const scripts = await page.evaluate(() => performance.getEntriesByType('resource')
            .map(e => new URL(e.name).pathname).filter(p => p.endsWith('.js')));
        // collab/ is not the legacy graph: terminals.js imports it at runtime by URL (staticUrl),
        // the same files the ttyd presence shim loads in each pane.
        assert.deepEqual(scripts.filter(p => p.includes('/app/')), [BUNDLE]);
        assert.deepEqual(scripts.filter(p => p.includes('/js/')), [], 'an unbundled legacy module was fetched');
        assert.equal(await page.evaluate(() => typeof window.chela?.closeModal), 'function');
        assert.deepEqual(errors, []);
        assert.deepEqual(misses, []);
    } finally {
        await context.close();
    }
});

test('a newer deploy still offers the CMX-426 reload, from the bundled version.js', { skip: why }, async () => {
    const { context, page } = await openDashboard(browser, {
        width: 1440, height: 900, api: { '/api/version': { version: 'newer0000000' } },
    });
    try {
        await page.locator('#update-banner-reload').waitFor({ state: 'visible', timeout: 15000 });
        assert.match(await page.locator('#update-banner-msg').textContent(), /updated/);
    } finally {
        await context.close();
    }
});
