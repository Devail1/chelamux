// knGraphModel — the pure data step feeding the Knowledge tab's sigma.js/graphology
// graph renderer (chela/dashboard/static/js/knowledge.js).
//
// sigma.js needs a real WebGL context to render, which jsdom does not provide (see
// tests/wall.test.mjs's jsdom setup vs. this one — this suite deliberately never
// touches window.chelaGraphLibs or instantiates Sigma). So the renderer itself
// (knRenderSigma) is untested glue; what's tested is everything upstream of it:
// deterministic seed positions, type -> color resolution, and edge filtering
// (dangling/self-loop/duplicate edges dropped) — exactly the correctness properties
// the old hand-rolled SVG renderer had to get right too.
//
// Run: node --test tests/knowledge_graph.test.mjs (also run by `uv run pytest -q`
// via tests/test_js_suites.py, which globs the whole repo for *.test.mjs).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

let kn;

before(async () => {
    // knowledge.js imports util.js, which reads window/document at MODULE SCOPE
    // (document.title, window.location.pathname, document.addEventListener, ...) —
    // those globals must exist before the import, exactly as in a real browser.
    const dom = new JSDOM('<!doctype html><html><body></body></html>',
        { url: 'http://localhost:5005/' });
    for (const k of ['window', 'document', 'getComputedStyle', 'HTMLElement', 'Element', 'Node']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.window.chela = globalThis.window.chela || {};
    kn = await import('../chela/dashboard/static/js/knowledge.js');
});

// A stand-in for knCssVar(name, fallback) that just returns the fallback —
// deterministic, no DOM read, so tests assert on the exact fallback hexes.
const cssVar = (_name, fallback) => fallback;

test('knGraphModel: colors resolve by type (project stays a fixed hex, not themed)', () => {
    const g = {
        nodes: [
            { id: 'agents/a.md', title: 'Agent A', type: 'Agent' },
            { id: 'runs/r.md', title: 'Run R', type: 'Dispatch Run' },
            { id: 'schedules/s.md', title: 'Sched S', type: 'Scheduled Task' },
            { id: 'projects/p.md', title: 'Project P', type: 'Project' },
            { id: 'x.md', title: 'X', type: 'Something Else' },
        ],
        edges: [],
    };
    const model = kn.knGraphModel(g, cssVar);
    const color = id => model.nodes.find(n => n.id === id).color;
    assert.equal(color('agents/a.md'), '#3fb950', 'agent -> green');
    assert.equal(color('runs/r.md'), '#58a6ff', 'run -> accent blue');
    assert.equal(color('schedules/s.md'), '#d29922', 'schedule -> yellow');
    assert.equal(color('projects/p.md'), '#a371f7', 'project -> fixed purple');
    assert.equal(color('x.md'), '#8b949e', 'unknown type -> dim fallback');
});

test('knGraphModel: dangling, self-loop, and duplicate edges are all dropped', () => {
    const g = {
        nodes: [
            { id: 'a', title: 'A', type: 'Agent' },
            { id: 'b', title: 'B', type: 'Agent' },
        ],
        edges: [
            { source: 'a', target: 'b' },
            { source: 'a', target: 'b' },     // duplicate
            { source: 'a', target: 'ghost' }, // dangling — no such node
            { source: 'b', target: 'b' },     // self-loop
        ],
    };
    const model = kn.knGraphModel(g, cssVar);
    assert.equal(model.edges.length, 1, 'only the single real a->b edge should survive');
    assert.deepEqual(model.edges[0], { source: 'a', target: 'b' });
});

test('knGraphModel: seed positions are deterministic and never collide', () => {
    const g = {
        nodes: Array.from({ length: 12 }, (_, i) => ({ id: `n${i}`, title: `N${i}`, type: 'Agent' })),
        edges: [],
    };
    const m1 = kn.knGraphModel(g, cssVar);
    const m2 = kn.knGraphModel(g, cssVar);
    assert.deepEqual(m1, m2, 'pure function: identical input must give identical output');

    const seen = new Set();
    for (const n of m1.nodes) {
        const key = `${n.x.toFixed(6)},${n.y.toFixed(6)}`;
        assert.ok(!seen.has(key), `seed position collided at ${key} — forceAtlas2 would get stuck`);
        seen.add(key);
    }
});

test('knNodeColor: matches knGraphModel\'s per-node resolution for the same type', () => {
    assert.equal(kn.knNodeColor('Agent', cssVar), '#3fb950');
    assert.equal(kn.knNodeColor('Project', cssVar), '#a371f7');
});
