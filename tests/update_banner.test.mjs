// CMX-426: "chela was updated — Reload" — the banner shows when the server's asset
// version differs from the page's, and NOT when they match (a stable deploy must never
// nag, or every open page would reload-storm on each poll).
//
// Run: node --test tests/update_banner.test.mjs (tests/test_js_suites.py runs it in pytest).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

let reloadOffered, applyVersion;

before(async () => {
    const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost:5005/' });
    dom.window.CHELA_ASSET_VERSION = 'aaaaaaaaaaaa';
    for (const k of ['window', 'document']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    ({ reloadOffered, applyVersion } = await import('../chela/dashboard/static/js/version.js'));
});

const banner = () => document.getElementById('update-banner');

test('reloadOffered: differs → offer; match or unknown → silent', () => {
    assert.equal(reloadOffered('a1', 'b2'), true);
    assert.equal(reloadOffered('a1', 'a1'), false);
    assert.equal(reloadOffered('', 'b2'), false);
    assert.equal(reloadOffered('a1', ''), false);
    assert.equal(reloadOffered('a1', undefined), false);
});

test('same version on every poll: no banner (the stable-deploy case)', () => {
    for (let i = 0; i < 3; i++) assert.equal(applyVersion('aaaaaaaaaaaa'), false);
    assert.ok(!banner() || banner().hidden, 'a banner appeared although nothing was deployed');
});

test('the page reads its own version from the rendered page', () => {
    // applyVersion's default pageVersion is window.CHELA_ASSET_VERSION via util.js.
    assert.equal(applyVersion('bbbbbbbbbbbb'), true);
    assert.equal(banner().hidden, false);
    assert.match(banner().textContent, /chela was updated/);
    assert.ok(document.getElementById('update-banner-reload'));
    assert.equal(applyVersion('aaaaaaaaaaaa'), false);
    assert.equal(banner().hidden, true);
});

test('dismiss hides it for that version only', () => {
    assert.equal(applyVersion('cccccccccccc'), true);
    document.getElementById('update-banner-close').click();
    assert.equal(banner().hidden, true);
    assert.equal(applyVersion('cccccccccccc'), false, 'a dismissed banner came back on the next poll');
    assert.equal(applyVersion('dddddddddddd'), true, 'a NEWER deploy must offer again');
});
