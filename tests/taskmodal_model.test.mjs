// TASK-DETAIL MODAL MODEL — pure brief-markdown + review-timeline guards
// (chela/dashboard/static/js/taskmodalmodel.js). No DOM: these are straight
// function-of-inputs checks, each written to go RED under one specific
// corruption of the real logic (a guard that survives its own corruption is
// decoration, not a guard).
//
// Run: node --test tests/taskmodal_model.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

let tm;

before(async () => {
    // taskmodalmodel.js imports knowledge.js's knMd, which imports util.js —
    // util.js reads window/document at MODULE SCOPE (window.location.pathname,
    // document.addEventListener, ...), so those globals must exist before the
    // import, same bootstrap as tests/knowledge_graph.test.mjs.
    const dom = new JSDOM('<!doctype html><html><body></body></html>',
        { url: 'http://localhost:5005/' });
    for (const k of ['window', 'document', 'getComputedStyle', 'HTMLElement', 'Element', 'Node']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.window.chela = globalThis.window.chela || {};
    tm = await import('../chela/dashboard/static/js/taskmodalmodel.js');
});

// --- briefHtml: pins the knMd CONTRACT the brief pane depends on -----------

test('briefHtml: empty/null/undefined text renders nothing', () => {
    // Base-case pin, not a corruption-sensitive guard on its own (knMd's own
    // `src || ''` already tolerates null/undefined, so dropping briefHtml's
    // `if (!text)` short-circuit does not by itself go RED here) — it exists so
    // taskmodal.js's "no brief" fallback path (openTaskModal calling this with
    // an empty string) has a pinned, explicit expectation. The heading/list/code
    // test below is what actually goes RED on a real regression (see its comment).
    assert.equal(tm.briefHtml(''), '');
    assert.equal(tm.briefHtml(null), '');
    assert.equal(tm.briefHtml(undefined), '');
});

test('briefHtml: a heading + numbered list + inline code render via knMd', () => {
    const src = '### OBJECTIVE\nBuild `sample()` with these steps:\n1. First step with `code`.\n2. Second step.\n\nSome paragraph.\n';
    const html = tm.briefHtml(src);
    // 🔴 GUARD: this is the EXACT knMd output for this fixture (verified against
    // knowledge.js directly) — taskmodal.js's brief pane depends on this shape:
    // a `#{1,4}` heading renders <h3 class="kn-mh">, inline `` `code` `` becomes
    // <code>, and (knMd has NO numbered-list support — only `-`/`*` bullets) a
    // "1. " line renders as a plain <p>, numeral preserved as text. Regressing
    // ANY of these (heading level, code wrapping, or accidentally starting to
    // eat the "1. " prefix) changes this string and goes RED.
    assert.equal(
        html,
        '<h3 class="kn-mh">OBJECTIVE</h3>'
        + '<p>Build <code>sample()</code> with these steps:</p>'
        + '<p>1. First step with <code>code</code>.</p>'
        + '<p>2. Second step.</p>'
        + '<p>Some paragraph.</p>',
    );
});

// --- timelineSteps: never throws, always an ordered list of {state, detail} --

test('timelineSteps: null/empty/malformed JSON all resolve to [], never throw', () => {
    // The empty-guard (`if (!reviewHistoryJson) return [];`) covers the common
    // case — a brand-new run with no review yet — as a cheap branch before ever
    // calling JSON.parse. null/''/undefined all take it.
    assert.deepEqual(tm.timelineSteps(null), []);
    assert.deepEqual(tm.timelineSteps(''), []);
    assert.deepEqual(tm.timelineSteps(undefined), []);
    // 🔴 GUARD: '{not json' is NOT falsy, so it skips the empty-guard and reaches
    // JSON.parse, which throws a SyntaxError — remove the try/catch around that
    // parse (or replace it with a bare `JSON.parse(reviewHistoryJson)`) and this
    // specific assertion goes RED with an uncaught SyntaxError (verified).
    assert.deepEqual(tm.timelineSteps('{not json'), []);
});

test('timelineSteps: well-formed JSON that is not a list resolves to []', () => {
    // 🔴 GUARD: drop the `if (!Array.isArray(parsed)) return [];` guard and a
    // bare object payload reaches `.filter`, which throws (objects have no
    // .filter) — this is the corruption to try when verifying the guard.
    assert.deepEqual(tm.timelineSteps('{"round":1}'), []);
    assert.deepEqual(tm.timelineSteps('"just a string"'), []);
    assert.deepEqual(tm.timelineSteps('42'), []);
});

test('timelineSteps: non-object array entries are dropped, valid ones map to {state, detail}, order preserved', () => {
    const raw = JSON.stringify([
        { round: 1, at: '2026-07-20T10:00:00+00:00', body: 'fix the flaky test', verdict: 'changes_requested' },
        'a stray string entry',          // dropped
        null,                            // dropped
        42,                              // dropped
        { round: 2, at: '2026-07-20T11:00:00+00:00', body: 'looks good now', verdict: 'reopened' },
    ]);
    const steps = tm.timelineSteps(raw);
    assert.equal(steps.length, 2, 'the 3 malformed entries must be dropped, not crash or pass through');
    assert.deepEqual(steps[0], {
        round: 1, at: '2026-07-20T10:00:00+00:00', state: 'changes_requested', detail: 'fix the flaky test',
    });
    assert.deepEqual(steps[1], {
        round: 2, at: '2026-07-20T11:00:00+00:00', state: 'reopened', detail: 'looks good now',
    });
});

test('timelineSteps: a missing verdict/body degrades to a safe default, not undefined/crash', () => {
    const raw = JSON.stringify([{ round: 1, at: 't' }]);
    const steps = tm.timelineSteps(raw);
    assert.equal(steps.length, 1);
    assert.equal(steps[0].state, 'review', 'no verdict on the row -> a generic, non-empty state label');
    assert.equal(steps[0].detail, '', 'no body on the row -> empty detail, never undefined (would render "undefined")');
});
