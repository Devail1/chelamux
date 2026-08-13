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
