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

// #btn-decisions + the #decisions-menu wrapper (not just the chip/list bare)
// are real here, not decoration — the CMX-182 dismisser tests below drive the
// actual light-dismiss listener, which looks both up by id.
const BODY = `<button id="btn-decisions"></button>
<div class="popover decisions-menu" id="decisions-menu" style="display:none;">
  <div id="decisions-chip"></div>
  <span id="decisions-unread" hidden></span>
  <input type="text" class="decisions-search" id="decisions-search">
  <div class="decisions-list" id="decisions-list"></div>
</div>`;

let decisions, orchestrator;
let requests;
let LOG_RESPONSE;
let STATUS_RESPONSE;
let DISPATCHER_RESPONSE;
let DISPATCHER_REJECT;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'MouseEvent']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.fetch = (url) => {
        const path = String(url);
        requests.push(path);
        if (path.includes('/api/orchestrator/status')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(STATUS_RESPONSE) });
        }
        if (path.includes('/api/dispatcher')) {
            if (DISPATCHER_REJECT) return Promise.reject(new Error('dispatcher unreachable'));
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(DISPATCHER_RESPONSE) });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(LOG_RESPONSE) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    orchestrator = await import('../chela/dashboard/static/js/orchestrator.js');
    decisions = await import('../chela/dashboard/static/js/decisions.js');
});

beforeEach(() => {
    requests = [];
    STATUS_RESPONSE = { wid: null, name: null, state: 'unregistered', why: '', queued: 0 };
    LOG_RESPONSE = { boot_id: 'b1', events: [], gap: null, first_seq: 0, last_seq: 0, next_seq: 0 };
    DISPATCHER_RESPONSE = { configured: true, workflows: [] };   // no match — the common "not found" case
    DISPATCHER_REJECT = false;
    decisions.setDecisionsQuery('');   // module-level state — never leak a query across tests
    delete window.chela.openTaskModal;
    decisions.hideDecisionsMenu();     // closes it AND tears down any dismiss listener from the prior test
});

// The dismiss listener is armed via `setTimeout(…, 0)` (openDecisionsMenu) so
// that the SAME click which opened the popover (e.g. the #btn-decisions
// button click) doesn't immediately re-trigger it. Tests need to wait out
// that tick before dispatching a click of their own.
const flushMicrotask = () => new Promise((r) => setTimeout(r, 0));

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

test('clicking a click-through row with NO dispatcher match falls back to the payload, marked partial', async () => {
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
    DISPATCHER_RESPONSE = { configured: true, workflows: [] };   // no run anywhere named t9
    await decisions.enterDecisions();
    let opened = null;
    window.chela.openTaskModal = (item) => { opened = item; };

    // jsdom (no runScripts) does not execute inline onclick="…" attributes, so
    // this drives the exact function the row's onclick calls (decisions.js's
    // openDecisionTicket), rather than simulating a real click event.
    await decisions.openDecisionTicket(document.querySelector('#decisions-list .feed-row'));

    assert.ok(requests.some(r => r.includes('/api/dispatcher')),
        'a click must try the dispatcher first, even when it will come up empty');
    assert.ok(opened, 'clicking a click-through row must call window.chela.openTaskModal');
    assert.equal(opened.task_id, 't9');
    assert.equal(opened.branch_name, 'cmx-9');
    assert.equal(opened.status, 'awaiting_review');
    // 🔴 GUARD: a partial ticket that does not SAY it is partial reads as a
    // lie ("No brief recorded" when the truth is "not loaded here"). Dropping
    // the DISPATCHER_AGED_OUT_NOTE stamp in partialItemFromDecisionPayload
    // makes this assert fail.
    assert.ok(opened.body && opened.body.includes('aged out'),
        'the fallback ticket must carry a visible aged-out/partial notice');
});

test('clicking a click-through row WITH a dispatcher match opens the authoritative run object', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{
            seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-9 awaiting review',
            payload: { task_id: 't9', title: 'cmx-9 task', run_status: 'awaiting_review' },
        }],
    };
    // 🔴 GUARD: this run object carries `brief` — a field ONLY the dispatcher
    // has, never the decision payload. Asserting on it proves openTaskModal
    // received THIS object, not one built by itemFromDecisionPayload/
    // partialItemFromDecisionPayload.
    DISPATCHER_RESPONSE = {
        configured: true,
        workflows: [{
            open_tasks: [], backlog_items: [],
            active_runs: [{ task_id: 't9', brief: 'The FULL brief, from the dispatcher.', status: 'awaiting_review' }],
            awaiting_review_runs: [], recent_runs: [],
        }],
    };
    await decisions.enterDecisions();
    let opened = null;
    window.chela.openTaskModal = (item) => { opened = item; };

    await decisions.openDecisionTicket(document.querySelector('#decisions-list .feed-row'));

    assert.ok(opened, 'a dispatcher match must still open the modal');
    assert.equal(opened.brief, 'The FULL brief, from the dispatcher.',
        'openTaskModal must receive the dispatcher\'s own run object, not the payload-normalised one');
});

test('clicking a click-through row when the dispatcher fetch REJECTS still opens the fallback, never throws', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{
            seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-9 awaiting review',
            payload: { task_id: 't9', title: 'cmx-9 task', run_status: 'awaiting_review' },
        }],
    };
    DISPATCHER_REJECT = true;
    await decisions.enterDecisions();
    let opened = null;
    window.chela.openTaskModal = (item) => { opened = item; };

    await assert.doesNotReject(() =>
        decisions.openDecisionTicket(document.querySelector('#decisions-list .feed-row')));

    assert.ok(opened, 'a rejected dispatcher fetch must still open the fallback ticket');
    assert.equal(opened.task_id, 't9');
    assert.ok(opened.body && opened.body.includes('aged out'), 'the fallback must still carry the partial notice');
});

test('clicking a row is a no-op (never throws) when no task modal handler is registered', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{ seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'x', payload: { task_id: 't1' } }],
    };
    await decisions.enterDecisions();
    await assert.doesNotReject(() =>
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

test('🔴 GUARD: an active search says what it actually searched (N of M loaded)', async () => {
    // The popover filters the events it HOLDS — `_refreshLog` pulls bounded batches, it
    // does not have the whole log. A filtered list with no qualifier silently reads as
    // "these are the only matches in your history" when it means "among the N I have".
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 3, last_seq: 3, next_seq: 3,
        events: [
            { seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-1 awaiting review', payload: { branch_name: 'cmx-1' } },
            { seq: 2, ts: 1001, type: 'run_review', wid: '@4', summary: 'cmx-2 awaiting review', payload: { branch_name: 'cmx-2' } },
            { seq: 3, ts: 1002, type: 'run_review', wid: '@5', summary: 'cmx-3 awaiting review', payload: { branch_name: 'cmx-3' } },
        ],
    };
    await decisions.enterDecisions();

    // No query: nothing to qualify — the list IS everything held.
    assert.equal(document.querySelector('#decisions-list .decisions-scope'), null,
        'an empty search box must not claim a scope — the list is simply everything held');

    decisions.setDecisionsQuery('cmx-1');
    const scope = document.querySelector('#decisions-list .decisions-scope');
    assert.ok(scope, 'an active search must say what it searched');
    assert.match(scope.textContent, /\b1\b[^0-9]*\b3\b/,
        'the scope must name BOTH the match count and the held count (e.g. "1 of 3 loaded") — ' +
        'a bare match count is the claim this guard exists to prevent');

    decisions.setDecisionsQuery('');
    assert.equal(document.querySelector('#decisions-list .decisions-scope'), null,
        'clearing the box must drop the qualifier again');
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

// 🔴 GUARD: filtering is a VIEW concern (what's rendered); the unread badge is
// a SEEN concern (what a human has looked at) — they must never be coupled.
// Computing the badge over `filterDecisionEvents(_events, _query)` instead of
// the full `_events` would silently mark a filtered-away unread event as seen,
// and nothing else would catch it: the row just wouldn't be on screen to miss.
test('🔴 GUARD: filtering the rendered list does not touch the unread badge', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{ seq: 1, ts: 1000, type: 'finished', wid: '@3', summary: 'cmx-seed', payload: {} }],
    };
    await decisions.enterDecisions();

    // A fresh, definitely-unseen event (a seq far beyond anything else in this
    // suite) so the badge has something real to count, whatever value this
    // file's shared lastSeen cursor already sits at.
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 100000, next_seq: 100000,
        events: [{
            seq: 100000, ts: 2000, type: 'run_review', wid: '@4',
            summary: 'cmx-unread awaiting review', payload: { branch_name: 'cmx-unread' },
        }],
    };
    await decisions.tickDecisions();

    const badge = document.querySelector('#decisions-unread');
    const before = badge.textContent;
    assert.notEqual(before, '', 'the freshly-arrived event must register as unread before any filter runs');

    // Filter down to ONLY the old, already-seen event — hiding the unread row
    // from the rendered list entirely.
    decisions.setDecisionsQuery('cmx-seed');
    assert.equal(rows().length, 1, 'the filter must actually hide the unread row from view');
    assert.ok(!rows()[0].textContent.includes('cmx-unread'));
    assert.equal(document.querySelector('#decisions-unread').textContent, before,
        'filtering the rendered VIEW must not change the unread badge — filtering is a VIEW concern, seen-state is not');
});

// --- CMX-182: the popover must not swallow clicks meant for its own contents --
//
// openDecisionsMenu (decisions.js) light-dismisses the popover with a `document`
// click listener — same pattern as nav.js's openNewMenu/openPrimaryMenu
// (tests/topbarmenu.test.mjs) — but #decisions-menu is the only one of the
// three that holds an INTERACTIVE descendant (#decisions-search). Clicking
// into the box to focus it used to bubble straight to the light-dismiss
// listener and close the popover out from under the click (Liav, 2026-07-26).
// The fix makes the dismisser itself containment-aware (ignore any click
// whose target is inside #decisions-menu or #btn-decisions) rather than
// requiring every interactive descendant to stop its own propagation — so
// these tests drive the REAL dismisser with real bubbling MouseEvents, not a
// source-text match.
const decisionsMenu = () => document.getElementById('decisions-menu');
const isOpen = () => decisionsMenu().style.display !== 'none';
const clickOn = (el) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));

test('🔴 GUARD: a click on the search box does not close the popover it lives inside', async () => {
    decisions.openDecisionsMenu();
    await flushMicrotask();
    assert.ok(isOpen(), 'openDecisionsMenu must show the popover');

    clickOn(document.getElementById('decisions-search'));

    assert.ok(isOpen(), 'a click on #decisions-search must not reach the light-dismiss listener');
});

test('🔴 GUARD: a click anywhere else inside the popover (list/chip/padding) also does not close it', async () => {
    decisions.openDecisionsMenu();
    await flushMicrotask();

    clickOn(document.getElementById('decisions-list'));
    assert.ok(isOpen(), 'a click on #decisions-list must not close the popover');

    clickOn(document.getElementById('decisions-chip'));
    assert.ok(isOpen(), 'a click on #decisions-chip must not close the popover');

    clickOn(decisionsMenu());
    assert.ok(isOpen(), 'a click on the popover\'s own padding must not close it');
});

test('🔴 GUARD: an inside click does not disarm the dismisser — a SUBSEQUENT outside click still closes it', async () => {
    decisions.openDecisionsMenu();
    await flushMicrotask();

    clickOn(document.getElementById('decisions-search'));   // inside click first
    assert.ok(isOpen(), 'sanity: the inside click must not have already closed it');

    clickOn(document.body);   // then a real outside click

    assert.ok(!isOpen(),
        'an outside click AFTER an inside click must still close the popover — a one-shot listener that ' +
        'silently disarmed itself on the inside click would leave the popover permanently undismissable');
});

test('a click outside the popover closes it', async () => {
    decisions.openDecisionsMenu();
    await flushMicrotask();

    clickOn(document.body);

    assert.ok(!isOpen(), 'a click outside #decisions-menu and #btn-decisions must close the popover');
});

test('clicking a click-through row closes the popover itself, since the dismisser no longer fires on it', async () => {
    LOG_RESPONSE = {
        boot_id: 'b1', gap: null, first_seq: 1, last_seq: 1, next_seq: 1,
        events: [{
            seq: 1, ts: 1000, type: 'run_review', wid: '@3', summary: 'cmx-9 awaiting review',
            payload: { task_id: 't9', title: 'cmx-9 task', run_status: 'awaiting_review' },
        }],
    };
    await decisions.enterDecisions();
    window.chela.openTaskModal = () => {};
    decisions.openDecisionsMenu();
    await flushMicrotask();
    assert.ok(isOpen(), 'sanity: the popover must be open before the row click');

    await decisions.openDecisionTicket(document.querySelector('#decisions-list .feed-row'));

    assert.ok(!isOpen(),
        'opening a ticket from a row must close the decisions popover itself — the row click never reaches ' +
        'document (it is inside #decisions-menu), so nothing else will close it');
});


// 🔴 GUARD (CMX-197 round 2): the panel must SUBSCRIBE to the judge kinds.
//
// `DECISION_TYPES` is what the scoped /api/log fetch asks for (`qs.append('type', t)`), so
// a kind missing from it is a kind the Decisions panel never receives — no matter how
// correctly the inbox emits it. Rename either entry and the verdict this whole ticket
// exists to surface silently stops reaching the one panel built to show decisions.
test('🔴 GUARD: the decisions panel subscribes to both judge verdict kinds', () => {
    assert.ok(decisions.DECISION_TYPES.includes('run_judge_clean'),
        `run_judge_clean must be subscribed, got: ${decisions.DECISION_TYPES.join(',')}`);
    assert.ok(decisions.DECISION_TYPES.includes('run_judge_cannot_verify'),
        `run_judge_cannot_verify must be subscribed, got: ${decisions.DECISION_TYPES.join(',')}`);
    // ...and the kinds that already worked are still there — this ticket must not trade
    // one silent verdict for another.
    for (const t of ['run_review', 'run_changes_requested', 'run_needs_human']) {
        assert.ok(decisions.DECISION_TYPES.includes(t), `${t} lost its subscription`);
    }
});
