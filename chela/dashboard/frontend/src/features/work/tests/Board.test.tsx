// The Work board, rendered (CMX-233; ported from tests/kanban_flatten, kanban_hidden_columns,
// kanban_tracker_render, kanban_tracker_link, kanban_open_tracker_state, kanban_task_modal_wiring,
// task_handoff, task_progress_chip and risk_chip .test.mjs, which drove legacy kanban.js).
import { act, fireEvent, render, screen } from '@testing-library/react';
import STYLE_CSS from '../../../../../static/style.css?raw';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { setWorkPoll, type Run, type WorkPayload, type Workflow } from '../../../shared/store/work.ts';
import { TaskModal, closeTaskModal } from '../../taskmodal/index.ts';
import { Board, moveCard, toasts } from '../index.ts';
import { must, stubFetch, viewport } from './setup.ts';

const LINEAR = [
  { name: 'Backlog', type: 'backlog', hidden: false },
  { name: 'Todo', type: 'unstarted', hidden: false },
  { name: 'In Progress', type: 'started', hidden: false },
  { name: 'In Review', type: 'started', hidden: false },
  { name: 'Done', type: 'completed', hidden: false },
  { name: 'Canceled', type: 'canceled', hidden: true },
];
const LANES = ['backlog', 'todo', 'in_progress', 'review', 'done', 'archived'];
const polls = vi.fn(() => Promise.resolve());

beforeEach(() => {
  polls.mockClear();
  setWorkPoll(polls);
});
afterEach(() => {
  act(() => closeTaskModal());
  act(() => toasts.set([]));
});

const run = (o: Partial<Run> & Record<string, unknown> = {}): Run => ({ task_id: 'CMX-1', title: 'a task', status: 'running', attempt: 1, started_at: '2026-10-07T00:00:00Z', ...o });
const wf = (o: Partial<Workflow> = {}): Workflow => ({ path: '/x/WORKFLOW.md', project_key: 'CMX', open_tasks: [], backlog_items: [], parked_tasks: [], active_runs: [], awaiting_review_runs: [], recent_runs: [], ...o });
const payload = (...wfs: Partial<Workflow>[]): WorkPayload => ({ configured: true, workflows: wfs.map(wf) });

/** Pin every column open (CMX-136 hides empty ones), so a test about WHICH columns exist sees all. */
function pinAll(keys: string[]) {
  localStorage.setItem('chela_kanban_colprefs', JSON.stringify(Object.fromEntries(keys.map((k) => [k, 'show']))));
}

let view: ReturnType<typeof render> | null = null;
function show(data: WorkPayload | null) {
  if (view) view.rerender(<><Board live={data} active /><TaskModal /></>);
  else view = render(<><Board live={data} active /><TaskModal /></>);
}
afterEach(() => {
  view = null;
});

const board = () => must(document.getElementById('kanban-board'), '#kanban-board');
const colLabels = () => [...board().querySelectorAll('.kanban-col-label')].map((e) => e.textContent);
const col = (label: string) => must([...board().querySelectorAll('.kanban-col')].find((c) => c.querySelector('.kanban-col-label')?.textContent === label), `column ${label}`);
const cardOf = (id: string) => must(document.querySelector<HTMLElement>(`.kanban-card[data-task-id="${id}"]`), `card ${id}`);
const chips = (el: Element) => [...el.querySelectorAll('.kanban-state-chip')].map((c) => c.textContent?.trim());
const panel = () => document.getElementById('kanban-hidden');
const rowLabels = () => [...(panel()?.querySelectorAll('.kanban-hidden-label') ?? [])].map((e) => e.textContent);
const row = (label: string) => must([...(panel()?.querySelectorAll<HTMLElement>('.kanban-hidden-row') ?? [])].find((r) => r.textContent?.includes(label)), `hidden row ${label}`);
const posts = <T extends { method?: string }>(calls: T[]) => calls.filter((c) => c.method === 'POST');

// --- lanes, cards and their chips ----------------------------------------------------------

test('a closed-not-merged run renders in Archived with a worded pill; a done run in Done; both heads say their lane', () => {
  pinAll(LANES);
  show(payload({ recent_runs: [run({ task_id: 'c1', status: 'closed' }), run({ task_id: 'd1', status: 'done', pr_state: 'merged' })] }));
  expect(colLabels()).toEqual(['Backlog', 'To Do', 'In Progress', 'Review', 'Done', 'Archived']);
  const closed = must(col('Archived').querySelector('.kanban-card-closed'));
  expect(closed.querySelector('.kanban-state-chip')?.textContent?.trim()).toBe('closed, not merged');
  expect(closed.querySelector('.kanban-state-chip svg')).not.toBeNull();
  expect(col('Done').querySelectorAll('.kanban-card')).toHaveLength(1);
  expect(col('Done').querySelector('.kanban-card-done')).not.toBeNull();
  expect(col('Archived').querySelectorAll('.kanban-card')).toHaveLength(1);
});

test('a card title renders its inline markdown, escaped', () => {
  show(payload({ recent_runs: [run({ status: 'done', title: 'ship **the wall** <img src=x> now' })] }));
  expect(cardOf('CMX-1').querySelector('.kanban-card-title span')?.innerHTML).toBe('ship <strong>the wall</strong> &lt;img src=x&gt; now');
});

test('a parked bullet is its own card in Backlog: reason (escaped, full in its tooltip), lock icon, workflow chip; no Promote, no ×', () => {
  const reason = 'stop" <b>evil</b><img src=x onerror=alert(1)>';
  show(payload({ parked_tasks: [{ id: 'p1', title: 'parked one', file: '/x/TODO.md', line_number: 3, raw: '- [ ] x', reason }, { id: 'p2', title: 'parked two' }] }));
  const [one, two] = [...col('Backlog').querySelectorAll<HTMLElement>('.kanban-card-parked')];
  expect(col('Backlog').querySelector('.kanban-card-backlog')).toBeNull();
  const chip = must(one?.querySelector('.kanban-parked-reason'));
  expect(chip.textContent?.trim()).toBe(reason);
  expect(chip.getAttribute('title')).toBe(reason);
  expect(chip.querySelector('b, img')).toBeNull();
  expect(chip.querySelector('svg')).not.toBeNull();
  expect(one?.querySelector('.kanban-wf-chip')?.textContent).toBe('x');
  expect(two?.querySelector('.kanban-parked-reason')?.textContent?.trim()).toBe('parked');
  expect(two?.querySelector('.kanban-parked-reason')?.getAttribute('title')).toBeNull();
  for (const c of [one, two]) expect(c?.querySelector('.kanban-promote-btn, .kanban-delete-btn')).toBeNull();
});

test('in Backlog a BACKLOG.md card renders before a parked one, even from a later workflow', () => {
  show(payload({ path: '/a/WORKFLOW.md', parked_tasks: [{ id: 'p1', title: 'parked' }] }, { path: '/b/WORKFLOW.md', backlog_items: [{ text: 'idea', section: 'Later', file: '/b/BACKLOG.md' }] }));
  const kids = [...must(col('Backlog').querySelector('.kanban-cards')).children].map((c) => c.className);
  expect(kids).toEqual(['kanban-card kanban-card-backlog', 'kanban-card kanban-card-parked']);
  expect(col('Backlog').querySelector('.kanban-section-chip')?.textContent).toBe('Later');
});

test('a hand-closed run shows "Closed: <reason>" as a note, never its stale error; a failed run keeps its error', () => {
  show(payload({ recent_runs: [run({ task_id: 'c', status: 'closed', close_reason: 'superseded by cmx-403', last_error: 'tmux window disappeared' }), run({ task_id: 'f', status: 'failed', last_error: 'x'.repeat(130) })] }));
  const closed = cardOf('c');
  expect(closed.querySelector('.kanban-card-closed-note')?.textContent).toBe('Closed: superseded by cmx-403');
  expect(closed.querySelector('.kanban-card-error')).toBeNull();
  expect(closed.textContent).not.toContain('tmux window disappeared');
  const err = must(cardOf('f').querySelector('.kanban-card-error'));
  expect(err.textContent).toBe('x'.repeat(120));
  expect(err.getAttribute('title')).toBe('x'.repeat(130));
});

test('run state rides on the card: the status pill, judging, rework N, blocked race; a quiet run only its pill', () => {
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ task_id: 'CMX-2', judge_state: 'running', rework_count: 2 }), run({ task_id: 'CMX-4', judge_state: 'clean', rework_count: 0 })],
    awaiting_review_runs: [run({ task_id: 'CMX-3', status: 'awaiting_review', judge_state: 'blocked_race', pr_url: 'https://github.com/o/r/pull/3' })] }));
  expect(chips(cardOf('CMX-2'))).toEqual(['running', 'judging', 'rework 2']);
  expect(chips(cardOf('CMX-3'))).toEqual(['awaiting review', 'blocked race']);
  expect(chips(cardOf('CMX-4'))).toEqual(['running']);
  expect(cardOf('CMX-2').querySelectorAll('.kanban-state-chip svg')).toHaveLength(3);
});

test('a died run is a "failed" pill INSIDE its Linear column, never a column of its own', () => {
  pinAll(LINEAR.map((c) => `tr-${c.name.toLowerCase().replace(' ', '-')}`));
  show(payload({ tracker_columns: LINEAR, recent_runs: [run({ status: 'failed', tracker_state: 'In Review', pr_url: 'https://github.com/o/r/pull/9' })] }));
  expect(colLabels()).toEqual(['Backlog', 'Todo', 'In Progress', 'In Review', 'Done']);
  expect(col('In Review').querySelector('.kanban-state-chip.st-failed')?.textContent?.trim()).toBe('failed');
  expect(col('In Progress').querySelectorAll('.kanban-card')).toHaveLength(0);
});

test('the CI chip, the PR link and the merge control: a button only on a green or CI-less PR, a badge once merged', () => {
  const pr = (n: number) => `https://github.com/o/r/pull/${n}`;
  show(payload({ awaiting_review_runs: [
    run({ task_id: 'g', status: 'awaiting_review', pr_url: pr(1), pr_checks: 'passing' }),
    run({ task_id: 'n', status: 'awaiting_review', pr_url: pr(2), pr_checks: 'none' }),
    run({ task_id: 'r', status: 'awaiting_review', pr_url: pr(3), pr_checks: 'failing' }),
    run({ task_id: 'u', status: 'awaiting_review', pr_url: pr(4) }),
    run({ task_id: 'm', status: 'awaiting_review', pr_url: pr(5), pr_checks: 'failing', pr_state: 'merged' }),
  ], active_runs: [run({ task_id: 'q' })] }));
  const ci = (id: string) => cardOf(id).querySelector('.kanban-ci-chip');
  expect([ci('g'), ci('n'), ci('r'), ci('u')].map((c) => [c?.className, c?.textContent?.trim()])).toEqual([
    ['kanban-ci-chip ci-passing', 'ci green'], ['kanban-ci-chip ci-none', 'no ci'], ['kanban-ci-chip ci-failing', 'CI RED'], ['kanban-ci-chip ci-unknown', 'ci unknown'],
  ]);
  expect(ci('q')).toBeNull();
  const merges = [...board().querySelectorAll('.kanban-merge-btn')].map((b) => b.closest<HTMLElement>('.kanban-card')?.dataset.taskId);
  expect(merges).toEqual(['g', 'n']);
  expect(cardOf('m').querySelector('.kanban-merged-badge')?.textContent?.trim()).toBe('Merged');
  const link = must(cardOf('g').querySelector<HTMLAnchorElement>('.kanban-pr-wrap a.pr-badge'));
  expect([link.getAttribute('href'), link.textContent, link.target]).toEqual([pr(1), '#1', '_blank']);
  // CMX-207: no batch merge anywhere outside the cards.
  expect(document.getElementById('kanban-filters')?.textContent).not.toMatch(/merge/i);
});

test('the id chip: a branch only when it differs from the display id; file:line for a branchless row', () => {
  show(payload({ active_runs: [run({ task_id: 'h1', project_key: 'CMX', task_number: 7, branch_name: 'cmx-7' }), run({ task_id: 'h2', branch_name: 'dogfood/abc' })],
    open_tasks: [{ id: 'o1', title: 'open', file: '/r/TODO.md', line_number: 4 }] }));
  expect(cardOf('h1').querySelector('.kanban-card-id')?.textContent).toBe('CMX-7');
  expect(cardOf('h1').querySelector('.ts')).toBeNull();
  expect(cardOf('h2').querySelector('.ts')?.textContent).toBe('dogfood/abc');
  expect(cardOf('o1').querySelector('.ts')?.textContent).toBe('TODO.md:4');
});

test('handoff note: one line, escaped, cut at 160 with the full note as its tooltip; none for null or blank', () => {
  const long = `${'n'.repeat(170)} <b>x</b>`;
  show(payload({ awaiting_review_runs: [run({ task_id: 'h', status: 'awaiting_review', handoff: `changed X\n\n  look at Y <img src=x>` }), run({ task_id: 'l', status: 'awaiting_review', handoff: long }),
    run({ task_id: 'z', status: 'awaiting_review', handoff: null }), run({ task_id: 'b', status: 'awaiting_review', handoff: ' \n ' })] }));
  const note = must(cardOf('h').querySelector('.kanban-card-handoff'));
  expect(note.textContent?.trim()).toBe('changed X look at Y <img src=x>');
  expect(note.querySelector('img')).toBeNull();
  expect(cardOf('l').querySelector('.kanban-card-handoff')?.textContent?.trim()).toBe(`${'n'.repeat(160)}…`);
  expect(cardOf('l').querySelector('.kanban-card-handoff')?.getAttribute('title')).toBe(long);
  for (const id of ['z', 'b']) expect(cardOf(id).querySelector('.kanban-card-handoff')).toBeNull();
});

test('task progress and risk chips, with their tooltips; nothing without data', () => {
  const tasks = { total: 4, done: 3, in_progress: { subject: 'wire it' }, blocked: [{ subject: 'ship', blocked_by: ['t-1'] }] };
  show(payload({ active_runs: [run({ task_id: 'p', tasks, risk: 'low', risk_reason: 'marker' }), run({ task_id: 'q', tasks: null, risk: 'high' }), run({ task_id: 'r', risk: 'extreme' })] }));
  const chip = must(cardOf('p').querySelector('.task-progress-chip'));
  expect(chip.textContent?.trim()).toBe('3/4');
  expect(chip.getAttribute('title')).toBe('3 of 4 tasks done\nIn progress: wire it\n"ship" blocked by t-1');
  expect(cardOf('q').querySelector('.task-progress-chip')).toBeNull();
  const risk = must(cardOf('p').querySelector('.kanban-risk-chip'));
  expect([risk.textContent, risk.className, risk.getAttribute('title')]).toEqual(['risk: low', 'kanban-risk-chip risk-low', 'marker']);
  expect(cardOf('q').querySelector('.kanban-risk-chip')?.getAttribute('title')).toBe('risk: high');
  expect(cardOf('r').querySelector('.kanban-risk-chip')).toBeNull();
});

// --- tracker links -----------------------------------------------------------------------

const url = (n: number) => `https://linear.app/acme/issue/CMX-${n}/slug-${n}`;

test('a Linear card links its identifier to EXACTLY the adapter\'s url, in a new tab; a markdown or non-https one gets no link', () => {
  show(payload({ open_tasks: [{ id: 'CMX-11', title: 'queued', tracker_url: url(11) }], active_runs: [run({ task_id: 'CMX-12', tracker_url: url(12) }), run({ task_id: 'CMX-20', tracker_url: 'javascript:alert(1)' }), run({ task_id: 'md-run' })] }));
  for (const n of [11, 12]) {
    const links = cardOf(`CMX-${n}`).querySelectorAll<HTMLAnchorElement>('a.kanban-tracker-link');
    expect(links).toHaveLength(1);
    expect([links[0]?.getAttribute('href'), links[0]?.textContent?.trim(), links[0]?.target, links[0]?.rel]).toEqual([url(n), `CMX-${n}`, '_blank', 'noopener noreferrer']);
    expect(links[0]?.getAttribute('title')).toBe(`Open CMX-${n} in Linear`);
  }
  for (const id of ['CMX-20', 'md-run']) {
    expect(cardOf(id).querySelectorAll('a')).toHaveLength(0);
    expect(cardOf(id).querySelector('span.kanban-card-id')).not.toBeNull();
  }
});

// --- Linear columns -----------------------------------------------------------------------

test('with tracker_columns the columns ARE Linear\'s visible states; without, chela\'s lanes; the grid is sized to what is drawn', () => {
  pinAll([...LINEAR.map((c) => `tr-${c.name.toLowerCase().replace(' ', '-')}`), ...LANES]);
  show(payload({ tracker_columns: LINEAR }));
  expect(colLabels()).toEqual(['Backlog', 'Todo', 'In Progress', 'In Review', 'Done']);
  expect(board().style.getPropertyValue('--kanban-cols')).toBe('5');
  show(payload({}));
  expect(colLabels()).toEqual(['Backlog', 'To Do', 'In Progress', 'Review', 'Done', 'Archived']);
  expect(board().style.getPropertyValue('--kanban-cols')).toBe('6');
});

test('open Linear tasks land by their state (any casing), fall back to Todo, and an unknown state with no Todo is Unmapped', () => {
  const t = (id: string, state: string | null) => ({ id, title: `title ${id}`, tracker_state: state });
  show(payload({ tracker_columns: LINEAR, open_tasks: [t('CMX-1', 'backlog'), t('CMX-2', 'Todo'), t('CMX-3', null)] }));
  const titles = (label: string) => [...col(label).querySelectorAll('.kanban-card-title')].map((e) => e.textContent);
  expect(titles('Backlog')).toEqual(['title CMX-1']);
  expect(titles('Todo')).toEqual(['title CMX-2', 'title CMX-3']);
  show(payload({ tracker_columns: LINEAR.filter((c) => c.type !== 'unstarted'), open_tasks: [t('CMX-9', 'Triage')] }));
  expect(titles('Unmapped')).toEqual(['title CMX-9']);
});

test('a tracker state name is text in the column header, never markup', () => {
  pinAll(['tr-todo', 'tr-b-qa-b']);
  show(payload({ tracker_columns: [{ name: 'Todo', type: 'unstarted' }, { name: '<b>QA</b>', type: 'started' }] }));
  expect(colLabels()).toEqual(['Todo', '<b>QA</b>']);
  expect(board().querySelector('.kanban-col-label b')).toBeNull();
});

test('CMX-193: Done says "latest" only when the server capped its runs, on both boards', () => {
  for (const cols of [LINEAR, undefined]) {
    show(payload({ tracker_columns: cols, recent_runs_capped: true, recent_runs: [run({ status: 'done', tracker_state: 'Done' })] }));
    expect(colLabels()).toContain('Done · latest');
    show(payload({ tracker_columns: cols, recent_runs_capped: false, recent_runs: [run({ status: 'done', tracker_state: 'Done' })] }));
    expect(colLabels()).toContain('Done');
    expect(colLabels()).not.toContain('Done · latest');
  }
});

// --- the project filter (CMX-194: a multi-select) -------------------------------------------

test('the project pills are a multi-select: each toggles, the union shows, All clears; persisted; a vanished project is dropped', () => {
  const data = payload({ path: '/a/WORKFLOW.md', active_runs: [run({ task_id: 'A-1' })] }, { path: '/b/WORKFLOW.md', active_runs: [run({ task_id: 'B-1' })] }, { path: '/c/WORKFLOW.md', active_runs: [run({ task_id: 'C-1' })] });
  show(data);
  const pill = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) });
  const ids = () => [...board().querySelectorAll<HTMLElement>('.kanban-card')].map((c) => c.dataset.taskId).sort();
  expect(pill('All').getAttribute('aria-pressed')).toBe('true');
  expect(pill('All').textContent).toBe('All (3)');
  expect(ids()).toEqual(['A-1', 'B-1', 'C-1']);
  fireEvent.click(pill('a'));
  fireEvent.click(pill('c'));
  expect(ids()).toEqual(['A-1', 'C-1']);
  expect([pill('All'), pill('a'), pill('b'), pill('c')].map((p) => p.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false', 'true']);
  expect(pill('a').className).toBe('kanban-filter-chip active');
  expect(JSON.parse(localStorage.getItem('chela_kanban_projects') ?? '')).toEqual(['/a/WORKFLOW.md', '/c/WORKFLOW.md']);
  fireEvent.click(pill('a'));
  expect(ids()).toEqual(['C-1']);
  // The selected project leaves the payload: the board shows everything, not nothing.
  show(payload({ path: '/a/WORKFLOW.md', active_runs: [run({ task_id: 'A-1' })] }));
  expect(ids()).toEqual(['A-1']);
  expect(pill('All').getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(pill('All'));
  expect(localStorage.getItem('chela_kanban_projects')).toBe('[]');
});

test('the filter picks the board: a Linear project gets its columns, a markdown one its lanes; each only its own cards', () => {
  const data = payload({ tracker_columns: LINEAR, active_runs: [run({ task_id: 'CMX-1', tracker_state: 'In Progress' })] }, { path: '/y/WORKFLOW.md', active_runs: [run({ task_id: 'MD-1' })] });
  localStorage.setItem('chela_kanban_projects', '["/y/WORKFLOW.md"]');
  show(data);
  expect(colLabels()).toEqual(['In Progress']);
  expect(col('In Progress').querySelector<HTMLElement>('.kanban-card')?.dataset.taskId).toBe('MD-1');
  expect(rowLabels()).toEqual(['Backlog', 'To Do', 'Review', 'Done', 'Archived']);
});

test('a saved selection is read back on mount', () => {
  localStorage.setItem('chela_kanban_projects', '["/b/WORKFLOW.md"]');
  show(payload({ path: '/a/WORKFLOW.md', active_runs: [run({ task_id: 'A-1' })] }, { path: '/b/WORKFLOW.md', active_runs: [run({ task_id: 'B-1' })] }));
  expect([...board().querySelectorAll<HTMLElement>('.kanban-card')].map((c) => c.dataset.taskId)).toEqual(['B-1']);
});

// --- before data, unconfigured, empty columns ------------------------------------------------

test('nothing before the first poll; the explainer when unconfigured or no workflow; never a hidden panel then', () => {
  show(null);
  expect(document.getElementById('kanban-empty')).toBeNull();
  expect(board().children).toHaveLength(0);
  for (const data of [{ configured: false }, { configured: true, workflows: [] }]) {
    show(data);
    expect(document.querySelector('#kanban-empty .work-empty-title')?.textContent).toBe('A board for work in flight');
    expect(document.getElementById('kanban-board')).toBeNull();
    expect(panel()).toBeNull();
    expect(document.getElementById('kanban-filters')?.children).toHaveLength(0);
  }
});

test('an empty column says so in words (no dash glyph)', () => {
  pinAll(['todo']);
  show(payload({ active_runs: [run()] }));
  expect(col('To Do').querySelector('.kanban-empty-col')?.textContent).toBe('Nothing here');
  expect(document.getElementById('work-board')?.textContent).not.toMatch(/[—]/);
});

// --- Hidden columns (CMX-136) -------------------------------------------------------------------

test('an empty column is in the panel, not on the board; the phone one-liner and the nav strip agree', () => {
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'In Progress' })] }));
  expect(colLabels()).toEqual(['In Progress']);
  expect(rowLabels()).toEqual(['Backlog', 'Todo', 'In Review', 'Done']);
  expect([...must(panel()).querySelectorAll('.col-count')].map((c) => c.textContent)).toEqual(['0', '0', '0', '0']);
  expect(board().style.getPropertyValue('--kanban-cols')).toBe('1');
  expect(panel()?.querySelector('.kanban-hidden-inline')?.textContent).toBe('Hidden: Backlog · Todo · In Review · Done');
  expect([...document.querySelectorAll('.kanban-nav-label')].map((e) => e.textContent)).toEqual(['In Progress']);
  // A count change moves a column between the panel and the board; nothing hidden, no panel.
  show(payload({ tracker_columns: LINEAR, active_runs: LINEAR.slice(0, 5).map((c, i) => run({ task_id: `CMX-${i}`, tracker_state: c.name })) }));
  expect(colLabels()).toEqual(['Backlog', 'Todo', 'In Progress', 'In Review', 'Done']);
  expect(panel()).toBeNull();
});

test('clicking (or Enter on) a hidden row shows it as a column and persists; Hide puts it back', () => {
  show(payload({ tracker_columns: LINEAR }));
  fireEvent.click(row('In Review'));
  expect(JSON.parse(localStorage.getItem('chela_kanban_colprefs') ?? '')).toEqual({ 'tr-in-review': 'show' });
  expect(colLabels()).toEqual(['In Review']);
  expect(rowLabels()).not.toContain('In Review');
  fireEvent.keyDown(row('Todo'), { key: 'Enter' });
  expect(colLabels()).toEqual(['Todo', 'In Review']);
  fireEvent.keyDown(row('Done'), { key: 'x' });
  expect(colLabels()).toEqual(['Todo', 'In Review']);
  fireEvent.click(must(col('In Review').querySelector('.kanban-col-hide')));
  expect(JSON.parse(localStorage.getItem('chela_kanban_colprefs') ?? '')).toEqual({ 'tr-todo': 'show' });
  expect(colLabels()).toEqual(['Todo']);
  // A pinned column with cards offers no Hide (it would not hide).
  show(payload({ tracker_columns: LINEAR, open_tasks: [{ id: 'T-1', tracker_state: 'Todo' }] }));
  expect(col('Todo').querySelector('.kanban-col-hide')).toBeNull();
});

test('a pin is read back on mount; storage that throws is the default, and a click still works for the page', () => {
  localStorage.setItem('chela_kanban_colprefs', '{"tr-todo":"show"}');
  show(payload({ tracker_columns: LINEAR }));
  expect(colLabels()).toEqual(['Todo']);
  view?.unmount();
  view = null;
  const deny = () => {
    throw new DOMException('denied', 'SecurityError');
  };
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(deny);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(deny);
  show(payload({ tracker_columns: LINEAR, recent_runs: [run({ status: 'done', tracker_state: 'Done' })] }));
  expect(colLabels()).toEqual(['Done']);
  fireEvent.click(row('Todo'));
  expect(colLabels()).toEqual(['Todo', 'Done']);
});

test('the hidden panel\'s caret survives a repaint', () => {
  show(payload({ tracker_columns: LINEAR }));
  const details = must(panel()?.querySelector('details'));
  expect(details.open).toBe(true);
  details.open = false;
  fireEvent(details, new Event('toggle'));
  show(payload({ tracker_columns: LINEAR }));
  expect(panel()?.querySelector('details')?.open).toBe(false);
});

// --- drag to a hidden column -----------------------------------------------------------------

function drag(el: Element, type: string, dataTransfer?: object) {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  if (dataTransfer) Object.defineProperty(ev, 'dataTransfer', { value: dataTransfer });
  act(() => {
    el.dispatchEvent(ev);
  });
  return ev;
}

test('a hidden row is a drop target only mid-drag; a drop announces the move and says plainly nothing moved', () => {
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ task_id: 'CMX-7', tracker_state: 'In Progress' })] }));
  const card = cardOf('CMX-7');
  expect(card.draggable).toBe(true);
  expect(drag(row('In Review'), 'dragover').defaultPrevented).toBe(false);
  const set: string[][] = [];
  drag(card, 'dragstart', { setData: (t: string, v: string) => set.push([t, v]) });
  expect(set).toEqual([['text/plain', 'CMX-7']]);
  expect(drag(row('In Review'), 'dragover').defaultPrevented).toBe(true);
  expect(row('In Review').classList.contains('kanban-hidden-row-over')).toBe(true);
  drag(row('In Review'), 'dragleave');
  expect(row('In Review').classList.contains('kanban-hidden-row-over')).toBe(false);
  const seen: unknown[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  document.addEventListener('chela:kanban-move', on);
  drag(row('In Review'), 'dragover');
  const ev = drag(row('In Review'), 'drop');
  document.removeEventListener('chela:kanban-move', on);
  expect(ev.defaultPrevented).toBe(true);
  expect(row('In Review').classList.contains('kanban-hidden-row-over')).toBe(false);
  expect(seen).toEqual([{ card: expect.objectContaining({ task_id: 'CMX-7' }), to: 'tr-in-review', label: 'In Review' }]);
  expect(toasts.get().map((t) => t.title)).toEqual(['Moving cards between columns is not wired to the tracker yet: CMX-7 was not moved to In Review.']);
  drag(card, 'dragend');
  expect(drag(row('In Review'), 'drop').defaultPrevented).toBe(true);
  expect(toasts.get()).toHaveLength(1);
});

test('the layout freezes mid-drag; the poll that landed paints on dragend', () => {
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'In Progress' })] }));
  drag(cardOf('CMX-1'), 'dragstart');
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'Todo' })] }));
  expect(colLabels()).toEqual(['In Progress']);
  drag(cardOf('CMX-1'), 'dragend');
  expect(colLabels()).toEqual(['Todo']);
  show(payload({ tracker_columns: LINEAR, active_runs: [run({ tracker_state: 'Done' })] }));
  expect(colLabels()).toEqual(['Done']);
});

test('moveCard names the card by id, else title, else "this card"', () => {
  moveCard({ status: 'backlog', title: 'an idea' }, { key: 'k', label: 'L', cards: [] });
  moveCard({ status: 'backlog' }, { key: 'k', label: 'L', cards: [] });
  expect(toasts.get().map((t) => t.title.split(': ')[1])).toEqual(['an idea was not moved to L.', 'this card was not moved to L.']);
});

// --- the card click opens the task modal --------------------------------------------------------

const modalTitle = () => document.querySelector('[role="dialog"].modal-task .task-modal-title')?.textContent ?? null;

test('clicking a card opens the task modal with THAT card, for every card kind', () => {
  show(payload({ backlog_items: [{ text: 'idea one' }, { text: 'idea two' }], parked_tasks: [{ id: 'p1', title: 'parked one', raw: '- [ ] parked one <!-- blocked: x -->' }],
    active_runs: [run({ task_id: 'r1', title: 'first run' }), run({ task_id: 'r2', title: 'second run' }), run({ task_id: 'r3', title: 'third run' })] }));
  const cards = [...board().querySelectorAll<HTMLElement>('.kanban-card')];
  for (const [i, want] of [[1, 'idea two'], [0, 'idea one'], [2, 'parked one'], [4, 'second run'], [5, 'third run'], [3, 'first run']] as const) {
    fireEvent.click(must(cards[i]));
    expect(modalTitle()).toContain(want);
    act(() => closeTaskModal());
  }
});

test('a click on a card\'s link, PR, action or confirm strip never also opens the modal', () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  vi.spyOn(window, 'prompt').mockReturnValue(null);
  show(payload({ backlog_items: [{ text: 'idea', file: '/x/BACKLOG.md' }], awaiting_review_runs: [run({ task_id: 'CMX-12', status: 'awaiting_review', tracker_url: url(12), pr_url: 'https://github.com/o/r/pull/1', pr_checks: 'passing' })] }));
  const c = cardOf('CMX-12');
  for (const sel of ['.kanban-tracker-link', '.kanban-pr-wrap', '.kanban-merge-btn', '.kanban-requeue-btn', '.kanban-delete-btn', '.kanban-confirm-msg', '.kanban-confirm .btn-confirm + button']) {
    fireEvent.click(must(c.querySelector(sel), sel));
    expect(modalTitle()).toBeNull();
  }
  fireEvent.click(must(document.querySelector('.kanban-promote-btn')));
  expect(modalTitle()).toBeNull();
  fireEvent.click(c);
  expect(modalTitle()).toContain('a task');
});

// --- the card's actions ---------------------------------------------------------------------

test('delete: × opens an inline confirm (hiding itself); Cancel restores it; Delete posts the run and the board refetches', async () => {
  const calls = stubFetch();
  show(payload({ active_runs: [run({ task_id: 'CMX-5' })] }));
  const x = must(cardOf('CMX-5').querySelector<HTMLElement>('.kanban-delete-btn'));
  expect(x.getAttribute('aria-label')).toBe('Delete this card');
  fireEvent.click(x);
  expect(cardOf('CMX-5').querySelector('.kanban-confirm-msg')?.textContent).toBe('Delete this?');
  expect(x.style.visibility).toBe('hidden');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(cardOf('CMX-5').querySelector('.kanban-confirm')).toBeNull();
  expect(x.style.visibility).toBe('');
  fireEvent.click(x);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Delete' })));
  expect(posts(calls)).toEqual([{ url: '/api/dispatcher/delete', method: 'POST', body: { kind: 'run', task_id: 'CMX-5' } }]);
  expect(polls).toHaveBeenCalledTimes(1);
  expect(cardOf('CMX-5').querySelector('.kanban-confirm')).toBeNull();
});

test('delete: a source line posts its file and text; a failure shows the server\'s words until Close', async () => {
  let resolve: (v: { status: number; body: unknown }) => void = () => {};
  const calls = stubFetch(() => new Promise((r) => (resolve = r)) as never);
  show(payload({ backlog_items: [{ text: 'idea', file: '/x/BACKLOG.md' }], open_tasks: [{ id: 'O-1', title: 'open', file: '/x/TODO.md' }, { id: 'L-1', title: 'linear', file: '' }] }));
  expect(cardOf('L-1').querySelector('.kanban-delete-btn')).toBeNull();
  const card = must(document.querySelector<HTMLElement>('.kanban-card-backlog'));
  fireEvent.click(must(card.querySelector('.kanban-delete-btn')));
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
  expect(card.querySelector('.kanban-confirm-msg')?.textContent).toBe('Deleting…');
  await act(async () => resolve({ status: 409, body: { ok: false, error: 'line not found' } }));
  expect(posts(calls)[0]?.body).toEqual({ kind: 'source-line', file: '/x/BACKLOG.md', text: 'idea' });
  expect(card.querySelector('.kanban-confirm-msg')?.textContent).toBe('line not found');
  expect(card.querySelector<HTMLElement>('.kanban-delete-btn')?.style.visibility).toBe('hidden');
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(card.querySelector('.kanban-confirm')).toBeNull();
  expect(polls).not.toHaveBeenCalled();
});

test('merge: asks first; posts the run\'s merge; shows Merging… while it runs; refetches on success', async () => {
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(false);
  let resolve: (v: { status: number; body: unknown }) => void = () => {};
  const calls = stubFetch(() => new Promise((r) => (resolve = r)) as never);
  show(payload({ awaiting_review_runs: [run({ task_id: 'CMX 9', status: 'awaiting_review', pr_url: 'https://github.com/o/r/pull/12', pr_checks: 'passing' })] }));
  const btn = must(document.querySelector<HTMLButtonElement>('.kanban-merge-btn'));
  fireEvent.click(btn);
  expect(ask).toHaveBeenCalledWith('Squash-merge PR #12 and delete branch?');
  expect(posts(calls)).toEqual([]);
  ask.mockReturnValue(true);
  fireEvent.click(btn);
  expect([btn.disabled, btn.textContent]).toEqual([true, 'Merging…']);
  await act(async () => resolve({ status: 200, body: { ok: true } }));
  expect(posts(calls)).toEqual([{ url: '/api/dispatcher/runs/CMX%209/merge', method: 'POST', body: undefined }]);
  expect([btn.disabled, btn.textContent]).toEqual([false, 'Merge']);
  expect(polls).toHaveBeenCalledTimes(1);
});

test('merge: a refusal, an HTTP error with no body and a network failure each surface on the card and re-enable the button', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  try {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    let reply: () => { status?: number; body?: unknown } | Promise<never> = () => ({ status: 409, body: { ok: false, error: 'checks are red' } });
    stubFetch(() => reply());
    show(payload({ awaiting_review_runs: [run({ status: 'awaiting_review', pr_url: 'https://g/o/r/x', pr_checks: 'none' })] }));
    const btn = must(document.querySelector<HTMLButtonElement>('.kanban-merge-btn'));
    const toast = () => cardOf('CMX-1').querySelector('.kanban-merge-toast')?.textContent ?? null;
    await act(async () => fireEvent.click(btn));
    expect(toast()).toBe('checks are red');
    expect(btn.disabled).toBe(false);
    reply = () => ({ status: 502, body: undefined });
    await act(async () => fireEvent.click(btn));
    expect(toast()).toBe('HTTP 502');
    reply = () => Promise.reject(new TypeError('offline'));
    await act(async () => fireEvent.click(btn));
    expect(toast()).toBe('TypeError: offline');
    expect(polls).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(14999));
    expect(toast()).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(toast()).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test('a PR url with no number is "this PR" in the merge question', () => {
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(false);
  show(payload({ awaiting_review_runs: [run({ status: 'awaiting_review', pr_url: 'https://example.test/review', pr_checks: 'passing' })] }));
  fireEvent.click(must(document.querySelector('.kanban-merge-btn')));
  expect(ask).toHaveBeenCalledWith('Squash-merge this PR and delete branch?');
});

const STALL = 'closed run blocks this task: requeue or refile';

test('requeue: a stalled open task, its stalling closed run and a running card each REQUEUE with the reason asked for', async () => {
  const ask = vi.spyOn(window, 'prompt').mockReturnValue('  brief changed  ');
  const calls = stubFetch();
  show(payload({ open_tasks: [{ id: 'CMX-33', title: 'naming', closed_run_stall: STALL }, { id: 'CMX-35', title: 'fine' }],
    recent_runs: [run({ task_id: 'CMX-33', status: 'closed', pr_state: 'closed', closed_run_stall: STALL }), run({ task_id: 'CMX-34', status: 'closed', pr_state: 'closed' })],
    active_runs: [run({ task_id: 'CMX-40' })] }));
  const [openCard, closedCard] = [...document.querySelectorAll<HTMLElement>('.kanban-card[data-task-id="CMX-33"]')];
  for (const c of [openCard, closedCard]) {
    expect(c?.querySelector('.kanban-card-stall')?.textContent?.trim()).toBe(STALL);
    expect(c?.querySelector('.kanban-card-stall svg')).not.toBeNull();
    expect(c?.querySelector('.kanban-requeue-btn')?.textContent).toBe('Requeue');
  }
  for (const id of ['CMX-34', 'CMX-35']) expect(cardOf(id).querySelector('.kanban-card-stall, .kanban-requeue-btn')).toBeNull();
  expect(cardOf('CMX-40').querySelector('.kanban-requeue-btn')?.textContent).toBe('Close & requeue');
  for (const btn of [openCard?.querySelector('.kanban-requeue-btn'), closedCard?.querySelector('.kanban-requeue-btn'), cardOf('CMX-40').querySelector('.kanban-requeue-btn')]) {
    await act(async () => fireEvent.click(must(btn)));
  }
  expect(ask).toHaveBeenCalledWith('Requeue CMX-33: why? (recorded in its review history; the old PR is closed and the next tick claims a fresh attempt on a new branch)');
  expect(posts(calls)).toEqual(['CMX-33', 'CMX-33', 'CMX-40'].map((id) => ({ url: `/api/dispatcher/runs/${id}/close`, method: 'POST', body: { reason: 'brief changed', requeue: true } })));
  // A cancelled or blank reason posts nothing.
  for (const answer of [null, '   ']) {
    ask.mockReturnValue(answer);
    await act(async () => fireEvent.click(must(cardOf('CMX-40').querySelector('.kanban-requeue-btn'))));
  }
  expect(posts(calls)).toHaveLength(3);
});

test('requeue: a failure surfaces on the card', async () => {
  vi.spyOn(window, 'prompt').mockReturnValue('why');
  stubFetch(() => ({ status: 400, body: { ok: false, error: 'not closable' } }));
  show(payload({ active_runs: [run()] }));
  await act(async () => fireEvent.click(must(document.querySelector('.kanban-requeue-btn'))));
  expect(cardOf('CMX-1').querySelector('.kanban-merge-toast')?.textContent).toBe('not closable');
});

test('promote: a backlog card with a workflow asks, posts it, and surfaces a refusal on the card', async () => {
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const calls = stubFetch(() => ({ status: 409, body: { ok: false, error: 'not on the base branch' } }));
  const long = 'x'.repeat(90);
  show(payload({ backlog_items: [{ text: long }] }));
  const btn = must(document.querySelector('.kanban-promote-btn'));
  await act(async () => fireEvent.click(btn));
  expect(ask).toHaveBeenCalledWith(`Promote backlog item to TODO?\n\n"${'x'.repeat(77)}..."\n\nThis commits to the base branch and pushes.`);
  expect(posts(calls)).toEqual([{ url: '/api/dispatcher/backlog/promote', method: 'POST', body: { workflow_path: '/x/WORKFLOW.md', text: long } }]);
  expect(document.querySelector('.kanban-card-backlog .kanban-promote-toast')?.textContent).toBe('not on the base branch');
  ask.mockReturnValue(false);
  await act(async () => fireEvent.click(btn));
  expect(posts(calls)).toHaveLength(1);
  show(payload({ backlog_items: [{ text: '' }] }));
  expect(document.querySelector('.kanban-promote-btn')).toBeNull();
});

// --- the phone layout ---------------------------------------------------------------------

test('Swipe ⇄ Rows: a class on #work-board, the pressed button, persisted and read back', () => {
  show(payload({ active_runs: [run()] }));
  const pane = must(document.getElementById('work-board'));
  const btn = (name: string) => screen.getByRole('button', { name });
  expect(pane.className).toBe('work-pane active kanban-mobile-swipe');
  expect(btn('Swipe').getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(btn('Rows'));
  expect(pane.className).toBe('work-pane active kanban-mobile-rows');
  expect([btn('Swipe').getAttribute('aria-pressed'), btn('Rows').getAttribute('aria-pressed'), btn('Rows').className]).toEqual(['false', 'true', 'kanban-layout-btn active']);
  expect(localStorage.getItem('chela_kanban_mlayout')).toBe('rows');
  view?.unmount();
  view = null;
  show(payload({ active_runs: [run()] }));
  expect(document.getElementById('work-board')?.className).toBe('work-pane active kanban-mobile-rows');
  fireEvent.click(btn('Swipe'));
  expect(localStorage.getItem('chela_kanban_mlayout')).toBe('swipe');
});

test('an inactive Board pane is not .active', () => {
  render(<Board live={payload({})} active={false} />);
  expect(document.getElementById('work-board')?.className).toBe('work-pane kanban-mobile-swipe');
});

test('Rows accordion: on a phone a head (click or key) folds its column and persists; Backlog/Done/Archived start folded; wider, a head does nothing', () => {
  pinAll(LANES);
  viewport.phone = true;
  show(payload({ active_runs: [run()] }));
  const folded = () => [...board().querySelectorAll<HTMLElement>('.kanban-col')].filter((c) => c.classList.contains('kanban-col-collapsed')).map((c) => c.dataset.col);
  const head = (label: string) => must(col(label).querySelector<HTMLElement>('.kanban-col-head'));
  expect(folded()).toEqual(['backlog', 'done', 'archived']);
  expect(head('Backlog').getAttribute('aria-expanded')).toBe('false');
  fireEvent.click(head('Backlog'));
  fireEvent.keyDown(head('Review'), { key: ' ' });
  expect(folded()).toEqual(['review', 'done', 'archived']);
  expect(head('Review').getAttribute('aria-expanded')).toBe('false');
  expect(JSON.parse(localStorage.getItem('chela_kanban_collapsed') ?? '')).toEqual(['done', 'archived', 'review']);
  view?.unmount();
  view = null;
  viewport.phone = false;
  show(payload({ active_runs: [run()] }));
  expect(folded()).toEqual(['review', 'done', 'archived']);
  fireEvent.click(head('Review'));
  expect(folded()).toEqual(['review', 'done', 'archived']);
});

test('a junk collapsed set is the default', () => {
  pinAll(LANES);
  localStorage.setItem('chela_kanban_collapsed', '{bad');
  show(payload({ active_runs: [run()] }));
  expect([...board().querySelectorAll('.kanban-col-collapsed')].map((c) => (c as HTMLElement).dataset.col)).toEqual(['backlog', 'done', 'archived']);
});

test('the quick-nav strip: one chip per drawn column with its count; a tap scrolls to that column and marks it', () => {
  pinAll(LANES);
  const scrolled: [string | undefined, unknown][] = [];
  Element.prototype.scrollIntoView = function (this: HTMLElement, opts?: unknown) {
    scrolled.push([this.dataset.col, opts]);
  } as typeof Element.prototype.scrollIntoView;
  show(payload({ active_runs: [run()] }));
  const navChips = [...document.querySelectorAll<HTMLElement>('#kanban-nav-strip .kanban-nav-chip')];
  expect(navChips.map((c) => [c.querySelector('.kanban-nav-label')?.textContent, c.querySelector('.kanban-nav-count')?.textContent])).toEqual([
    ['Backlog', '0'], ['To Do', '0'], ['In Progress', '1'], ['Review', '0'], ['Done', '0'], ['Archived', '0'],
  ]);
  fireEvent.click(must(navChips[3]));
  expect(scrolled).toEqual([['review', { behavior: 'smooth', inline: 'start', block: 'nearest' }]]);
  const on = [...document.querySelectorAll<HTMLElement>('.kanban-nav-chip')].map((c) => c.getAttribute('aria-current'));
  expect(on).toEqual(['false', 'false', 'false', 'true', 'false', 'false']);
  expect(document.querySelector('.kanban-nav-chip.active')?.textContent).toBe('Review0');
});

// --- Liav 2026-10-19: a column is a lane, not a share of the window -----------------------------

test('a column carries a max width, and the board scrolls sideways rather than squeezing (style.css)', () => {
  const sheet = document.createElement('style');
  sheet.textContent = STYLE_CSS;
  document.head.append(sheet);
  try {
    show(payload({ active_runs: [run()] }));
    expect(getComputedStyle(col('In Progress')).maxWidth).toBe('400px');
    expect(getComputedStyle(board()).overflowX).toBe('auto');
    expect(getComputedStyle(board()).gridTemplateColumns).toBe('repeat(var(--kanban-cols, 6), minmax(240px, 400px))');
  } finally {
    sheet.remove();
  }
});

test('a parked card and its reason chip are on screen under style.css, a long reason ellipsized (CMX-298)', () => {
  const sheet = document.createElement('style');
  sheet.textContent = STYLE_CSS;
  document.head.append(sheet);
  try {
    show(payload({ parked_tasks: [{ id: 'p1', title: 'parked', reason: 'a fairly long blocked reason that would need to ellipsize past the card edge' }] }));
    const card = must(document.querySelector('.kanban-card-parked'));
    const chip = must(card.querySelector('.kanban-parked-reason'));
    const [c, r] = [getComputedStyle(card), getComputedStyle(chip)];
    expect([Number(c.opacity) > 0, c.display, c.visibility]).toEqual([true, 'block', 'visible']);
    expect([parseFloat(r.fontSize) > 0, r.display === 'none', r.visibility]).toEqual([true, false, 'visible']);
    expect([r.overflow, r.textOverflow, r.whiteSpace]).toEqual(['hidden', 'ellipsis', 'nowrap']);
  } finally {
    sheet.remove();
  }
});
