// CMX-426: "chela was updated — Reload" — the banner shows when the server's asset
// version differs from the page's, and NOT when they match (a stable deploy must never
// nag, or every open page would reload-storm on each poll).
//
// Run: node --test tests/update_banner.test.mjs (tests/test_js_suites.py runs it in pytest).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM, VirtualConsole } from 'jsdom';

let reloadOffered, applyVersion, checkForUpdate, staticUrl;
// jsdom cannot navigate: location.reload() reports "Not implemented: navigation" on the
// virtual console. That report IS the evidence the Reload button reloaded.
const navigations = [];
let serverVersion = '';      // what the fake /api/version answers
let versionFetches = 0;

before(async () => {
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', e => { if (/navigation/i.test(e.message)) navigations.push(e.message); });
    const dom = new JSDOM('<!doctype html><html><body></body></html>',
        { url: 'http://localhost:5005/', virtualConsole });
    dom.window.CHELA_ASSET_VERSION = 'aaaaaaaaaaaa';
    for (const k of ['window', 'document']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.fetch = url => {
        if (!String(url).endsWith('/api/version')) return Promise.reject(new Error('unexpected ' + url));
        versionFetches++;
        if (serverVersion === null) return Promise.reject(new Error('dashboard down'));
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ version: serverVersion }) });
    };
    ({ reloadOffered, applyVersion, checkForUpdate } = await import('../chela/dashboard/static/js/version.js'));
    ({ staticUrl } = await import('../chela/dashboard/static/js/util.js'));
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

// The judge's note: staticUrl() was guarded by reading its SOURCE. Call it and check the
// URL it actually returns — that is what the dynamic import() of presence-owner.js loads.
test('staticUrl() returns the versioned URL of the page\'s own deploy', () => {
    assert.equal(staticUrl('collab/presence-owner.js'), '/static/v/aaaaaaaaaaaa/collab/presence-owner.js');
    assert.equal(staticUrl('x.js'), '/static/v/aaaaaaaaaaaa/x.js');
});

test('staticUrl() with no rendered version (an old template) stays a working unversioned URL', async () => {
    const saved = window.CHELA_ASSET_VERSION;
    delete window.CHELA_ASSET_VERSION;
    try {
        // A fresh module instance (query string) so util.js re-reads the page's version.
        const fresh = await import('../chela/dashboard/static/js/util.js?no-version');
        assert.equal(fresh.ASSET_VERSION, '');
        assert.equal(fresh.staticUrl('x.js'), '/static/x.js');
    } finally {
        window.CHELA_ASSET_VERSION = saved;
    }
});

// checkForUpdate() is what refresh() actually calls: it must ASK the server and compare
// the answer with the page's own version — both directions, through the real fetch path.
test('checkForUpdate: the server reports the page\'s own version → no banner', async () => {
    const before = versionFetches;
    serverVersion = 'aaaaaaaaaaaa';
    for (let i = 0; i < 3; i++) assert.equal(await checkForUpdate(), false);
    assert.equal(versionFetches - before, 3, 'checkForUpdate did not ask /api/version');
    assert.ok(!banner() || banner().hidden, 'a banner appeared although the versions match');
});

test('checkForUpdate: the server reports a different version → banner; back to the same → hidden', async () => {
    serverVersion = 'eeeeeeeeeeee';
    assert.equal(await checkForUpdate(), true);
    assert.equal(banner().hidden, false);
    assert.equal(banner().dataset.version, 'eeeeeeeeeeee');
    serverVersion = 'aaaaaaaaaaaa';
    assert.equal(await checkForUpdate(), false);
    assert.equal(banner().hidden, true);
});

test('checkForUpdate: an unreachable server or an empty answer never offers a reload', async () => {
    serverVersion = null;
    assert.equal(await checkForUpdate(), false);
    serverVersion = '';
    assert.equal(await checkForUpdate(), false);
    assert.ok(!banner() || banner().hidden);
});

test('Reload reloads the page; nothing reloads on its own', async () => {
    serverVersion = 'ffffffffffff';
    assert.equal(await checkForUpdate(), true);
    assert.equal(navigations.length, 0, 'the page navigated without the user clicking Reload');
    document.getElementById('update-banner-reload').click();
    assert.equal(navigations.length, 1, 'clicking Reload did not reload the page');
});
