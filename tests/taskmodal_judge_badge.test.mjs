// TASK MODAL — the judge-state severity badge (chela/dashboard/static/js/taskmodal.js's
// `_JUDGE_BADGE`). Exported (like dispatcher.js's `_runDisplayId`/`_runPrCell`) so this can
// pin the severity mapping without driving the full modal DOM.
//
// Run: node --test tests/taskmodal_judge_badge.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

let taskmodal;

before(async () => {
    const dom = new JSDOM('<!doctype html><html><body></body></html>',
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

    // Browser-faithful import order (main.js is the entry; nav <-> main is a cycle —
    // import anything else first and nav.js's `let`s are in their TDZ, same as
    // tests/wallnav.test.mjs).
    await import('../chela/dashboard/static/js/main.js');
    taskmodal = await import('../chela/dashboard/static/js/taskmodal.js');
});

// 🔴 GUARD (CMX-239 round 2): `blocked_race` is a CONFIRMED blocking finding the run row
// never recorded (the CAS-refused race) — it must read exactly as loud as an ordinary
// `blocked`, never fall to `cannot_verify`'s low-priority tier (the `|| 'badge-priority-low'`
// fallback in `_judgeHtml` would silently swallow a renamed/missing entry into that same
// low tier, so the value is pinned explicitly, not just presence-checked).
test('blocked_race renders as loud as blocked, never the cannot_verify low-priority tier', () => {
    assert.equal(taskmodal._JUDGE_BADGE.blocked_race, 'badge-off',
        `blocked_race must share blocked's badge class, got: ${taskmodal._JUDGE_BADGE.blocked_race}`);
    assert.equal(taskmodal._JUDGE_BADGE.blocked_race, taskmodal._JUDGE_BADGE.blocked,
        'blocked_race must be exactly as loud as blocked');
    assert.notEqual(taskmodal._JUDGE_BADGE.blocked_race, taskmodal._JUDGE_BADGE.cannot_verify,
        'blocked_race must NOT share cannot_verify\'s low-priority tier');
});
