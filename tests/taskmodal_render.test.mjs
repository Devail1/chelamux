// TASK MODAL — openTaskModal() actually reaches the DOM
// (chela/dashboard/static/js/taskmodal.js). Every existing suite that touches
// openTaskModal (tests/decisions.test.mjs) replaces `window.chela.openTaskModal`
// with a stub before calling it, so the real function's own rendering — in
// particular its title line, `knInline(displayTitle(item.title || '(untitled)')
// .slice(0, 300))` at taskmodal.js:156 — is driven by nothing anywhere in the
// suite. This is the SECOND of the "two callers, one guarded" call sites
// (DEFEAT_SHAPES shape 7; kanban.js's card title is the other, guarded
// independently in tests/kanban_flatten.test.mjs) — kept in its own file so a
// fix at one call site cannot silently pass for both.
//
// Run: node --test tests/taskmodal_render.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const BODY = `
<div id="modal-task" class="modal">
  <div id="task-modal-content"></div>
</div>`;

let taskmodal;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        // defineProperty, NOT assignment: from node 21 `globalThis.navigator` has only a
        // getter (see tests/wall.test.mjs).
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;

    // Browser-faithful import order (main.js is the entry) — same bootstrap as
    // tests/taskmodal_judge_badge.test.mjs.
    await import('../chela/dashboard/static/js/main.js');
    taskmodal = await import('../chela/dashboard/static/js/taskmodal.js');
});

// 🔴 GUARD (round 3, PR #350): calls the REAL openTaskModal (not a stub) with a title
// carrying a mid-string `**bold**` span — the case displayTitle() deliberately leaves
// untouched so knInline is the thing that has to render it (see
// taskmodal_model.test.mjs) — and reads the REAL `.task-modal-title` element back out
// of `#task-modal-content`. If taskmodal.js:156's `knInline(...)` call is dead-coded
// (bypassed in favour of the bare displayTitle() string), this fails on literal `**`
// where `<strong>` should be.
test('openTaskModal: a title with a mid-string bold span renders through knInline as <strong>, not literal asterisks', () => {
    taskmodal.openTaskModal({ title: 'ship **the wall** now', status: 'open' });

    const titleEl = document.querySelector('#task-modal-content .task-modal-title');
    assert.ok(titleEl, 'openTaskModal did not render a .task-modal-title element');
    assert.equal(titleEl.innerHTML, 'ship <strong>the wall</strong> now',
        `task modal title did not render through knInline — got: "${titleEl.innerHTML}"`);
});

// 🔴 GUARD (round 4, PR #350): the THIRD knMd/knInline call site this PR edited
// (taskmodal.js:116's `knMd(s.detail)` inside `_timelineHtml`, which dropped the
// 'review.md' argument). tests/taskmodal_model.test.mjs pins knMd as a pure
// function; this drives the REAL openTaskModal with a `review_history` payload
// (which every other test in this suite passes as absent, so `_timelineHtml`
// never reaches the knMd call) and reads the REAL `.task-modal-timeline-body`
// element back out of `#task-modal-content`. If taskmodal.js:116's `knMd(...)`
// call is dead-coded (bypassed in favour of a plain escHtml() of the raw
// detail, wrapped without knMd's own `<p>`), this fails on literal `**` and a
// missing `<p>` wrapper where `<strong>` inside `<p>` should be.
test('openTaskModal: a review-timeline entry with a markdown detail renders through knMd, not literal escaped text', () => {
    const review_history = JSON.stringify([
        { round: 1, at: '2026-07-20T10:00:00+00:00', body: 'ship **the wall** now', verdict: 'changes_requested' },
    ]);
    taskmodal.openTaskModal({ title: 'plain title', status: 'open', review_history });

    const bodyEl = document.querySelector('#task-modal-content .task-modal-timeline-body');
    assert.ok(bodyEl, 'openTaskModal did not render a .task-modal-timeline-body element');
    assert.equal(bodyEl.innerHTML, '<p>ship <strong>the wall</strong> now</p>',
        `review-timeline body did not render through knMd — got: "${bodyEl.innerHTML}"`);
});
