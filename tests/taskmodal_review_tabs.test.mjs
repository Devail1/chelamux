// CMX-122 — the task modal's Brief | Review (N) tabs, per-round collapsible cards, the
// sidebar round list, the phone layout, and the header's Linear link
// (chela/dashboard/static/js/taskmodal.js + taskmodalmodel.js + style.css). Drives the REAL
// openTaskModal in the REAL dashboard DOM (bootDashboardDom) and reads the DOM back.
//
// Run: node --test tests/taskmodal_review_tabs.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { bootDashboardDom, clickOnclick } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, DESKTOP, PHONE } from './js_helpers/css_viewport.mjs';

const BODY = '<div id="modal-task" class="modal-overlay"><div class="modal modal-task"><div id="task-modal-content"></div></div></div>';
const STYLE_CSS = fs.readFileSync(new URL('../chela/dashboard/static/style.css', import.meta.url), 'utf8');

let taskmodal, tm;
let scrolled = [];

before(async () => {
    ({ modules: { taskmodal } } = await bootDashboardDom({ body: BODY, extraModules: ['taskmodal.js'] }));
    tm = await import('../chela/dashboard/static/js/taskmodalmodel.js');
    // jsdom has no layout, so no scrollIntoView — record which element was asked to scroll.
    window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this); };
});

let seq = 0;
const history = (n) => JSON.stringify(Array.from({ length: n }, (_, i) => ({
    round: i + 1, at: `2026-10-1${i % 10}T10:00:00+00:00`, verdict: 'changes_requested', body: `report **${i + 1}**`,
})));
// A fresh task id per call, so the per-task remembered tab never leaks between tests.
const open = (fields) => taskmodal.openTaskModal({ task_id: `CMX-T${++seq}`, title: 't', ...fields });
const q = (sel) => document.querySelector(`#task-modal-content ${sel}`);
const qa = (sel) => [...document.querySelectorAll(`#task-modal-content ${sel}`)];
const selected = () => q('[role="tab"][aria-selected="true"]').dataset.tab;

for (const [fields, rounds, want] of [
    [{ status: 'awaiting_review' }, 2, 'review'],
    [{ status: 'needs_human' }, 1, 'review'],
    [{ status: 'running', judge_state: 'blocked' }, 1, 'review'],
    [{ status: 'changes_requested', judge_state: 'blocked_race' }, 1, 'review'],
    [{ status: 'running' }, 2, 'brief'],
    [{ status: 'done', judge_state: 'clean' }, 2, 'brief'],
    [{ status: 'awaiting_review' }, 0, 'brief'],
]) {
    test(`default tab: ${JSON.stringify(fields)} with ${rounds} round(s) → ${want}`, () => {
        open({ ...fields, review_history: history(rounds) });
        assert.equal(selected(), want);
        assert.equal(q(`#tm-panel-${want}`).hidden, false);
        assert.equal(q(`#tm-panel-${want === 'review' ? 'brief' : 'review'}`).hidden, true);
        assert.equal(tm.defaultTab({ ...fields }, rounds), want);
    });
}

test('the last tab choice is remembered per task, and the opening default is not', () => {
    const a = { task_id: 'CMX-MEM-A', title: 'a', status: 'awaiting_review', review_history: history(2) };
    taskmodal.openTaskModal({ ...a, status: 'running' });   // default brief — must NOT stick
    taskmodal.openTaskModal(a);
    assert.equal(selected(), 'review', 'the default the modal opened on was remembered as a choice');
    clickOnclick(q('#tm-tab-brief'));
    taskmodal.openTaskModal(a);
    assert.equal(selected(), 'brief', 'the viewer\'s choice was not remembered');
    taskmodal.openTaskModal({ ...a, task_id: 'CMX-MEM-B' });
    assert.equal(selected(), 'review', 'one task\'s choice leaked into another');
});

test('N rounds → N cards, newest first, only the newest open, full markdown inside', () => {
    open({ status: 'awaiting_review', review_history: history(13) });
    const cards = qa('#tm-panel-review details.task-modal-round');
    assert.equal(cards.length, 13);
    assert.deepEqual(cards.map(c => c.dataset.ri), [...Array(13).keys()].reverse().map(String));
    assert.deepEqual(cards.map(c => c.open), [true, ...Array(12).fill(false)]);
    assert.match(cards[0].querySelector('summary').textContent, /Round 13\s+changes requested/);
    assert.equal(cards[0].querySelector('.task-modal-timeline-body').innerHTML, '<p>report <strong>13</strong></p>');
    assert.equal(q('#tm-tab-review').textContent, 'Review (13)');
});

test('a round summary carries the visible and held-out survivor counts', () => {
    const body = 'verdict\n\n### 🙈 2 held-out guard(s) also survived — strengthen the guards';
    open({ status: 'awaiting_review', review_history: JSON.stringify([
        { round: 1, verdict: 'changes_requested', body, mutations: [{}, {}, {}] },
        { round: 2, verdict: 'retry', body: 'keep going' },
    ]) });
    const [newest, oldest] = qa('.task-modal-round summary').map(s => s.textContent);
    assert.match(oldest, /3 survived · 2 held-out/);
    assert.doesNotMatch(newest, /survived/);
    // The held-out count is read from its own heading line only.
    assert.equal(tm.heldOutCount(body), 2);
    for (const neg of ['🙈 2 held-out guard(s)', '#### 🙈 2 held-out guard', 'x ### 🙈 2 held-out guard', null]) {
        assert.equal(tm.heldOutCount(neg), 0, JSON.stringify(neg));
    }
});

test('a round-list click switches to Review, opens that round and scrolls to it', () => {
    open({ status: 'running', review_history: history(4) });
    assert.equal(selected(), 'brief');
    const links = qa('.task-modal-side .task-modal-roundlist .task-modal-roundlink');
    assert.deepEqual(links.map(l => l.textContent.trim().split(/\s+/).slice(0, 2).join(' ')),
        ['Round 4', 'Round 3', 'Round 2', 'Round 1']);
    scrolled = [];
    clickOnclick(links[3]);
    const card = q('#tm-panel-review .task-modal-round[data-ri="0"]');
    assert.equal(selected(), 'review');
    assert.equal(card.open, true);
    assert.deepEqual(scrolled, [card]);
});

test('a11y: tablist / tab / tabpanel wired by id, arrows + Home/End move and focus, rounds are <details>', () => {
    open({ status: 'running', review_history: history(2) });
    const list = q('[role="tablist"]');
    const tabs = qa('[role="tablist"] [role="tab"]');
    assert.ok(list);
    assert.equal(tabs.length, 2);
    for (const t of tabs) {
        const panel = q(`#${t.getAttribute('aria-controls')}`);
        assert.equal(panel.getAttribute('role'), 'tabpanel');
        assert.equal(panel.getAttribute('aria-labelledby'), t.id);
    }
    assert.deepEqual(tabs.map(t => t.tabIndex), [0, -1]);
    // The REAL onkeydown attribute on the tablist, fired with the focused tab as the target.
    const key = (k) => new Function('chela', 'event', list.getAttribute('onkeydown'))
        .call(list, window.chela, { key: k, target: tabs.find(t => t.tabIndex === 0), preventDefault() {} });
    for (const [k, want] of [['ArrowRight', 'review'], ['ArrowRight', 'brief'], ['ArrowLeft', 'review'],
        ['Home', 'brief'], ['End', 'review'], ['Enter', 'review']]) {
        key(k);
        assert.equal(selected(), want, k);
        assert.equal(document.activeElement, q(`#tm-tab-${want}`), `${k} did not move focus`);
    }
    assert.ok(qa('.task-modal-round > summary:first-child').length === 2);
});

function computed(viewport, sel) {
    const style = document.createElement('style');
    style.textContent = cssForViewport(STYLE_CSS, viewport);
    document.head.appendChild(style);
    try {
        return Object.fromEntries(qa(sel).map(el => [el.className || el.tagName, window.getComputedStyle(el)]));
    } finally {
        style.remove();
    }
}

test('phone: the details column stacks above the tabs and only .task-modal-body scrolls', () => {
    open({ status: 'awaiting_review', review_history: history(3), brief: '```\ndiff\n```' });
    const style = document.createElement('style');
    style.textContent = cssForViewport(STYLE_CSS, PHONE);
    document.head.appendChild(style);
    try {
        const side = window.getComputedStyle(q('.task-modal-side'));
        assert.equal(side.order, '-1');
        assert.equal(side.position, 'static');
        assert.equal(window.getComputedStyle(q('.task-modal-rounds')).display, 'none');
        assert.equal(window.getComputedStyle(q('.task-modal-body')).overflowY, 'auto');
        const scrollers = qa('.task-modal-body *').filter(el => /auto|scroll/.test(window.getComputedStyle(el).overflowY));
        assert.deepEqual(scrollers, [], 'a nested vertical scroll area inside the modal body');
    } finally {
        style.remove();
    }
});

test('desktop: the sidebar is sticky; a report\'s code blocks scroll sideways instead of wrapping', () => {
    open({ status: 'awaiting_review', review_history: JSON.stringify([{ round: 1, body: '```\n- a very long diff line\n```' }]) });
    for (const vp of [DESKTOP, PHONE]) {
        const code = computed(vp, '.task-modal-timeline-body .kn-code')['kn-code'];
        assert.equal(code.whiteSpace, 'pre');
        assert.equal(code.overflowX, 'auto');
    }
    assert.equal(computed(DESKTOP, '.task-modal-side')['task-modal-side'].position, 'sticky');
    assert.equal(computed(DESKTOP, '.task-modal-rounds')['task-modal-rounds'].display, 'block');
});

test('Linear task: the header key links to the payload URL in a new tab, with the external-link icon', () => {
    const url = 'https://linear.app/some-team/issue/CMX-88/a-slug';
    taskmodal.openTaskModal({ task_id: 'CMX-88', title: 't', status: 'running', tracker_url: url });
    const a = q('.task-modal-headline a.kanban-tracker-link');
    assert.ok(a, 'no link');
    assert.equal(a.getAttribute('href'), url);
    assert.equal(a.getAttribute('target'), '_blank');
    assert.equal(a.getAttribute('rel'), 'noopener noreferrer');
    assert.equal(a.textContent.trim(), 'CMX-88');
    assert.ok(a.querySelector('svg path[d="M10 14 21 3"]'), 'no Lucide external-link icon');
});

for (const [why, tracker_url] of [['TODO task (no tracker URL)', undefined], ['non-https URL', 'javascript:alert(1)']]) {
    test(`${why}: the header key is plain text, never a link`, () => {
        taskmodal.openTaskModal({ task_id: 'TODO-7', title: 't', status: 'running', tracker_url });
        assert.equal(q('.task-modal-headline a'), null);
        assert.equal(q('.task-modal-headline .kanban-card-id').textContent, 'TODO-7');
    });
}

test('a backlog item (no id at all) renders no key chip, not an empty one', () => {
    taskmodal.openTaskModal({ title: 'someday', section: 'Ideas', status: 'backlog' });
    assert.equal(q('.task-modal-headline .kanban-card-id'), null);
});

test('a round with no stored number is labelled by its position', () => {
    open({ status: 'awaiting_review', review_history: JSON.stringify([{ verdict: 'reopened' }, { verdict: 'retry' }]) });
    assert.deepEqual(qa('.task-modal-round-no').map(e => e.textContent), ['Round 2', 'Round 1']);
});

test('tab / round handlers are no-ops (never throw) once the modal content is gone or the round is unknown', () => {
    open({ status: 'awaiting_review', review_history: history(1) });
    assert.doesNotThrow(() => window.chela.taskModalRound(99));
    document.querySelector('#task-modal-content').innerHTML = '';
    assert.doesNotThrow(() => window.chela.taskModalTab('review'));
});
