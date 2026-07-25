// THE DECISIONS SIDEBAR SECTION, IN A REAL DOM — owner-independent, and scoped to
// decisions. (cmx-106 first shipped this inside the Personas panel; cmx-107 moved
// it into its own always-visible sidebar section — the ids this test drives,
// #decisions-chip/#decisions-list, are unchanged by that move, so this suite
// exercises the same renderer regardless of what wraps it.)
//
// Two properties this drives the REAL renderer to prove (jsdom, not a source grep):
//
//   1. THE LOG RENDERS REGARDLESS OF WHO OWNS THE ROLE. A decisions section that only
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

const BODY = `<section class="side-section" id="side-decisions">
  <div id="decisions-chip"></div>
  <input type="text" id="decisions-search">
  <div class="decisions-list" id="decisions-list"></div>
</section>`;

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
    decisions.setDecisionsQuery('');   // module-level state — never leak a query across tests
    delete window.chela.openTaskModal;
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
    // 🔴 COLOURBLIND CUE (the orchestrator is red-weak): the BAD state must be
    // distinguishable WITHOUT relying on the -bad colour class — a non-colour glyph
    // carries it. Empty the glyph in CHIP_META and this assert goes red, even though
    // the class above still says "bad".
    assert.ok(chip().textContent.includes('✕'),
        'the dangling/gone chip carries no non-colour glyph — indistinguishable from OK without colour');
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

// --- CMX-178: click-through to the task-detail modal ------------------------

test('a row whose payload names a task_id is click-through (data-seq + clickable class)', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{
            seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-9 awaiting review',
            payload: { task_id: 't9', title: 'cmx-9 task', branch_name: 'cmx-9', pr_url: 'https://x/9' },
        }],
    };
    await decisions.enterDecisions();
    const row = rows()[0];
    assert.ok(row.classList.contains('feed-row-clickable'), 'a task_id-bearing row must carry the clickable class');
    assert.equal(row.dataset.seq, '1');
});

// 🔴 GUARD: a bare window/inbox-plumbing event (no task_id in its payload) has
// nothing to click through to — this must NOT render as clickable. Deleting
// the `itemFromDecisionPayload(e) != null` check in decisions.js's _rowHtml
// makes every row clickable, including this one, and this assert goes red.
test('🔴 GUARD: a row with no task_id in its payload is NOT click-through', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{
            seq: 1, ts: 1000, type: 'inbox_undeliverable', wid: null,
            summary: 'the inbox address is dead', payload: { wid: '@0', why: 'no claude running' },
        }],
    };
    await decisions.enterDecisions();
    const row = rows()[0];
    assert.equal(row.classList.contains('feed-row-clickable'), false);
    assert.equal(row.dataset.seq, undefined);
});

test('clicking a click-through row opens the task modal with the payload normalised', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{
            seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-9 awaiting review',
            payload: {
                task_id: 't9', title: 'cmx-9 task', run_status: 'awaiting_review',
                branch_name: 'cmx-9', pr_url: 'https://x/9',
            },
        }],
    };
    await decisions.enterDecisions();
    let opened = null;
    window.chela.openTaskModal = (item) => { opened = item; };

    // jsdom (no runScripts) does not execute inline onclick="…" attributes, so
    // this drives the exact function the row's onclick calls (decisions.js's
    // openDecisionTicket), rather than simulating a real click event.
    decisions.openDecisionTicket(document.querySelector('#decisions-list .feed-row'));

    assert.ok(opened, 'clicking a click-through row must call window.chela.openTaskModal');
    assert.equal(opened.task_id, 't9');
    assert.equal(opened.branch_name, 'cmx-9');
    assert.equal(opened.status, 'awaiting_review');
});

test('clicking a row is a no-op (never throws) when no task modal handler is registered', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{ seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'x', payload: { task_id: 't1' } }],
    };
    await decisions.enterDecisions();
    assert.doesNotThrow(() =>
        decisions.openDecisionTicket(document.querySelector('#decisions-list .feed-row')));
});

// --- CMX-178: search ----------------------------------------------------------

test('the search box narrows the rendered list without re-fetching', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 2, last_seq: 2, next_seq: 2,
        events: [
            { seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-1 awaiting review', payload: { branch_name: 'cmx-1' } },
            { seq: 2, ts: 1001, type: 'run_review', wid: '@4', summary: 'cmx-2 awaiting review', payload: { branch_name: 'cmx-2' } },
        ],
    };
    await decisions.enterDecisions();
    assert.equal(rows().length, 2, 'both decisions must render before any search is typed');

    const requestsBefore = requests.length;
    decisions.setDecisionsQuery('cmx-1');

    assert.equal(rows().length, 1, 'the search must hide the non-matching row');
    assert.ok(rows()[0].textContent.includes('cmx-1'));
    assert.equal(requests.length, requestsBefore, 'filtering the held events must not trigger a new fetch');
});

test('a search with no matches shows a "no match" message, not a blank/empty-log message', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{ seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-1 awaiting review', payload: {} }],
    };
    await decisions.enterDecisions();
    decisions.setDecisionsQuery('does-not-exist-anywhere');

    assert.equal(rows().length, 0);
    const empty = document.querySelector('#decisions-list .side-empty');
    assert.ok(empty, 'a no-match search must still render an explanatory empty state');
    assert.ok(empty.textContent.includes('does-not-exist-anywhere'));
    assert.ok(!empty.textContent.includes('No decisions logged yet'),
        'a filtered-to-zero result must read differently from a genuinely empty log');
});

test('clearing the search brings back the full held list, still with no re-fetch', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 2, last_seq: 2, next_seq: 2,
        events: [
            { seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-1 awaiting review', payload: {} },
            { seq: 2, ts: 1001, type: 'run_review', wid: '@4', summary: 'cmx-2 awaiting review', payload: {} },
        ],
    };
    await decisions.enterDecisions();
    decisions.setDecisionsQuery('cmx-1');
    assert.equal(rows().length, 1);
    decisions.setDecisionsQuery('');
    assert.equal(rows().length, 2, 'clearing the query must restore every held event');
});
