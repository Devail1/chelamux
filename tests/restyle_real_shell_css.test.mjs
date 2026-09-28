// CMX-377 round 5 — the restyle's cascade-resolved CSS guards, mounted on the
// REAL DOM a browser renders, against the stylesheet a browser at 1440×900
// actually APPLIES (defeat_shapes #377d).
//
// Round 4's three cascade guards (tests/dashboard_shell_grid.test.mjs,
// tests/wallnav.test.mjs 11b-3, tests/dashboard_scale_nav_a11y.test.mjs's
// full-space Wall) each mounted a hand-written minimal fixture against the raw
// style.css, and the judge defeated all three with the suite green:
//   1. `@supports (display: grid) { .app > .canvas { grid-row: 2; } }` —
//      jsdom's getComputedStyle skips every rule nested in an at-rule, so an
//      always-true wrapper hides any override from it.
//   2. `.gs-head .term-status-dot[data-wid].waiting { …circle… }` — the real
//      pane-header dot carries data-wid (terminals.js paneHead); the fixture's
//      bare `<span>` did not, so the fork matched only the real header.
//   3. `#term-stage > .grid-stack:has(.gs-head) { padding… }` — every real
//      pane has a .gs-head; the fixture's panes were empty.
// Each is the same gap: the guard's input (DOM + stylesheet) was not the
// browser's input. So this file boots the REAL index.html shell (sliceTemplate),
// renders REAL panes through the real renderTerminals → paneHead path and REAL
// sidebar rows through renderSidebarAgents, colours them through the real
// status tick, and resolves styles against cssForViewport(style.css) — every
// @supports/@media block evaluated for the viewport and unwrapped, exactly as a
// browser would apply it.
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { bootDashboardDom, sliceTemplate, flush } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, DESKTOP } from './js_helpers/css_viewport.mjs';

const CSS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static', 'style.css'), 'utf8');

// One agent per status state — the sidebar and the pane header each derive
// their state through their OWN real code from these same fields.
const AGENTS = [
    { name: 'a-working', window_id: '@1', online: true, session_status: 'busy', cwd: '/p/x' },
    { name: 'b-waiting', window_id: '@2', online: true, session_status: 'waiting', needs_human: true, cwd: '/p/x' },
    { name: 'c-idle', window_id: '@3', online: true, session_status: 'idle', cwd: '/p/x' },
    { name: 'd-done', window_id: '@4', online: true, session_status: 'idle', done: true, pr: { url: 'https://example.invalid/pr/1' }, cwd: '/p/x' },
];
const STATE_OF = { '@1': 'working', '@2': 'waiting', '@3': 'idle', '@4': 'done' };

function fakeFetch(url) {
    const path = String(url);
    const body = path.endsWith('/api/agents') ? AGENTS
        : path.endsWith('/api/agents/context') ? {}
            : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
                : path.startsWith('/api/term/ready') ? { ready: true }
                    : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {}, removeWidget(el) { el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let win;
before(async () => {
    globalThis.GridStack = fakeGridStack();
    // The REAL shell, byte-identical to what Flask serves (terminals enabled).
    const body = sliceTemplate('<div class="app">', '\n  </main>') + '</div>';
    const { dom, modules } = await bootDashboardDom({
        body, fetchImpl: fakeFetch, canvasStub: true,
        extraModules: ['util.js', 'terminals.js', 'nav.js'],
    });
    win = dom.window;
    win.document.elementFromPoint = () => null;
    const { util, terminals, nav } = modules;
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();   // real paneHead() markup, real status tick
    nav.renderSidebarAgents(AGENTS);     // real sidebar rows
    await flush();
    const style = win.document.createElement('style');
    style.textContent = cssForViewport(CSS, DESKTOP);
    win.document.head.appendChild(style);
});

test('real shell @1440: .canvas RESOLVES to grid-row 1 / grid-column 2 — at-rule-wrapped overrides included', () => {
    const canvas = win.document.getElementById('canvas');
    assert.ok(canvas.closest('.app') && win.document.querySelector('.app > .sidebar'),
        'sanity: the real .app > .sidebar + .canvas shell did not mount');
    const cs = win.getComputedStyle(canvas);
    assert.equal(cs.gridRow, '1',
        `.canvas resolves to grid-row "${cs.gridRow}" in a 1440px browser, not "1" — some rule (possibly inside an ` +
        '@supports/@media block) places it on row 2+: the Wall renders off-screen below the sidebar (PR #529 round 2)');
    assert.equal(cs.gridColumn, '2',
        `.canvas resolves to grid-column "${cs.gridColumn}" in a 1440px browser, not "2" — it is not beside the sidebar`);
});

test('real Wall @1440: the POPULATED .grid-stack (real panes, real .gs-head) resolves zero padding and zero margin', () => {
    const grid = win.document.querySelector('#term-stage > .grid-stack');
    assert.ok(grid, 'sanity: the real renderTerminals did not build #term-stage > .grid-stack');
    assert.equal(grid.querySelectorAll('.grid-stack-item .gs-head').length, AGENTS.length,
        'sanity: the Wall under test is not populated with one real pane header per agent');
    const cs = win.getComputedStyle(grid);
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
        assert.equal(cs[`padding${side}`], '0px',
            `the real Wall's .grid-stack padding-${side.toLowerCase()} is ${cs[`padding${side}`]} — no longer full-space`);
        assert.equal(cs[`margin${side}`], '0px',
            `the real Wall's .grid-stack margin-${side.toLowerCase()} is ${cs[`margin${side}`]} — no longer full-space`);
    }
});

test('real DOM @1440: each state\'s REAL pane-header dot resolves the SAME clip-path/border-radius as its REAL sidebar row dot', () => {
    for (const [wid, state] of Object.entries(STATE_OF)) {
        const name = AGENTS.find(a => a.window_id === wid).name;
        const head = win.document.querySelector(`.grid-stack-item .gs-head .term-status-dot[data-wid="${wid}"]`);
        const row = win.document.querySelector(`#sidebar-agents .agent-row[data-agent="${name}"] > .term-status-dot`);
        assert.ok(head, `sanity: no real pane-header dot for ${wid}`);
        assert.ok(row, `sanity: no real sidebar row dot for ${name}`);
        assert.ok(head.classList.contains(state), `sanity: the real status tick did not put "${state}" on ${wid}'s header dot (${head.className})`);
        assert.ok(row.classList.contains(state), `sanity: the real sidebar did not put "${state}" on ${name}'s row dot (${row.className})`);
        const h = win.getComputedStyle(head);
        const r = win.getComputedStyle(row);
        assert.equal(h.clipPath, r.clipPath,
            `state "${state}": pane header resolves clip-path "${h.clipPath}", sidebar "${r.clipPath}" — the header forked its shape`);
        assert.equal(h.borderRadius, r.borderRadius,
            `state "${state}": pane header resolves border-radius "${h.borderRadius}", sidebar "${r.borderRadius}" — the header forked its shape`);
    }
});
