// THE DECISIONS PANEL, IN A REAL DOM — owner-independent, and scoped to decisions.
//
// Two properties this drives the REAL renderer to prove (jsdom, not a source grep):
//
//   1. THE LOG RENDERS REGARDLESS OF WHO OWNS THE ROLE. A decisions panel that only
//      showed rows while a live orchestrator was registered would silently go blank
//      at the exact moment it matters most (CMX-106's whole point: "no owner" must
//      never mean "no visibility").
//   2. THE FETCH IS SCOPED TO DECISION KINDS, not the Feed's whole firehose — every
//      request to /api/log carries `type=` for each kind chela/inbox.py actually
//      queues/logs (run_review, finished, blocked, …), never the tool-call/prompt
//      noise the Feed filters client-side.
//
// Run: node --test tests/decisions.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest; needs `npm ci` for jsdom).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const BODY = `<div class="panel" id="panel-personas">
  <div id="decisions-chip"></div>
  <div class="decisions-list" id="decisions-list"></div>
</div>`;

let decisions, orchestrator;
let requests;
let LOG_RESPONSE;
let STATUS_RESPONSE;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.fetch = (url) => {
        const path = String(url);
        requests.push(path);
        const body = path.includes('/api/orchestrator/status') ? STATUS_RESPONSE : LOG_RESPONSE;
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    orchestrator = await import('../chela/dashboard/static/js/orchestrator.js');
    decisions = await import('../chela/dashboard/static/js/decisions.js');
});

beforeEach(() => {
    requests = [];
    STATUS_RESPONSE = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };
    LOG_RESPONSE = { boot_id: 'b1', events: [], gap: null, first_seq: 0, last_seq: 0, next_seq: 0 };
});

const rows = () => document.querySelectorAll('#decisions-list .feed-row');
const chip = () => document.querySelector('#decisions-chip .decisions-chip');

test('the panel renders decision rows with NO owner registered — never blank on "nobody"', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{ seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-9 awaiting review', payload: {} }],
    };
    await decisions.enterDecisions();

    assert.equal(rows().length, 1, 'a decision must render even with no live orchestrator');
    assert.ok(rows()[0].textContent.includes('cmx-9 awaiting review'));
    // The chip itself says "nobody", but the log below it is not empty because of that.
    assert.ok(chip(), 'the owner chip did not render');
    assert.equal(chip().className, 'decisions-chip decisions-chip-none');
});

test('an owner is reflected in the chip once one is registered', async () => {
    STATUS_RESPONSE = { wid: '@7', name: 'orchestrator', state: 'ok', why: '', queued: 2 };
    await decisions.enterDecisions();

    assert.equal(chip().className, 'decisions-chip decisions-chip-ok');
    assert.ok(chip().textContent.includes('@7'));
    assert.ok(chip().textContent.includes('2 queued'));
});

test('a dangling/gone owner is a visibly BAD chip, not a quiet green one', async () => {
    STATUS_RESPONSE = { wid: '@7', name: 'orchestrator', state: 'gone', why: 'no claude running', queued: 3 };
    await decisions.enterDecisions();

    assert.equal(chip().className, 'decisions-chip decisions-chip-bad');
    assert.ok(chip().textContent.includes('no claude running'));
});

test('🔴 GUARD: the log fetch is scoped to decision kinds, not the whole firehose', async () => {
    await decisions.enterDecisions();
    const logCall = requests.find(r => r.includes('/api/log'));
    assert.ok(logCall, 'decisions.js never called /api/log');
    for (const t of decisions.DECISION_TYPES) {
        assert.ok(logCall.includes('type=' + t), `missing type=${t} on the decisions fetch — a tool-call/prompt firehose would leak in`);
    }
    // And it must NOT be requesting every type unfiltered (no `type` param at all).
    assert.ok(logCall.includes('type='), 'the fetch carries no type filter at all');
});

test('a gap is rendered, never silently swallowed', async () => {
    LOG_RESPONSE = {
        boot_id: 'b2', gap: { reason: 'boot_id changed', boot_id: 'b2' },
        events: [], first_seq: 0, last_seq: 0, next_seq: 0,
    };
    await decisions.enterDecisions();
    const gapEl = document.querySelector('#decisions-list .feed-gap');
    assert.ok(gapEl, 'a reported gap must show on screen');
    assert.ok(gapEl.textContent.includes('boot_id changed'));
});
