// THE PERSONAS PANEL, IN A REAL DOM — it must render EVERY declared persona, not a fixed few.
//
// The persona layer is only "visible" if the panel actually renders the whole registry. A
// source grep ("personas.js maps over a list") would assert the artifact that was written,
// never the one that runs — the exact anti-pattern tests/sidebar.test.mjs abolished. So this
// drives the REAL renderPersonas() into a REAL #personas-list (jsdom) and counts the cards it
// emits: three personas in ⇒ three .persona-card nodes out. Break the render to drop one
// (render list[0] only, cap the map, hardcode two cards) and the count goes red — it asserts
// what RENDERS, not what the source says.
//
// Run: node --test tests/personas.test.mjs  (pytest runs it via tests/test_js_suites.py;
// it needs `npm ci` for jsdom — CHELA_REQUIRE_JS_TESTS makes a missing jsdom a FAILURE.)
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const BODY = '<div class="panel" id="panel-personas"><div class="personas-list" id="personas-list"></div></div>';

// The three declared personas, shaped like /api/personas' payload entries. Mirrors the Python
// registry (judge · critic · orchestrator) — the layer this panel must show in full.
const THREE = [
    { key: 'judge', title: 'The Judge', trigger: 'awaiting_review', mode: 'adjudicative',
      action_surface: 'verdict', prompt_source: 'chela/judge.py', summary: 's', enabled: true,
      status: 'reviewing cmx-3', docs: ['docs/PERSONA_PATTERN.md'] },
    { key: 'critic', title: 'The Critic', trigger: 'dispatch', mode: 'advisory',
      action_surface: 'advisory comment', prompt_source: 'chela/critic.py', summary: 's',
      enabled: true, docs: ['docs/PERSONA_PATTERN.md'] },
    { key: 'orchestrator', title: 'The Orchestrator', trigger: 'boot + inbox event',
      mode: 'attended-autonomous', action_surface: 'gated chela commands',
      prompt_source: 'chela/personas/orchestrator.md', summary: 's', enabled: false,
      docs: ['docs/ORCHESTRATOR_PERSONA.md'] },
];

let personas;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    personas = await import('../chela/dashboard/static/js/personas.js');
});

const cards = () => document.querySelectorAll('#personas-list .persona-card');

test('the panel renders ONE card per declared persona — three in, three out', () => {
    personas.renderPersonas(THREE);
    assert.equal(cards().length, 3, 'the Personas panel did not render all three personas');
    // …and it renders the RIGHT three, by key — a render that emitted three blanks would pass a
    // bare count but fail this.
    const keys = [...cards()].map(c => c.dataset.persona);
    assert.deepEqual(keys, ['judge', 'critic', 'orchestrator']);
});

test('drop a persona from the payload and a card disappears — the panel is driven by the registry', () => {
    // The registry (its serialized payload) is the SOLE source of what renders: remove the
    // orchestrator and exactly two cards remain, orchestrator gone. This is what "remove one
    // from the registry → red" means at the render boundary.
    personas.renderPersonas(THREE.filter(p => p.key !== 'orchestrator'));
    const keys = [...cards()].map(c => c.dataset.persona);
    assert.deepEqual(keys, ['judge', 'critic']);
    assert.ok(!keys.includes('orchestrator'));
});

test('each card renders the declared trigger and mode — the panel SHOWS the layer', () => {
    personas.renderPersonas(THREE);
    const orch = document.querySelector('#personas-list .persona-card[data-persona="orchestrator"]');
    assert.ok(orch, 'the orchestrator card is missing');
    // The text a human reads: its trigger and its mode are on the rendered node.
    assert.ok(orch.textContent.includes('boot + inbox event'), 'the orchestrator trigger is not rendered');
    assert.ok(orch.textContent.includes('attended-autonomous'), 'the orchestrator mode is not rendered');
    // The judge's live status wins over the plain enabled pill.
    const judge = document.querySelector('#personas-list .persona-card[data-persona="judge"]');
    assert.ok(judge.textContent.includes('reviewing cmx-3'), 'the judge live status is not rendered');
});
