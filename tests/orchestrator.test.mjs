// ORCHESTRATOR STATE (client side) — one shared fact, every listener told.
//
// terminals.js (the pane-title toggle) and decisions.js (the owner chip) both read
// orchestratorState() and both react to onOrchestratorChange — this proves the module
// keeps exactly ONE state (a bug here would let a pane button and the chip disagree
// about who owns the role) and that a REFUSED subscribe/release never overwrites it
// with garbage (a 404/409 must leave the last-known-good state alone).
//
// Run: node --test tests/orchestrator.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

let orchestrator;
let RESPONSES;   // path -> response body

before(async () => {
    // orchestrator.js imports util.js, which reads `document`/`window` at module
    // scope (the tab-signal favicon bootstrap) — a minimal jsdom, not a mock.
    const dom = new JSDOM('<!doctype html><html><body></body></html>',
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.fetch = (url) => {
        const path = String(url);
        const body = RESPONSES[path];
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    orchestrator = await import('../chela/dashboard/static/js/orchestrator.js');
});

beforeEach(() => {
    RESPONSES = {};
});

test('subscribing applies the server response as the new shared state', async () => {
    RESPONSES['/api/orchestrator/subscribe'] = { ok: true, wid: '@3', name: 'agent-a', state: 'ok', why: '', queued: 0 };
    const result = await orchestrator.orchestratorSubscribe('@3');
    assert.equal(result.ok, true);
    assert.equal(orchestrator.orchestratorState().wid, '@3');
});

test('a REFUSED subscribe (dead window) never overwrites the last-known-good state', async () => {
    RESPONSES['/api/orchestrator/subscribe'] = { ok: true, wid: '@3', name: 'agent-a', state: 'ok', why: '', queued: 0 };
    await orchestrator.orchestratorSubscribe('@3');

    RESPONSES['/api/orchestrator/subscribe'] = { ok: false, error: 'no such window: @999' };
    const result = await orchestrator.orchestratorSubscribe('@999');

    assert.equal(result.ok, false);
    // 🔴 GUARD: the refusal must not clobber who actually owns the slot.
    assert.equal(orchestrator.orchestratorState().wid, '@3', 'a refused subscribe changed the shared owner');
});

test('a GUARDED release refusal (not the current owner) leaves state alone', async () => {
    RESPONSES['/api/orchestrator/subscribe'] = { ok: true, wid: '@3', name: 'agent-a', state: 'ok', why: '', queued: 0 };
    await orchestrator.orchestratorSubscribe('@3');

    RESPONSES['/api/orchestrator/release'] = { ok: false, wid: '@4', orchestrator: '@3' };
    const result = await orchestrator.orchestratorRelease('@4');

    assert.equal(result.ok, false);
    assert.equal(orchestrator.orchestratorState().wid, '@3', 'a refused release changed the shared owner');
});

test('every registered listener hears a change, and one bad listener does not silence the rest', async () => {
    const heard = [];
    const off1 = orchestrator.onOrchestratorChange(() => { throw new Error('boom'); });
    const off2 = orchestrator.onOrchestratorChange(s => heard.push(s.wid));

    RESPONSES['/api/orchestrator/subscribe'] = { ok: true, wid: '@9', name: 'x', state: 'ok', why: '', queued: 0 };
    await orchestrator.orchestratorSubscribe('@9');

    assert.deepEqual(heard, ['@9']);
    off1(); off2();
});
