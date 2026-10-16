// CMX-129 (CMX-95 slice D): "N services run old code — Restart" shares ONE banner with
// CMX-426's Reload. Unknown hides it; dismiss is per stale set; Restart is confirm-gated and
// POSTs the one existing route, /api/update/apply. Run via tests/test_js_suites.py.
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

let applyStale, applyVersion, checkForUpdate, restartStale, staleOffer;
let versionPayload, statusPayload, applyPayload, fetchCalls;

before(async () => {
    const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost:5005/' });
    dom.window.CHELA_ASSET_VERSION = 'aaaaaaaaaaaa';
    for (const k of ['window', 'document']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.fetch = (url, opts) => {
        const u = String(url); fetchCalls.push({ url: u, opts: opts || null });
        const body = u.endsWith('/api/version') ? versionPayload
            : u.endsWith('/api/update/status') ? statusPayload
                : u.endsWith('/api/update/apply') ? applyPayload : new Error('unexpected ' + u);
        if (body instanceof Error) return Promise.reject(body);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    };
    ({ applyStale, applyVersion, checkForUpdate, restartStale } =
        await import('../chela/dashboard/static/js/version.js'));
    ({ staleOffer } = await import('../chela/dashboard/static/js/updateflow.js'));
});

const STALE = { ok: true, behind: 0, stale_ok: true, boot: 'b1', applying: false,
    stale_services: ['chela-collab', 'chela-telegram'], last_result: null };
const $ = id => document.getElementById(id);
const msg = () => $('update-banner-msg').textContent;
const shown = () => !!$('update-banner') && !$('update-banner').hidden;
const btn = () => $('update-banner-restart');
const posts = () => fetchCalls.filter(c => c.url.endsWith('/api/update/apply'));
const tick = () => new Promise(r => setTimeout(r, 0));
// followUpdate deps that never really wait.
const instant = statuses => {
    let i = 0, clock = 0;
    return { sleep: async ms => { clock += ms; }, now: () => clock, reload: () => {},
        fetchStatus: async () => statuses[Math.min(i++, statuses.length - 1)] };
};

beforeEach(() => {
    applyVersion('aaaaaaaaaaaa'); applyStale(null);   // no reload, nothing stale
    versionPayload = { version: 'aaaaaaaaaaaa' };
    statusPayload = null; applyPayload = {}; fetchCalls = [];
});

test('stale → one banner naming the count, a Restart button, no Reload', () => {
    assert.equal(applyStale(STALE), true);
    assert.ok(shown());
    assert.equal(msg(), '2 services run old code');
    assert.deepEqual([btn().hidden, btn().textContent, $('update-banner-reload').hidden], [false, 'Restart', true]);
    applyStale({ ...STALE, stale_services: ['chela-collab'] });
    assert.equal(msg(), '1 service runs old code');
    applyStale({ ...STALE, behind: 1 });
    assert.equal(btn().textContent, 'Update & restart');
});

for (const [label, st] of [
    ['nothing stale', { ...STALE, stale_services: [] }],
    ['no payload', null], ['ok:false', { ...STALE, ok: false }], ['ok missing', { ...STALE, ok: undefined }],
    ['stale_ok:false', { ...STALE, stale_ok: false }], ['stale_ok missing', { ...STALE, stale_ok: undefined }],
    ['stale list missing', { ...STALE, stale_services: undefined }],
]) {
    test(`${label} → hidden, never a restart offer`, () => {
        applyStale(STALE);
        assert.equal(applyStale(st), false);
        assert.ok(!shown());
        assert.equal(staleOffer(st), null);
    });
}

test('a deploy AND stale services: ONE banner, both facts, each button', () => {
    applyStale(STALE);
    assert.equal(applyVersion('bbbbbbbbbbbb'), true);
    assert.equal(document.querySelectorAll('.update-banner').length, 1, 'two banners stacked');
    assert.equal(msg(), 'chela was updated · 2 services run old code');
    assert.deepEqual([btn().hidden, $('update-banner-reload').hidden], [false, false]);
    applyStale({ ...STALE, stale_services: [] });
    assert.deepEqual([msg(), btn().hidden, shown()], ['chela was updated', true, true]);
});

test('dismiss hides THIS stale set only; a new set shows again', () => {
    const mine = { ...STALE, stale_services: ['chela-dismissed'] };
    applyStale(mine);
    $('update-banner-close').click();
    assert.equal(shown() || applyStale(mine), false, 'a dismissed set came back on the next poll');
    assert.ok(applyStale({ ...STALE, stale_services: ['chela-daemon'] }) && shown(), 'a NEW set stayed hidden');
});

test('dismissing a reload alone does not swallow a later stale set', () => {
    applyVersion('dddddddddddd');
    $('update-banner-close').click();
    assert.equal(applyStale({ ...STALE, stale_services: ['chela-late'] }), true);
    assert.equal(msg(), '1 service runs old code');
});

test('checkForUpdate reads /api/update/status; the two reads are independent', async () => {
    statusPayload = STALE;
    versionPayload = new Error('version route down');
    assert.equal(await checkForUpdate(), false);
    await tick();
    assert.ok(shown(), 'a failed /api/version read hid the stale offer');
    // A failed status read hides the stale half (unknown) but keeps a pending reload.
    versionPayload = { version: 'cccccccccccc' };
    statusPayload = new Error('status route down');
    assert.equal(await checkForUpdate(), true);
    await tick();
    assert.deepEqual([btn().hidden, $('update-banner-reload').hidden], [true, false]);
});

test('Restart: confirm names the services, POSTs the existing route, ends in the outcome', async () => {
    applyStale(STALE);
    let asked = '';
    globalThis.confirm = q => { asked = q; return true; };
    applyPayload = { ok: true, started: true, run: 2, boot: 'b1', detail: 'restarting chela-collab' };
    const res = await restartStale(document, instant([{ ...STALE, applying: true },
        { ...STALE, last_result: { run: 2, ok: true, behind_before: 0, restarted: ['chela-telegram', 'chela-collab'] } }]));
    assert.match(asked, /^Restart: chela-collab, chela-telegram\?/);
    assert.deepEqual(posts().map(c => c.opts.method), ['POST']);
    assert.deepEqual([res.text, msg()], Array(2).fill('Restarted chela-collab, chela-telegram'));
    assert.match($('update-banner-msg').className, /\bok\b/);
    assert.equal(btn().hidden, true, 'a finished restart still offers Restart');
    $('update-banner-close').click();   // dismissing the outcome clears it for the next set
    applyStale({ ...STALE, stale_services: ['chela-next'] });
    assert.equal(msg(), '1 service runs old code', 'a dismissed outcome stuck to the banner');
});

test('the banner button runs it; behind > 0 confirms as "Update & restart"', async () => {
    applyStale({ ...STALE, behind: 3 });
    let asked = '';
    globalThis.confirm = q => { asked = q; return false; };
    btn().click();
    await tick();
    assert.match(asked, /^Update & restart: chela-collab, chela-telegram\?/);
    assert.equal(posts().length, 0, 'a declined confirm POSTed');
});

test('nothing stale, or one already in flight: Restart neither asks nor POSTs', async () => {
    let asked = 0; globalThis.confirm = () => { asked++; return true; };
    assert.equal(await restartStale(document, instant([])), null);
    assert.equal(asked, 0);
    applyStale(STALE);
    applyPayload = { ok: true, started: true, run: 4, boot: 'b1', detail: 'restarting chela-collab' };
    let release; const gate = new Promise(r => { release = r; });
    const deps = { ...instant([]), fetchStatus: async () => { await gate;
        return { ...STALE, last_result: { run: 4, ok: true, restarted: ['chela-collab'] } }; } };
    const first = restartStale(document, deps); await tick();
    const second = restartStale(document, deps); await tick();
    release(); await first;
    assert.equal(await second, null, 'an in-flight restart was started twice');
    assert.equal(posts().length, 1);
});

test('refused, not started, and never confirmed each end in their own outcome', async () => {
    globalThis.confirm = () => true;
    applyStale(STALE);
    applyPayload = { ok: false, error: 'an update is already running (4s so far)' };
    assert.equal((await restartStale(document, instant([]))).text, 'an update is already running (4s so far)');
    assert.match($('update-banner-msg').className, /\berr\b/);
    assert.equal(btn().hidden, false, 'a refusal left nothing to retry with');
    applyPayload = { ok: true, started: false, detail: 'already up to date' };
    assert.equal((await restartStale(document, instant([]))).text, 'already up to date');
    applyStale(STALE);
    applyPayload = { ok: true, started: true, run: 3, boot: 'b1', detail: 'restarting chela-collab' };
    const res = await restartStale(document, instant([{ ...STALE, applying: true }]));
    assert.equal(res.text, "Couldn't confirm the restart — check `pm2 ls`.");
    assert.match($('update-banner-msg').className, /\berr\b/);
});
