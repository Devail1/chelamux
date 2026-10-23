// CMX-249 (FRONTEND_PORT_PLAN.md §6.3): ⌘K and Ctrl+, pressed INSIDE a pane still reach the
// page. A focused pane's keys never bubble to the dashboard, so app.py's
// _TERM_PALETTE_KEY_SHIM, injected into every ttyd page, calls window.parent.chela.openPalette()
// and .toggleSettings(), the two names src/iframeBridge.ts registers. This loads that exact
// shim (read out of app.py, not a copy) into a pane's frame on the built page and presses the
// keys there.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

/** The shim's markup: the string literals of `_TERM_PALETTE_KEY_SHIM = ( … )` in app.py, joined. */
function paletteKeyShim(appPy) {
    const start = appPy.indexOf('\n_TERM_PALETTE_KEY_SHIM = (');
    if (start < 0) throw new Error('_TERM_PALETTE_KEY_SHIM is not in app.py');
    const block = appPy.slice(start, appPy.indexOf('\n)', start));
    const parts = block.split('\n').slice(2).filter(l => !/^\s*#/.test(l)).map(l => JSON.parse(l.trim()));
    return parts.join('');
}
const SHIM = paletteKeyShim(readFileSync(new URL('../../chela/dashboard/app.py', import.meta.url), 'utf8'));

const shown = (page, id) => page.waitForFunction(i => {
    const el = document.getElementById(i);
    return !!el && getComputedStyle(el).visibility !== 'hidden' && el.getBoundingClientRect().width > 0;
}, id, { timeout: 5000 });

describe('§6.3: the pane keys reach the page from inside a pane', { skip: why }, () => {
    test('the shim read out of app.py is the keydown shim calling the two contract names', () => {
        assert.match(SHIM, /^<script>/);
        assert.match(SHIM, /openPalette/);
        assert.match(SHIM, /toggleSettings/);
        assert.match(SHIM, /window\.parent\.chela/);
    });

    test('Ctrl+K in a pane opens the palette; Ctrl+, opens Settings', async () => {
        const { context, page, errors } = await openDashboard(browser, { width: 1440, height: 900 });
        try {
            // The pane's page, as term_http serves it: ttyd's document with the shim in its head.
            await context.route('**/term/**', route => route.fulfill({
                status: 200, contentType: 'text/html',
                body: `<!doctype html><html><head>${SHIM}</head><body><textarea id="t"></textarea></body></html>`,
            }));
            await page.waitForFunction(() => document.querySelector('iframe[src*="/term/"]'), null, { timeout: 15000 });
            const frame = page.frames().find(f => f.url().includes('/term/'));
            assert.ok(frame, 'no pane frame on the wall');
            await frame.goto(frame.url());
            await frame.locator('#t').focus();
            assert.equal(await page.locator('#palette').count(), 0, 'the palette was open before the key');

            await frame.locator('#t').press('Control+k');
            await shown(page, 'palette');
            await page.keyboard.press('Escape');
            await page.waitForFunction(() => !document.getElementById('palette'), null, { timeout: 5000 });

            await frame.locator('#t').focus();
            await frame.locator('#t').press('Control+,');
            await shown(page, 'settings-drawer');
            assert.deepEqual(errors, []);
        } finally {
            await context.close();
        }
    });
});
