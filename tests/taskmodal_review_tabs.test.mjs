// CMX-122 — the task modal's layout under the REAL style.css: the phone stacks the details
// above the tabs with ONE scroll area, the desktop keeps the sidebar sticky, and a report's
// code scrolls sideways. Opens the REAL task modal island (features/taskmodal, CMX-228) in
// the REAL dashboard DOM (bootDashboardDom) and reads the computed styles back. Its tabs,
// rounds and header are tested in features/taskmodal/tests.
//
// Run: pnpm exec vitest run tests/taskmodal_review_tabs.test.mjs (tests/test_js_suites.py runs every
// .test.mjs inside pytest, by discovery).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, DESKTOP, PHONE } from './js_helpers/css_viewport.mjs';

const STYLE_CSS = fs.readFileSync(new URL('../chela/dashboard/static/style.css', import.meta.url), 'utf8');

let taskmodal;

beforeAll(async () => {
    await bootDashboardDom({ body: '<div id="task-modal-island"></div>' });
    taskmodal = await import('../chela/dashboard/frontend/src/features/taskmodal/index.ts');
});

const history = (n) => JSON.stringify(Array.from({ length: n }, (_, i) => ({
    round: i + 1, at: `2026-10-1${i % 10}T10:00:00+00:00`, verdict: 'changes_requested', body: `report **${i + 1}**`,
})));
let seq = 0;
async function open(fields) {
    taskmodal.openTaskModal({ task_id: `CMX-T${++seq}`, title: 't', ...fields });
    await flush();
}
const q = (sel) => document.querySelector(`#task-modal-content ${sel}`);
const qa = (sel) => [...document.querySelectorAll(`#task-modal-content ${sel}`)];

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

test('phone: the details column stacks above the tabs and only .task-modal-body scrolls', async () => {
    await open({ status: 'awaiting_review', review_history: history(3), brief: '```\ndiff\n```' });
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

test('desktop: the sidebar is sticky; a report\'s code blocks scroll sideways instead of wrapping', async () => {
    await open({ status: 'awaiting_review', review_history: JSON.stringify([{ round: 1, body: '```\n- a very long diff line\n```' }]) });
    for (const vp of [DESKTOP, PHONE]) {
        const code = computed(vp, '.task-modal-timeline-body .kn-code')['kn-code'];
        assert.equal(code.whiteSpace, 'pre');
        assert.equal(code.overflowX, 'auto');
    }
    assert.equal(computed(DESKTOP, '.task-modal-side')['task-modal-side'].position, 'sticky');
    assert.equal(computed(DESKTOP, '.task-modal-rounds')['task-modal-rounds'].display, 'block');
});
