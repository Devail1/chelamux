// SETTINGS DRAWER "Update" CONTROL (CMX-199), in a REAL DOM.
//
// The judge corrupted `nav.js`'s `if (behind > 0)` guard to `if (false && behind > 0)`
// and the pytest suite stayed green — nothing in `tests/` exercises `renderSettings`,
// `_loadSettingsStatus`, or `#update-apply-btn`, so the entire operator-facing half of
// CMX-199 (the button that actually appears/enables when the checkout falls behind)
// was unguarded ground. This runs the REAL nav.js (via main.js, same module-graph-import
// approach as tests/topbarmenu.test.mjs) against a mocked /api/settings response and
// asserts the DOM `_renderUpdateStatus` actually produces — not a source grep.
//
// Two properties:
//
//   1. 🔴 BEHIND > 0 ENABLES THE BUTTON AND ANNOUNCES THE COUNT. Drop the `behind > 0`
//      branch (or falsify its condition) and this goes red — the button stays disabled
//      and the badge never says "N behind" even though the checkout is behind.
//   2. 🔴 COUNTERWEIGHT — up to date leaves the button disabled. Without this, "always
//      enable the button" would also satisfy property 1.
//
// Run: pnpm exec vitest run tests/settings_update.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom — CHELA_REQUIRE_JS_TESTS makes a
// missing jsdom a FAILURE.)
import { beforeAll, beforeEach, test } from 'vitest';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it

const BODY = `
<div class="drawer-scrim" id="drawer-scrim" onclick="chela.toggleSettings()"></div>
<aside class="drawer" id="settings-drawer">
  <div class="drawer-body" id="drawer-body"></div>
</aside>`;

let updatePayload;
let applyPayload = {};
let fetchCalls = [];
let settingsShouldFail = false;

function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

beforeAll(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.fetch = (url, opts) => {
        fetchCalls.push({ url: String(url), opts: opts || null });
        if (String(url).endsWith('/api/settings') && settingsShouldFail) {
            return Promise.reject(new Error('network down'));
        }
        const body = String(url).endsWith('/api/settings')
            ? { sections: [], update: updatePayload }
            : applyPayload;
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(() => {
    // Each test opens the drawer fresh — toggleSettings() only calls renderSettings()
    // when it transitions closed→open, so start from a known-closed state.
    document.getElementById('settings-drawer').classList.remove('open');
    applyPayload = {};
    fetchCalls = [];
    settingsShouldFail = false;
    useStatuses([]);
});

// CMX-95: a FAKE /api/update/status, one scripted entry per poll (an Error = unreachable),
// on a fake clock that advances per sleep, so the 180 s timeout costs no real time.
let statusPolls = 0, clock = 0, reloads = 0;
function useStatuses(list) {
    statusPolls = clock = reloads = 0;
    Object.assign(window.chela._updateFlowDeps, {
        sleep: async ms => { clock += ms; }, now: () => clock, reload: () => { reloads++; },
        fetchStatus: async () => {
            const v = list.length ? list[Math.min(statusPolls++, list.length - 1)] : null;
            if (v instanceof Error) throw v;
            return v;
        },
    });
}

async function openWith(update) {
    updatePayload = update;
    window.chela.toggleSettings();
    await flush();
}

// --- 1. 🔴 behind > 0 enables the button and announces the count ------------------

test('a checkout that is behind enables the Update button and shows the count', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.equal(btn.disabled, false, 'the Update button stayed disabled while behind > 0');
    assert.match(row.textContent, /3 behind/, 'the status row does not announce the behind count');
    assert.match(row.textContent, /dev/, 'the status row does not name the branch');
});

// --- 2. 🔴 counterweight — up to date leaves the button disabled ------------------

test('a checkout that is up to date offers no Update button at all', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: [], stale_ok: true });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.equal(btn.disabled, true, 'the Update button is enabled with nothing to pull');
    // CMX-95 (Liav's live test 10-12): "Update now" was offered on UP TO DATE.
    assert.equal(btn.hidden, true, '"Update now" is offered with nothing to pull and nothing stale');
    assert.match(row.textContent, /Up to date/, 'the status row does not say "Up to date"');
});

test('the button comes back once there is something to do (after an up-to-date render)', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: [], stale_ok: true });
    document.getElementById('settings-drawer').classList.remove('open');
    await openWith({ ok: true, behind: 2, ahead: 0, branch: 'dev', stale_services: [], stale_ok: true });
    assert.equal(document.getElementById('update-apply-btn').hidden, false);
});

test('an unreadable service-freshness check is Unknown, never "Up to date", and offers nothing', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: [], stale_ok: false });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.doesNotMatch(row.textContent, /Up to date/, 'an unread fleet rendered as "Up to date"');
    assert.match(row.textContent, /Unknown.*could not read which services run old code/s);
    assert.equal(btn.hidden, true);
});

test('an unreadable checkout (git=false) reports Unknown and keeps the button disabled', async () => {
    await openWith({ ok: false, git: false, error: 'not a git checkout' });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.equal(btn.disabled, true);
    assert.equal(btn.hidden, true);
    assert.match(row.textContent, /Unknown/);
});

// --- CMX-212: nothing to pull, but running services predate the checked-out code --
//
// Measured live 2026-08-02: the card said "UP TO DATE" while `chela doctor` reported
// running `chela-*` services predating the checkout (a bare `git pull` never restarts
// anything). `behind: 0` alone must not render "Up to date" once `stale_services` is
// non-empty.

test('nothing to pull but stale services never renders as "Up to date"', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev',
        stale_services: ['chela-daemon', 'chela-dashboard'] });

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.doesNotMatch(row.textContent, /Up to date/,
        'a checkout with stale running services still rendered as "Up to date"');
    assert.match(row.textContent, /chela-daemon/, 'the stale service names never reached the row');
    assert.match(row.textContent, /chela-dashboard/);
    // CMX-56: with nothing to pull, the click restarts exactly these — so the button is
    // live, and says so instead of promising an update there is nothing to pull for.
    assert.equal(btn.disabled, false, 'stale services listed over a dead button');
    assert.equal(btn.textContent, 'Restart stale services (2)');
});

// CMX-160 rewrote this sentence; both number agreements are pinned word for word.
const staleDetail = () => document.querySelector('#update-status-row .s-status-detail').textContent.replace(/\s+/g, ' ');

test('the stale sentence for two services says "run" and "they"', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: ['chela-daemon', 'chela-dashboard'] });
    assert.equal(staleDetail(), 'branch dev: nothing to pull, but code that chela-daemon, chela-dashboard run changed '
        + 'after they started (or pm2 restart chela-daemon chela-dashboard)');
});

test('the stale sentence for one service says "runs" and "it"', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: ['chela-daemon'] });
    assert.equal(staleDetail(), 'branch dev: nothing to pull, but code that chela-daemon runs changed '
        + 'after it started (or pm2 restart chela-daemon)');
});

test('a service whose freshness is unknown is named as unknown, not stale and not hidden', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: [],
        unknown_services: ['chela-agent-terminals'] });

    const row = document.getElementById('update-status-row');
    assert.match(row.textContent, /unknown for chela-agent-terminals/);
    assert.equal(document.getElementById('update-apply-btn').disabled, true);
});

const BOOT = 'boot-1';
const status = extra => ({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_ok: true,
    stale_services: [], boot: BOOT, applying: false, last_result: null, ...extra });

test('clicking Restart stale services says which services it is restarting, then the outcome', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev',
        stale_services: ['chela-collab', 'chela-daemon', 'chela-telegram'] });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: true, behind: 0, run: 4, boot: BOOT,
        restarting: ['chela-collab', 'chela-daemon', 'chela-telegram'],
        detail: 'restarting chela-collab, chela-daemon, chela-telegram' };
    const seen = [];
    const msg = document.getElementById('update-apply-msg');
    useStatuses([status({ applying: true }), status({ applying: true }),
        status({ last_result: { run: 4, ok: true, step: 'done', error: '', behind_before: 0,
            restarted: ['chela-telegram', 'chela-collab', 'chela-daemon'] } })]);
    const fetchStatus = window.chela._updateFlowDeps.fetchStatus;
    window.chela._updateFlowDeps.fetchStatus = async () => { seen.push(msg.className + '|' + msg.textContent); return fetchStatus(); };

    await window.chela.applyUpdate();
    await flush();

    assert.match(seen[0], /Restarting chela-collab, chela-daemon, chela-telegram/,
        'a restart click gave no feedback naming what it restarted');
    // While polling it is in progress — neither the success nor the error style.
    assert.equal(seen[1], 's-savemsg |Restarting chela-collab, chela-daemon, chela-telegram…');
    // 🔴 The hang (Liav 10-12): it used to sit at "refresh in a few seconds" forever.
    assert.equal(msg.textContent, 'Restarted chela-collab, chela-daemon, chela-telegram');
    assert.match(msg.className, /\bok\b/);
    assert.equal(statusPolls, 3, 'it stopped polling before the outcome, or kept polling after it');
});

test('a failed restart ends in the error, styled as one, with the button back', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: ['chela-daemon'] });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: true, behind: 0, run: 5, boot: BOOT, detail: 'restarting chela-daemon' };
    useStatuses([status({ applying: true }), status({ last_result: { run: 5, ok: false,
        step: 'pm2-restart', restarted: [], error: 'chela-daemon is still mid-tick' } })]);

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.equal(msg.textContent, 'Update failed at pm2-restart: chela-daemon is still mid-tick');
    assert.match(msg.className, /\berr\b/);
    const btn = document.getElementById('update-apply-btn');
    assert.equal(btn.disabled, false, 'a failed restart must leave the button to retry');
    assert.equal(btn.textContent, 'Restart stale services (1)');
});

test('no answer within the bound ends in "couldn\'t confirm", never success and never forever', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: ['chela-collab'] });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: true, behind: 0, run: 8, boot: BOOT, detail: 'restarting chela-collab' };
    // Unreachable, then forever "applying": nothing ever confirms.
    useStatuses([new Error('down'), status({ applying: true })]);

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.equal(msg.textContent, "Couldn't confirm the restart. Check `pm2 ls`.");
    assert.match(msg.className, /\berr\b/);
    assert.doesNotMatch(msg.className, /\bok\b/);
    // 180s bound at 2s per poll — bounded, and not given up early.
    assert.equal(statusPolls, 90);
});

test('a restart of THIS dashboard (new boot) reloads the page onto the new code', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: ['chela-dashboard'] });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: true, behind: 0, run: 9, boot: BOOT, detail: 'restarting chela-dashboard' };
    useStatuses([new Error('down'), status({ boot: 'boot-2' })]);

    await window.chela.applyUpdate();
    await flush();

    assert.equal(reloads, 1, 'the dashboard restarted but the page never reloaded');
    const msg = document.getElementById('update-apply-msg');
    assert.equal(msg.className + '|' + msg.textContent, 's-savemsg |The dashboard restarted, reloading…');
    assert.equal(statusPolls, 2);
});

// --- Counterweight — no stale services still renders "Up to date" -------------------
//
// Without this, always treating `stale_services` as non-empty (or ignoring it and always
// warning) would also satisfy the test above.

test('nothing to pull and no stale services still renders "Up to date"', async () => {
    await openWith({ ok: true, behind: 0, ahead: 0, branch: 'dev', stale_services: [] });

    const row = document.getElementById('update-status-row');
    assert.match(row.textContent, /Up to date/);
});


// --- 3. 🔴 THE BUTTON IS WIRED TO applyUpdate() ------------------------------------
//
// The judge corrupted `onclick="chela.applyUpdate()"` to `onclick="void 0"` and the whole
// suite stayed green: every assertion above reads `btn.disabled` and `row.textContent` —
// how the control LOOKS, never that it DOES anything. An unwired button renders perfectly.
// Same shape as tests/shortcuts.test.mjs:90, which pins its overlay's onclick attribute.

test('the Update button is wired to applyUpdate() — not just rendered', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });

    const btn = document.getElementById('update-apply-btn');
    assert.match(btn.getAttribute('onclick') || '', /chela\.applyUpdate\(\)/,
        'the Update button renders but is not wired to chela.applyUpdate()');
});

// --- 4. 🔴 A REFUSAL REACHES THE OPERATOR, NAMING WHY ------------------------------
//
// The backend guard asserts the 409 body NAMES the in-flight dispatched task ids
// (tests/test_update_apply_route.py::test_apply_refuses_while_a_dispatched_run_is_in_flight).
// That is worth nothing if the drawer swallows it: the judge corrupted
// `setMsg('err', (resp && resp.error) || 'Update refused.')` to `setMsg('err', '')` and the
// suite stayed green. This drives the real applyUpdate() and asserts the reason lands in
// the DOM — and, in passing, that the POST goes where it claims to.

test('a refused update surfaces the reason, naming the in-flight task', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => true;
    applyPayload = { ok: false, error: 'an update is already running (cmx-199-abc12)' };

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.match(msg.textContent, /cmx-199-abc12/,
        'the refusal reason never reached the operator — the drawer swallowed it');
    const post = fetchCalls.find(c => c.url.endsWith('/api/update/apply'));
    assert.ok(post, 'applyUpdate() never POSTed to /api/update/apply');
    assert.equal(post.opts && post.opts.method, 'POST');
    const btn = document.getElementById('update-apply-btn');
    assert.equal(btn.disabled, false, 'a refused update must re-enable the button to retry');
});

// --- 5. 🔴 COUNTERWEIGHT — a STARTED update must not render as a refusal ------------
//
// Without this, `setMsg('err', ...)` on every path would satisfy the test above.

test('a started update reports success, not a refusal', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: true, run: 3, boot: BOOT };
    useStatuses([status({ last_result: { run: 3, ok: true, step: 'done', behind_before: 3,
        restarted: ['chela-daemon'] } })]);

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.equal(msg.textContent, 'Pulled 3 commit(s). Restarted chela-daemon',
        'a started update did not report its outcome');
    assert.doesNotMatch(msg.textContent, /refused/i);
    // ⛔ The TEXT alone is not the property: setMsg carries the outcome in the class, so
    // `setMsg('ok', …)` -> `setMsg('err', …)` leaves this message word-for-word identical
    // while styling a successful update as a failure. Assert the class, or that mutation
    // survives (it did, the first time I wrote this test).
    assert.match(msg.className, /\bok\b/, 'a started update is styled as an error');
    assert.doesNotMatch(msg.className, /\berr\b/);
});

test('done re-reads the status; an unreadable one hides the shown button again', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: true, run: 3, boot: BOOT };
    useStatuses([status({ last_result: { run: 3, ok: true, step: 'done', restarted: [] } })]);
    settingsShouldFail = true;   // the POST is not /api/settings; only the re-read fails
    await window.chela.applyUpdate();
    await flush();
    assert.match(document.getElementById('update-status-row').textContent, /Unknown/);
    assert.equal(document.getElementById('update-apply-btn').hidden, true);
});

// --- 6. 🔴 A NOT-STARTED response must never be announced as a fleet-wide restart ---
//
// Judge round 5 (PR #260) corrupted `if (!resp.started) {` to `if (false && !resp.started) {`
// and the suite stayed green: nothing drives applyUpdate() with `started: false` (the
// backend's "already up to date" arm — see test_apply_refuses_when_already_up_to_date).
// Falling through to the started-branch text would tell the operator a restart is underway
// when nothing was ever kicked off.

test('a not-started response reports its detail, not a started-restart message', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => true;
    applyPayload = { ok: true, started: false, detail: 'already up to date' };

    await window.chela.applyUpdate();
    await flush();

    const msg = document.getElementById('update-apply-msg');
    assert.match(msg.textContent, /already up to date/i,
        'a not-started response never surfaced its detail');
    assert.doesNotMatch(msg.textContent, /Started/,
        'a not-started response was announced as a fleet-wide restart');
});

// --- 7. 🔴 DECLINING THE CONFIRM DIALOG MUST ABORT BEFORE THE POST -----------------
//
// Judge round 6 (PR #260) corrupted the `if (!confirm(...)) return;` gate to
// `if (false && !confirm(...)) return;` and the suite stayed green: both existing
// applyUpdate() tests stub `globalThis.confirm = () => true`, so the gate is assumed and
// never exercised in its refusing direction. A fleet-wide restart must not fire from a
// single accidental click.

test('declining the confirm dialog aborts before any POST', async () => {
    await openWith({ ok: true, behind: 3, ahead: 0, branch: 'dev' });
    globalThis.confirm = () => false;

    await window.chela.applyUpdate();
    await flush();

    const post = fetchCalls.find(c => c.url.endsWith('/api/update/apply'));
    assert.equal(post, undefined, 'declining confirm() still POSTed to /api/update/apply');
});

// --- 8. 🔴 A FAILED /api/settings POLL MUST RE-RENDER THE ROW AS UNKNOWN -----------
//
// Judge round 5 (PR #260) corrupted the catch arm's `_renderUpdateStatus(null);` to
// `void 0;` and the suite stayed green: nothing ever made the mocked `/api/settings` fetch
// reject, so the row was left sitting on its initial "Checking…" badge forever instead of
// being told the poll failed.

test('a failed /api/settings poll re-renders the Update row as Unknown', async () => {
    settingsShouldFail = true;
    updatePayload = { ok: true, behind: 3, ahead: 0, branch: 'dev' };
    window.chela.toggleSettings();
    await flush();

    const btn = document.getElementById('update-apply-btn');
    const row = document.getElementById('update-status-row');
    assert.match(row.textContent, /Unknown/,
        'a failed /api/settings poll left the row on its stale/initial state');
    assert.equal(btn.disabled, true);
});

// --- CMX-95: the state machine on its exact boundaries ------------------------------

// Imported after the first beforeAll has installed `window` (util.js reads it at import).
let flow;
beforeAll(async () => { flow = await import('../chela/dashboard/static/js/updateflow.js'); });
const CTX = { run: 4, boot: BOOT, label: 'Restarting chela-daemon', startedAt: 1000 };
const done = { run: 4, ok: true, step: 'done', error: '', restarted: ['chela-daemon'], behind_before: 0 };

test('timeout: 179.9 s still polls, 180 s gives up — and never as success', () => {
    const st = status({ applying: true });
    assert.deepEqual(flow.updateOutcome(st, CTX, 1000 + 179900),
        { state: 'polling', text: 'Restarting chela-daemon…' });
    assert.deepEqual(flow.updateOutcome(st, CTX, 1000 + 180000),
        { state: 'timeout', text: "Couldn't confirm the restart. Check `pm2 ls`." });
    assert.equal(flow.updateOutcome(null, CTX, 1000 + 180000).state, 'timeout');
});

test('done needs applying === false AND this run\'s result', () => {
    assert.deepEqual(flow.updateOutcome(status({ last_result: done }), CTX, 1000),
        { state: 'done', text: 'Restarted chela-daemon' });
    // Still applying, or `applying` unread: not done yet (only an explicit false finishes).
    for (const applying of [true, undefined]) assert.equal(flow.updateOutcome(status({ applying, last_result: done }), CTX, 1000).state, 'polling');
    assert.equal(flow.updateOutcome(status({ last_result: { ...done, run: 3 } }), CTX, 1000).state, 'polling');
    // A result decided at the bound still wins over the timeout.
    assert.equal(flow.updateOutcome(status({ last_result: done }), CTX, 1000 + 180000).state, 'done');
    assert.equal(flow.updateOutcome(status({ last_result: { ...done, restarted: [] } }), CTX, 1000).text,
        'Done. No running service needed a restart');
});

test('an unknown result (ok missing / not true) is failed, never success', () => {
    for (const ok of [undefined, null, 'true', 1]) {
        const step = flow.updateOutcome(status({ last_result: { run: 4, ok, step: 'pm2-restart' } }), CTX, 1000);
        assert.deepEqual(step, { state: 'failed', text: 'Update failed at pm2-restart: no detail' }, String(ok));
    }
    assert.equal(flow.updateOutcome(status({ last_result: { run: 4, ok: false, step: 'git-pull', error: 'diverged' } }),
        CTX, 1000).text, 'Update failed at git-pull: diverged');
});

test('reload only on a CHANGED boot — a missing boot on either side is not a restart', () => {
    assert.deepEqual(flow.updateOutcome(status({ boot: 'boot-2', applying: true }), CTX, 1000),
        { state: 'reload', text: 'The dashboard restarted, reloading…' });
    assert.equal(flow.updateOutcome(status({ boot: BOOT, applying: true }), CTX, 1000).state, 'polling');
    assert.equal(flow.updateOutcome(status({ boot: undefined, applying: true }), CTX, 1000).state, 'polling');
    assert.equal(flow.updateOutcome(status({ boot: 'boot-2', applying: true }), { ...CTX, boot: undefined }, 1000).state, 'polling');
});

test('followUpdate polls every 2 s from the click, and fetches /api/update/status by default', async () => {
    let t = 0;
    const sleeps = [];
    const steps = [];
    const seq = [status({ applying: true }), status({ last_result: done })];
    const step = await flow.followUpdate({ run: 4, boot: BOOT, detail: 'restarting chela-daemon' },
        s => steps.push(s.state), {
            fetchStatus: async () => seq.shift(), now: () => t,
            sleep: async ms => { sleeps.push(ms); t += ms; }, reload: () => assert.fail('reloaded'),
        });
    assert.deepEqual(sleeps, [2000, 2000]);
    assert.deepEqual(steps, ['polling', 'done']);
    assert.equal(step.state, 'done');

    const before = fetchCalls.length;
    applyPayload = status({ last_result: done });
    const def = await flow.followUpdate({ run: 4, boot: BOOT }, () => {}, { sleep: async () => {} });
    assert.equal(def.state, 'done');
    assert.ok(fetchCalls.slice(before).some(c => c.url.endsWith('/api/update/status')));
});
