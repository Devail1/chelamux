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

test('knGraphModel: colors resolve by type to the fixed Okabe-Ito palette (not themed)', () => {
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
    assert.equal(color('agents/a.md'), '#009E73', 'agent -> Okabe-Ito bluish green');
    assert.equal(color('runs/r.md'), '#0072B2', 'run -> Okabe-Ito blue');
    assert.equal(color('schedules/s.md'), '#E69F00', 'schedule -> Okabe-Ito orange');
    assert.equal(color('projects/p.md'), '#CC79A7', 'project -> Okabe-Ito reddish purple');
    assert.equal(color('x.md'), '#8b949e', 'unknown type -> dim fallback');
});

// Liav is red-weak (deuteranomaly): a plain per-type hue is not enough on its
// own ([[user_colorblind]]) — every node must also carry a non-hue cue (a
// glyph prefix on its label) so the type is legible in greyscale too.
test('knNodeLabel: every type renders a distinct non-hue glyph prefix (colorblind guard)', () => {
    const types = ['Agent', 'Dispatch Run', 'Scheduled Task', 'Project', 'Something Else'];
    const glyphs = types.map(t => {
        const label = kn.knNodeLabel(t, 'Title');
        const m = label.match(/^\[(.+?)\] Title$/);
        assert.ok(m, `label "${label}" must be of the form "[glyph] title"`);
        return m[1];
    });
    assert.equal(new Set(glyphs).size, glyphs.length,
        'every type must render a distinct glyph — two types sharing one glyph means the type cue fell back to hue-only');
});

test('knGraphModel: node output carries the glyph-prefixed label, not the bare title (wiring)', () => {
    const g = { nodes: [{ id: 'a', title: 'Agent A', type: 'Agent' }], edges: [] };
    const model = kn.knGraphModel(g, cssVar);
    assert.equal(model.nodes[0].label, kn.knNodeLabel('Agent', 'Agent A'),
        'knGraphModel must run titles through knNodeLabel, not pass the raw title through as the render label');
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
    assert.equal(kn.knNodeColor('Agent', cssVar), '#009E73');
    assert.equal(kn.knNodeColor('Project', cssVar), '#CC79A7');
});

// --- Wiring: knShowGraph must actually feed knRenderSigma the knGraphModel ---
// output, not the raw /api/knowledge/graph payload. The tests above only prove
// knGraphModel itself is correct in isolation; they say nothing about whether
// the live call site (knShowGraph) runs it before handing nodes to Sigma. A
// regression there (e.g. `const model = { nodes, edges }` instead of
// `knGraphModel(g, knCssVar)`) would still leave every test above green, so
// this test drives knShowGraph end-to-end through a fake window.chelaGraphLibs
// (no real WebGL needed — graphology/Sigma are just constructors we replace)
// and asserts the nodes handed to graph.addNode carry knGraphModel's output:
// seeded x/y and a resolved color. Raw API nodes have neither.
class _FakeGraph {
    constructor() { this.nodes = []; this.edges = []; }
    addNode(id, attrs) { this.nodes.push({ id, ...attrs }); }
    addEdge(source, target, attrs) { this.edges.push({ source, target, ...attrs }); }
}
class _FakeSigma {
    on() {}
    kill() {}
}

test('knShowGraph wiring: Sigma receives knGraphModel\'s seeded/colored nodes, not raw API nodes', async () => {
    let builtGraph = null;
    const origLibs = globalThis.window.chelaGraphLibs;
    const origFetch = globalThis.fetch;
    globalThis.window.chelaGraphLibs = {
        Graph: class extends _FakeGraph { constructor() { super(); builtGraph = this; } },
        Sigma: _FakeSigma,
        forceAtlas2: { assign: () => {} },
    };
    const apiGraph = {
        nodes: [
            { id: 'a', title: 'A', type: 'Agent' },
            { id: 'b', title: 'B', type: 'Project' },
        ],
        edges: [{ source: 'a', target: 'b' }],
    };
    globalThis.fetch = async () => ({ json: async () => apiGraph });
    const container = globalThis.document.createElement('div');
    container.id = 'kn-content';
    globalThis.document.body.appendChild(container);

    try {
        await globalThis.window.chela.knShowGraph();
    } finally {
        globalThis.window.chelaGraphLibs = origLibs;
        globalThis.fetch = origFetch;
        container.remove();
    }

    assert.ok(builtGraph, 'knRenderSigma must construct a graph via window.chelaGraphLibs.Graph');
    assert.equal(builtGraph.nodes.length, 2);
    for (const n of builtGraph.nodes) {
        assert.equal(typeof n.x, 'number', `node ${n.id} must carry knGraphModel's seeded x, not raw API data`);
        assert.equal(typeof n.y, 'number', `node ${n.id} must carry knGraphModel's seeded y, not raw API data`);
        assert.equal(typeof n.color, 'string', `node ${n.id} must carry knGraphModel's resolved color, not raw API data`);
    }
    const a = builtGraph.nodes.find(n => n.id === 'a');
    const b = builtGraph.nodes.find(n => n.id === 'b');
    assert.equal(a.color, '#009E73', 'agent -> Okabe-Ito bluish green, per knGraphModel type resolution');
    assert.equal(b.color, '#CC79A7', 'project -> Okabe-Ito reddish purple, per knGraphModel type resolution');
});

// --- FAIL-LOUD: knShowGraph must never leave a frozen spinner or a blank ---
// canvas when something breaks (API failure, or the vendor renderer bundle
// failing to load) — both read as "still working"/"empty" when the truth is
// "this broke". The old behaviour let an unhandled rejection freeze the
// "Building graph…" spinner forever, and let a missing window.chelaGraphLibs
// leave an empty <div id="kn-graph-canvas"> with no explanation.
test('knShowGraph FAIL-LOUD: a graph-API failure shows a visible error, not a frozen spinner', async () => {
    const origFetch = globalThis.fetch;
    globalThis.fetch = async () => { throw new Error('network down'); };
    const container = globalThis.document.createElement('div');
    container.id = 'kn-content';
    globalThis.document.body.appendChild(container);

    try {
        await globalThis.window.chela.knShowGraph();
        assert.match(container.innerHTML, /kn-graph-error/,
            'a failed graph fetch must render a visible error state');
        assert.doesNotMatch(container.innerHTML, /Building graph/,
            'the loading spinner must not be left frozen on screen');
    } finally {
        globalThis.fetch = origFetch;
        container.remove();
    }
});

test('knShowGraph FAIL-LOUD: a missing graph renderer shows a visible error, not a blank canvas', async () => {
    const origLibs = globalThis.window.chelaGraphLibs;
    const origFetch = globalThis.fetch;
    globalThis.window.chelaGraphLibs = undefined; // vendor bundle failed to load
    globalThis.fetch = async () => ({
        json: async () => ({ nodes: [{ id: 'a', title: 'A', type: 'Agent' }], edges: [] }),
    });
    const container = globalThis.document.createElement('div');
    container.id = 'kn-content';
    globalThis.document.body.appendChild(container);

    try {
        await globalThis.window.chela.knShowGraph();
        assert.match(container.innerHTML, /kn-graph-error/,
            'a null renderer (missing chelaGraphLibs) must render a visible error state');
        assert.doesNotMatch(container.innerHTML, /id="kn-graph-canvas"><\/div>/,
            'must not leave an empty, unexplained canvas div on screen');
    } finally {
        globalThis.window.chelaGraphLibs = origLibs;
        globalThis.fetch = origFetch;
        container.remove();
    }
});
