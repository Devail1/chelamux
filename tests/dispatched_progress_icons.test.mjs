// CMX-137: a DISPATCHED run wears Linear-style progress icons (◐ working with the shared
// pulse ring, ◕ judging / awaiting review; the shared check / triangle for done / waiting),
// in Okabe-Ito bluish-green; a human session's dot is unchanged. Real DOM: the real
// renderSidebarAgents and dock, resolved against style.css. Run: pnpm exec vitest run <this file>.
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bootDashboardDom, sliceTemplate, flush, SIDEBAR } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, resolveVars, shapeSignature, DESKTOP, DESKTOP_DARK } from './js_helpers/css_viewport.mjs';

const CSS = readFileSync(new URL('../chela/dashboard/static/style.css', import.meta.url), 'utf8');

const WT = '/srv/u/.chela/worktrees/chelamux';
const card = (tid, role, over = {}) => ({ task_id: tid, title: `Run ${tid}`, status: 'running', judge_state: '', role, ...over });
const human = (name, wid, over = {}) => ({ name, window_id: wid, online: true, claude_running: true, session_status: 'idle', cwd: '/p/x', ...over });
const worker = (tid, wid, run, over = {}) => ({ name: `liavacc/${tid.toLowerCase()}-x`, window_id: wid, online: true,
    claude_running: true, session_status: 'idle', cwd: `${WT}/${tid}`, dispatched: true, run, ...over });

const AGENTS = [
    human('h-working', '@1', { session_status: 'busy' }),
    human('h-waiting', '@2', { session_status: 'waiting', needs_human: true }),
    human('h-idle', '@3'),
    human('h-done', '@4', { done: true }),
    worker('R-WORK', '@11', card('R-WORK', 'agent'), { session_status: 'busy' }),
    worker('R-REWORK', '@12', card('R-REWORK', 'agent', { status: 'changes_requested' }), { session_status: 'busy' }),
    worker('R-JUDGE', '@13', card('R-JUDGE', 'agent', { status: 'awaiting_review', judge_state: 'running' })),
    { ...worker('R-JUDGE', '@14', card('R-JUDGE', 'judge', { status: 'awaiting_review', judge_state: 'running' })),
        name: 'judge-liavacc/r-judge-x' },
    worker('R-REVIEW', '@15', card('R-REVIEW', 'agent', { status: 'awaiting_review', judge_state: 'clean' })),
    worker('R-DONE', '@16', card('R-DONE', 'agent', { status: 'done', judge_state: 'clean' })),
    worker('R-WAIT', '@17', card('R-WAIT', 'agent'), { session_status: 'waiting', needs_human: true }),
];

// run → [the dot's state class, the label's state word]
const RUNS = { 'R-WORK': ['working', 'working'], 'R-REWORK': ['working', 'rework'], 'R-JUDGE': ['judging', 'judging'],
    'R-REVIEW': ['judging', 'awaiting review'], 'R-DONE': ['done', 'done'], 'R-WAIT': ['waiting', 'waiting'] };
const HUMANS = { 'h-working': 'working', 'h-waiting': 'waiting', 'h-idle': 'idle', 'h-done': 'done' };

let fleet = AGENTS;
const BODIES = [['/api/agents', () => fleet], ['/api/agents/context', () => ({})],
    ['/api/rooms', () => ({ rooms: {}, pending: [] })], ['/api/term/ready', () => ({ ready: true })]];
function fakeFetch(url) {
    const hit = BODIES.find(([p]) => String(url).split('?')[0].endsWith(p));
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(hit ? hit[1]() : {}) });
}
const noop = () => {};
const fakeGridStack = () => ({ init: () => ({
    on: noop, off: noop, save: () => [], destroy: noop, removeWidget(el) { el.remove(); }, addWidget: el => el,
    makeWidget: el => el, enableMove: noop, enableResize: noop, update: noop, batchUpdate: noop, commit: noop,
    cellHeight: noop, column: noop, getGridItems: () => [], removeAll: noop, float: noop, engine: { nodes: [] },
}) });

let win, doc, util, terminals, nav, sheet, sidebar;
beforeAll(async () => {
    globalThis.GridStack = fakeGridStack();
    const body = sliceTemplate('<div class="app">', '\n  </main>') + '</div>';
    const { dom, modules } = await bootDashboardDom({ body, fetchImpl: fakeFetch, canvasStub: true,
        extraModules: ['util.js', 'terminals.js', 'nav.js', SIDEBAR] });
    win = dom.window; doc = win.document;
    doc.elementFromPoint = () => null;
    ({ util, terminals, nav, sidebar } = modules);
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
    sidebar.renderSidebarAgents(AGENTS);
    await flush();
    sheet = doc.head.appendChild(doc.createElement('style'));
    sheet.textContent = cssForViewport(CSS, DESKTOP);
});

const runDot = tid => doc.querySelector(`#sidebar-agents .agent-row[data-run="${tid}"] > .term-status-dot`);
const humanDot = name => doc.querySelector(`#sidebar-agents .agent-row[data-agent="${name}"] > .term-status-dot`);
const chipDot = wid => doc.querySelector(`#term-min-dock .min-chip[data-wid="${wid}"] .term-status-dot`);
const classes = el => [...el.classList].sort();
const prop = (el, p) => resolveVars(win, el, win.getComputedStyle(el).getPropertyValue(p)).trim();
// jsdom keeps `animation` / `border-radius` as the shorthand it was given (style.css
// writes them that way), so the animation's NAME is the shorthand's first token.
const animName = el => (prop(el, 'animation').split(/\s+/)[0] || 'none');
const SKY = /#56b4e9|rgb\(86, 180, 233\)/i;   // Okabe-Ito sky blue #56B4E9: a human's working dot
const BLUISH_GREEN = /#009e73|rgb\(0, 158, 115\)/i;   // Okabe-Ito bluish-green #009E73

test('sidebar: a dispatched run in each state wears `dispatched <state>` and is labelled "Dispatched run: <state>"', () => {
    for (const [tid, [cls, word]] of Object.entries(RUNS)) {
        const dot = runDot(tid);
        assert.ok(dot, `no run row dot for ${tid}`);
        assert.deepEqual(classes(dot), ['dispatched', cls, 'term-status-dot'].sort(), `${tid}: ${dot.className}`);
        assert.equal(dot.getAttribute('aria-label'), `Dispatched run: ${word}`, tid);
        assert.equal(dot.getAttribute('title'), `Dispatched run: ${word}`, tid);
        assert.equal(dot.getAttribute('role'), 'img', tid);
    }
});

test('sidebar: a human session keeps its own glyph: no `dispatched`, no run label', () => {
    for (const [name, cls] of Object.entries(HUMANS)) {
        const dot = humanDot(name);
        assert.ok(dot, `no row dot for ${name}`);
        assert.deepEqual(classes(dot), [cls, 'term-status-dot'].sort(), `${name}: ${dot.className}`);
        // CMX-276: a human row's dot names its state too (CMX-121 took the word off the row face)
        assert.equal(dot.getAttribute('aria-label'), `Session: ${cls}`, name);
        assert.equal(dot.getAttribute('title'), `Session: ${cls}`, name);
    }
});

const conicFill = el => {
    const m = /conic-gradient\((.*)\)/.exec(prop(el, 'background-image'));
    return m ? m[1] : null;
};

for (const [scheme, vp] of [['light', DESKTOP], ['dark', DESKTOP_DARK]]) {
    test(`[${scheme}] ◐ working is HALF filled, ◕ judging / awaiting review is ¾ filled: bluish-green, a ring, never the human's solid sky dot`, () => {
        sheet.textContent = cssForViewport(CSS, vp);
        for (const [tid, cls] of [['R-WORK', 'working'], ['R-REWORK', 'working'], ['R-JUDGE', 'judging'], ['R-REVIEW', 'judging']]) {
            const dot = runDot(tid);
            const fill = conicFill(dot);
            assert.ok(fill, `${tid}: no conic fill (background-image "${prop(dot, 'background-image')}")`);
            assert.match(fill, BLUISH_GREEN, `${tid}: the fill "${fill}" is not Okabe-Ito bluish-green`);
            assert.match(fill, cls === 'working' ? /\b0(deg|%)?\s+50%/ : /\b0(deg|%)?\s+75%/, `${tid}: ${fill}`);
            assert.match(prop(dot, 'border-top-color'), BLUISH_GREEN, `${tid}: the ring is not bluish-green`);
            assert.equal(prop(dot, 'border-top-width'), '1.5px', tid);
            assert.equal(prop(dot, 'border-top-style'), 'solid', tid);
            assert.equal(prop(dot, 'clip-path'), 'none', `${tid}: clipped to another silhouette`);
            assert.equal(prop(dot, 'border-radius'), '50%', tid);
            assert.doesNotMatch(prop(dot, 'background-color'), SKY, tid);
        }
        const h = humanDot('h-working');
        assert.match(prop(h, 'background-color'), SKY, "a human's working dot is no longer sky blue");
        assert.equal(prop(h, 'background-image'), 'none', "a human's working dot is no longer solid");
        assert.notDeepEqual(shapeSignature(win, runDot('R-WORK')), shapeSignature(win, h),
            'a dispatched working run resolves the SAME silhouette as a human working session');
        assert.notDeepEqual(shapeSignature(win, runDot('R-WORK')), shapeSignature(win, runDot('R-JUDGE')),
            '◐ and ◕ resolve the same silhouette');
    });

    test(`[${scheme}] the pulse ring is on a WORKING dispatched run and absent in every other state`, () => {
        sheet.textContent = cssForViewport(CSS, vp);
        const ring = dot => ({ anim: animName(dot), shadow: prop(dot, 'box-shadow') });
        const h = ring(humanDot('h-working'));
        assert.equal(h.anim, 'term-status-pulse', 'sanity: the human working pulse moved');
        for (const tid of ['R-WORK', 'R-REWORK']) {
            const r = ring(runDot(tid));
            assert.equal(r.anim, 'term-status-pulse', `${tid}: the working pulse is gone`);
            assert.match(r.shadow, /0(px)? 0(px)? 0(px)? 3px/, `${tid}: the halo ring is gone ("${r.shadow}")`);
            assert.match(r.shadow, /--ok-green|#009e73|0, 158, 115/i, `${tid}: the halo is not tinted to the run's hue ("${r.shadow}")`);
        }
        for (const tid of ['R-JUDGE', 'R-REVIEW', 'R-DONE', 'R-WAIT']) {
            const r = ring(runDot(tid));
            assert.notEqual(r.anim, 'term-status-pulse', `${tid}: carries the working pulse`);
            assert.equal(r.shadow, 'none', `${tid}: carries a halo ring ("${r.shadow}")`);
        }
    });

    test(`[${scheme}] attention looks the same for both kinds: done and waiting resolve the human silhouette`, () => {
        sheet.textContent = cssForViewport(CSS, vp);
        assert.deepEqual(shapeSignature(win, runDot('R-WAIT')), shapeSignature(win, humanDot('h-waiting')));
        assert.equal(prop(runDot('R-WAIT'), 'background-color'), prop(humanDot('h-waiting'), 'background-color'));
        assert.equal(animName(runDot('R-WAIT')), animName(humanDot('h-waiting')));
        assert.deepEqual(shapeSignature(win, runDot('R-DONE')), shapeSignature(win, humanDot('h-done')));
    });
}

test('reduced motion: the dispatched working pulse stops; the ring geometry stays', () => {
    sheet.textContent = cssForViewport(CSS, { ...DESKTOP, reducedMotion: true });
    assert.equal(animName(runDot('R-WORK')), 'none');
    assert.match(prop(runDot('R-WORK'), 'box-shadow'), /3px/);
    sheet.textContent = cssForViewport(CSS, DESKTOP);
    assert.equal(animName(runDot('R-WORK')), 'term-status-pulse');
});

test('same size as the human dot: expanded row and collapsed rail', () => {
    for (const collapsed of [false, true]) {
        doc.body.classList.toggle('sidebar-collapsed', collapsed);
        sheet.textContent = cssForViewport(CSS, DESKTOP);
        for (const tid of Object.keys(RUNS)) {
            for (const p of ['width', 'height', 'margin-top']) {
                assert.equal(prop(runDot(tid), p), prop(humanDot('h-working'), p), `${tid} ${p} (collapsed=${collapsed})`);
            }
        }
        assert.equal(prop(runDot('R-WORK'), 'width'), '10px');
    }
    doc.body.classList.remove('sidebar-collapsed');
});

test('minimized bar: a run window wears its run\'s icon and label; a human chip is unchanged', () => {
    for (const a of AGENTS) terminals.minimizePane(a.window_id);
    const want = { '@11': ['working', 'working'], '@14': ['judging', 'judging'], '@15': ['judging', 'awaiting review'],
        '@16': ['done', 'done'], '@17': ['waiting', 'waiting'] };
    for (const [wid, [cls, word]] of Object.entries(want)) {
        const dot = chipDot(wid);
        assert.ok(dot, `no dock chip for ${wid}`);
        assert.deepEqual(classes(dot), ['dispatched', cls, 'term-status-dot'].sort(), `${wid}: ${dot.className}`);
        assert.equal(dot.getAttribute('aria-label'), `Dispatched run: ${word}`, wid);
        assert.equal(dot.title, `Dispatched run: ${word}`, wid);
        assert.equal(dot.getAttribute('role'), 'img', wid);
    }
    for (const [wid, cls] of [['@1', 'working'], ['@2', 'waiting'], ['@3', 'idle']]) {
        const dot = chipDot(wid);
        assert.deepEqual(classes(dot), [cls, 'term-status-dot'].sort(), `${wid}: ${dot.className}`);
        assert.equal(dot.getAttribute('aria-label'), null, wid);
        assert.equal(dot.getAttribute('role'), null, wid);
    }
});

test('minimized bar: a state change drops the old state: the pulse ring stops when the run leaves working', async () => {
    const swap = (wid, over) => AGENTS.map(a => (a.window_id === wid ? { ...a, ...over } : a));
    fleet = swap('@11', { session_status: 'idle', run: card('R-WORK', 'agent', { status: 'awaiting_review', judge_state: 'running' }) });
    util.setAgentsCache(fleet);
    await terminals.termTick();
    assert.deepEqual(classes(chipDot('@11')), ['dispatched', 'judging', 'term-status-dot']);
    sheet.textContent = cssForViewport(CSS, DESKTOP);
    assert.notEqual(animName(chipDot('@11')), 'term-status-pulse');
    // the window loses its run card (still docked): back to the plain dot, label gone
    fleet = swap('@11', { session_status: 'busy', run: null });
    util.setAgentsCache(fleet);
    await terminals.termTick();
    const dot = chipDot('@11');
    assert.deepEqual(classes(dot), ['term-status-dot', 'working']);
    assert.equal(dot.getAttribute('aria-label'), null);
    assert.equal(dot.getAttribute('role'), null);
    assert.equal(dot.title, 'Working');
    fleet = AGENTS;
});

test('model: windowRunDot is null without a run card; an unknown run status keeps the plain idle ring', async () => {
    const { windowRunDot, runDot, buildItems } = await import('../chela/dashboard/frontend/src/features/sidebar/model.ts');
    assert.equal(windowRunDot(null, false), null);
    assert.equal(windowRunDot({ name: 'h', window_id: '@1', dispatched: true }, false), null);
    assert.equal(windowRunDot({ name: 'h', window_id: '@1', run: { status: 'running' } }, false), null);
    assert.deepEqual(windowRunDot({ name: 'w', window_id: '@2', run: card('T', 'agent') }, true),
        { cls: 'dispatched waiting', label: 'Dispatched run: waiting' });
    assert.deepEqual(windowRunDot({ name: 'w', window_id: '@2', run: card('T', 'agent') }, false),
        { cls: 'dispatched working', label: 'Dispatched run: working' });
    const item = buildItems([{ name: 'w', window_id: '@2', run: card('T', 'agent', { status: 'queued' }) }])[0];
    assert.deepEqual(runDot(item, () => false), { cls: 'dispatched idle', label: 'Dispatched run: queued' });
});
