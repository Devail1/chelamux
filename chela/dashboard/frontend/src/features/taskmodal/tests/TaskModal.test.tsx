// The task-detail modal (ported from tests/taskmodal_render.test.mjs, taskmodal_review_tabs,
// risk_chip and taskmodal_judge_badge, CMX-228). The kanban card click that opens it is
// legacy: tests/kanban_task_modal_wiring.test.mjs drives that on the real page.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { TaskModal, closeTaskModal, openTaskModal } from '../index.ts';
import type { TaskItem } from '../model.ts';
import { must } from './setup.ts';

let scrolled: { el: Element; opts: unknown }[] = [];
beforeEach(() => {
  scrolled = [];
  Element.prototype.scrollIntoView = function (this: Element, opts?: unknown) {
    scrolled.push({ el: this, opts });
  } as typeof Element.prototype.scrollIntoView;
  render(<TaskModal />);
});
afterEach(() => act(() => closeTaskModal()));

let seq = 0;
const history = (n: number) =>
  JSON.stringify(Array.from({ length: n }, (_, i) => ({ round: i + 1, at: `2026-10-1${i % 10}T10:00:00+00:00`, verdict: 'changes_requested', body: `report **${i + 1}**` })));
/** Open on `item`; a fresh task id per call unless given, so a remembered tab never leaks. */
function open(item: TaskItem) {
  act(() => openTaskModal({ task_id: `CMX-T${++seq}`, title: 't', ...item }));
}
const dialog = () => screen.queryByRole('dialog');
const q = (sel: string) => dialog()?.querySelector<HTMLElement>(sel) ?? null;
const qa = (sel: string) => [...(dialog()?.querySelectorAll<HTMLElement>(sel) ?? [])];
const selected = () => screen.getByRole('tab', { selected: true }).textContent;
const tab = (name: RegExp) => screen.getByRole('tab', { name });
const row = (label: string) => qa('.task-modal-row').find((r) => r.querySelector('.task-modal-row-label')?.textContent === label);
const rowValue = (label: string) => row(label)?.querySelector('.task-modal-row-value') ?? null;
function choose(t: HTMLElement) {
  // Radix Tabs selects on mousedown (a click alone does not reach it in jsdom).
  fireEvent.mouseDown(t, { button: 0, ctrlKey: false });
}

test('closed until an item is opened; a null item opens nothing', () => {
  expect(dialog()).toBeNull();
  act(() => openTaskModal(null));
  expect(dialog()).toBeNull();
  open({ title: 'x' });
  expect(must(dialog()).className).toBe('modal modal-task');
});

test('the dialog is named by its title; the visible title renders inline markdown', () => {
  open({ title: 'ship **the wall** now', status: 'open' });
  expect(screen.getByRole('dialog', { name: 'ship **the wall** now' })).toBeTruthy();
  expect(must(q('.task-modal-title > span')).innerHTML).toBe('ship <strong>the wall</strong> now');
});

test('a leading bold span is the display title; a missing title is "(untitled)"', () => {
  open({ title: '**Move the log.** Design is settled.' });
  expect(must(q('.task-modal-title')).textContent).toBe('Move the log.');
  open({ title: '' });
  expect(must(q('.task-modal-title')).textContent).toBe('(untitled)');
  open({ title: '**  **' });
  expect(screen.getByRole('dialog', { name: 'Task' })).toBeTruthy();
});

test('a title longer than 300 characters is cut at 300', () => {
  open({ title: 'x'.repeat(400) });
  expect(must(q('.task-modal-title')).textContent).toHaveLength(300);
});

test('the headline: workflow chip, the key, and the status badge in its severity class', () => {
  open({ task_id: 'abc', project_key: 'CMX', task_number: 7, workflow_path: '/x/chelamux/WORKFLOW.md', status: 'needs_human' });
  expect(must(q('.task-modal-headline .kanban-wf-chip')).textContent).toBe('chelamux');
  expect(must(q('.task-modal-headline .kanban-card-id')).textContent).toBe('CMX-7');
  const badge = must(q('.task-modal-headline .badge'));
  expect(badge.textContent).toBe('needs human');
  expect(badge.className).toBe('badge badge-needs-human');
});

test('Linear task: the key links to the tracker URL in a new tab, with the external-link icon', () => {
  const url = 'https://linear.app/some-team/issue/CMX-88/a-slug';
  open({ task_id: 'CMX-88', status: 'running', tracker_url: url });
  const a = must(q('.task-modal-headline a.kanban-tracker-link'));
  expect(a.getAttribute('href')).toBe(url);
  expect(a.getAttribute('target')).toBe('_blank');
  expect(a.getAttribute('rel')).toBe('noopener noreferrer');
  expect(a.textContent?.trim()).toBe('CMX-88');
  expect(a.querySelector('svg')).toBeTruthy();
});

test('a tracker URL carrying a quote cannot break out of the href attribute', () => {
  const url = 'https://linear.app/t/issue/CMX-9/"onmouseover="alert(1)';
  open({ task_id: 'CMX-9', status: 'running', tracker_url: url });
  const a = must(q('.task-modal-headline a.kanban-tracker-link'));
  expect(a.getAttribute('href')).toBe(url);
  expect(a.getAttribute('onmouseover')).toBeNull();
});

test.each([
  ['TODO task (no tracker URL)', undefined],
  ['non-https URL', 'javascript:alert(1)'],
])('%s: the key is plain text, never a link', (_why, tracker_url) => {
  open({ task_id: 'TODO-7', status: 'running', tracker_url });
  expect(q('.task-modal-headline a')).toBeNull();
  expect(must(q('.task-modal-headline .kanban-card-id')).textContent).toBe('TODO-7');
});

test('a backlog item (no id at all) renders no key chip, and "?" for its status', () => {
  act(() => openTaskModal({ title: 'someday' }));
  expect(q('.task-modal-headline .kanban-card-id')).toBeNull();
  expect(must(q('.task-modal-headline .badge')).textContent).toBe('?');
});

test.each([
  [{ status: 'awaiting_review' }, 2, /^Review/],
  [{ status: 'running', judge_state: 'blocked' }, 1, /^Review/],
  [{ status: 'running' }, 2, /^Brief/],
  [{ status: 'awaiting_review' }, 0, /^Brief/],
])('opens on the default tab: %j with %i round(s)', (fields, n, want) => {
  open({ ...fields, review_history: history(n) });
  expect(selected()).toMatch(want);
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
});

test('the last tab choice is remembered per task, and the opening default is not', () => {
  const a: TaskItem = { task_id: 'CMX-MEM-A', title: 'a', status: 'awaiting_review', review_history: history(2) };
  open({ ...a, status: 'running' }); // default Brief: must NOT stick
  open(a);
  expect(selected()).toBe('Review (2)');
  choose(tab(/^Brief/));
  expect(selected()).toBe('Brief');
  open(a);
  expect(selected()).toBe('Brief');
  open({ ...a, task_id: 'CMX-MEM-B' });
  expect(selected()).toBe('Review (2)');
});

test('a task known only by `id` still remembers its tab, apart from another id', () => {
  const a: TaskItem = { task_id: undefined, id: 'CMX-ID-ONLY', title: 'a', status: 'awaiting_review', review_history: history(1) };
  open(a);
  choose(tab(/^Brief/));
  open(a);
  expect(selected()).toBe('Brief');
  open({ ...a, id: 'CMX-ID-OTHER' });
  expect(selected()).toBe('Review (1)');
});

test('a11y: a "Task" tablist whose tabs each control a labelled tabpanel', () => {
  open({ status: 'running', review_history: history(1) });
  expect(screen.getByRole('tablist', { name: 'Task' })).toBeTruthy();
  const brief = tab(/^Brief/);
  const panel = screen.getByRole('tabpanel');
  expect(brief.getAttribute('aria-controls')).toBe(panel.id);
  expect(panel.getAttribute('aria-labelledby')).toBe(brief.id);
  expect(brief.className).toBe('task-modal-tab active');
  expect(tab(/^Review/).className).toBe('task-modal-tab');
  expect(must(q('[role="tablist"]')).className).toBe('task-modal-tabs');
});

test('N rounds: N cards, newest first, only the newest open, full markdown inside', () => {
  open({ status: 'awaiting_review', review_history: history(13) });
  const cards = qa('details.task-modal-round');
  expect(cards.map((c) => c.dataset.ri)).toEqual([...Array(13).keys()].reverse().map(String));
  expect(cards.map((c) => (c as HTMLDetailsElement).open)).toEqual([true, ...Array(12).fill(false)]);
  expect(must(cards[0]?.querySelector('summary')).textContent).toMatch(/Round 13\s+changes requested/);
  expect(must(cards[0]?.querySelector('.task-modal-timeline-body')).innerHTML).toBe('<p>report <strong>13</strong></p>');
  expect(tab(/^Review/).textContent).toBe('Review (13)');
});

test.each([
  ['both', [{}, {}, {}], 2, '3 survived · 2 held-out'],
  ['visible only', [{}], 0, '1 survived · 0 held-out'],
  ['held-out only', undefined, 12, '0 survived · 12 held-out'],
  ['none', [], 0, null],
])("a round summary's survivor counts: %s", (_why, mutations, held, want) => {
  const body = held ? `verdict\n\n### 🙈 ${held} held-out guard(s) also survived` : 'verdict';
  open({
    status: 'awaiting_review',
    review_history: JSON.stringify([
      { round: 1, verdict: 'changes_requested', body, mutations },
      { round: 2, verdict: 'retry', body: 'keep going' },
    ]),
  });
  const [newest, oldest] = qa('.task-modal-round summary').map((s) => s.textContent ?? '');
  if (want) expect(oldest).toContain(want);
  else expect(oldest).not.toMatch(/survived|held-out/);
  expect(newest).not.toMatch(/survived|held-out/);
});

test("each round's summary carries its OWN verdict chip and timestamp; the round list agrees", () => {
  const rounds = [
    { round: 1, at: '2026-10-11T09:30:00+00:00', verdict: 'changes_requested', body: 'a' },
    { round: 2, at: '2026-10-12T14:05:00+00:00', verdict: 'cannot_verify', body: 'b' },
    { round: 3, at: '2026-10-13T20:45:00+00:00', verdict: 'clean', body: 'c' },
  ];
  open({ status: 'awaiting_review', review_history: JSON.stringify(rounds) });
  const sums = qa('.task-modal-round summary');
  const links = qa('.task-modal-roundlist .task-modal-roundlink');
  [...rounds].reverse().forEach((r, k) => {
    const label = r.verdict.replace(/_/g, ' ');
    expect(sums[k]?.querySelector('.badge')?.textContent).toBe(label);
    expect(links[k]?.querySelector('.badge')?.textContent).toBe(label);
    expect(links[k]?.getAttribute('aria-label')).toBe(`Round ${r.round}: ${label}`);
    const d = new Date(r.at);
    const day = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const hm = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
    expect(sums[k]?.textContent).toContain(`${hm} ${day}`);
  });
});

test('a round with no stored number is labelled by its position', () => {
  open({ status: 'awaiting_review', review_history: JSON.stringify([{ verdict: 'reopened' }, { verdict: 'retry' }]) });
  expect(qa('.task-modal-round-no').map((e) => e.textContent)).toEqual(['Round 2', 'Round 1']);
});

test('no rounds: Review (0) shows the empty note and the sidebar has no round list', () => {
  open({ status: 'running' });
  expect(tab(/^Review/).textContent).toBe('Review (0)');
  choose(tab(/^Review/));
  expect(qa('.task-modal-round')).toHaveLength(0);
  expect(screen.getByRole('tabpanel').textContent).toMatch(/No review history yet/);
  expect(q('.task-modal-rounds')).toBeNull();
});

test('a round-list click switches to Review, opens that round and scrolls it to the top', () => {
  open({ status: 'running', review_history: history(4) });
  expect(selected()).toBe('Brief');
  const links = qa('.task-modal-side .task-modal-roundlist .task-modal-roundlink');
  expect(links.map((l) => l.textContent?.trim().split(/\s+/).slice(0, 2).join(' '))).toEqual(['Round 4', 'Round 3', 'Round 2', 'Round 1']);
  fireEvent.click(must(links[3]));
  const card = must(q('.task-modal-round[data-ri="0"]')) as HTMLDetailsElement;
  expect(selected()).toBe('Review (4)');
  expect(card.open).toBe(true);
  expect(scrolled.map((s) => s.el)).toEqual([card]);
  expect(scrolled[0]?.opts).toEqual({ block: 'start', behavior: 'smooth' });
  // Only the clicked round opened: the newest stays open, every other stays shut.
  expect(qa('.task-modal-round').map((c) => (c as HTMLDetailsElement).open)).toEqual([true, false, false, true]);
});

test('a round jump scrolls once: coming back to Review later keeps the viewer\'s own scroll', () => {
  open({ status: 'running', review_history: history(3) });
  fireEvent.click(must(qa('.task-modal-roundlink')[2]));
  expect(scrolled).toHaveLength(1);
  choose(tab(/^Brief/));
  choose(tab(/^Review/));
  expect(scrolled).toHaveLength(1);
});

test('a round card toggled by hand stays as the viewer left it', () => {
  open({ status: 'awaiting_review', review_history: history(2) });
  const newest = must(q('.task-modal-round[data-ri="1"]')) as HTMLDetailsElement;
  act(() => {
    newest.open = false;
    newest.dispatchEvent(new Event('toggle'));
  });
  expect(newest.open).toBe(false);
  const older = must(q('.task-modal-round[data-ri="0"]')) as HTMLDetailsElement;
  act(() => {
    older.open = true;
    older.dispatchEvent(new Event('toggle'));
  });
  expect(qa('.task-modal-round').map((c) => (c as HTMLDetailsElement).open)).toEqual([false, true]);
});

test('the brief pane renders the brief as markdown, brief > body > raw', () => {
  open({ status: 'open', brief: '### OBJECTIVE\nDo the **thing**.', body: 'not this' });
  expect(must(q('.task-modal-brief.md > div')).innerHTML).toBe('<h3 class="kn-mh">OBJECTIVE</h3><p>Do the <strong>thing</strong>.</p>');
  open({ status: 'open', raw: '- [ ] the raw bullet' });
  expect(must(q('.task-modal-brief')).textContent).toContain('the raw bullet');
});

test('no brief at all: a plain note, never a blank pane', () => {
  open({ status: 'open' });
  expect(must(q('.task-modal-brief')).textContent).toBe('No brief recorded for this task.');
});

test('🔴 a brief holding a javascript: link renders it as text, never a live link (CMX-228)', () => {
  open({ status: 'open', brief: 'see [the fix](javascript:alert.call) and [docs](https://example.com)' });
  const links = [...must(q('.task-modal-brief')).querySelectorAll('a')];
  expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://example.com']);
  expect(must(q('.task-modal-brief')).textContent).toContain('see the fix and docs');
});

test('the details rows: every field that has a value, in order; none renders an empty row', () => {
  open({
    workflow_path: '/x/chelamux/WORKFLOW.md',
    branch_name: 'cmx-7',
    model: 'opus',
    pr_url: 'https://github.com/o/r/pull/12',
    pr_state: 'open',
    pr_checks: 'failing',
    attempt: 0,
    risk: 'high',
    risk_reason: "inferred: BOUNDARIES touch 'judge.py'",
    rework_count: 2,
    judge_state: 'blocked_race',
    judge_detail: 'a guard survived',
    started_at: '2026-10-12T14:05:00+00:00',
    ended_at: '2026-10-12T15:05:00+00:00',
    last_error: 'tmux window disappeared',
  });
  expect(qa('.task-modal-row-label').map((l) => l.textContent)).toEqual(['Workflow', 'Branch', 'Model', 'PR', 'Attempt', 'Risk', 'Reworks', 'Judge', 'Started', 'Ended', 'Error']);
  expect(rowValue('Attempt')?.textContent).toBe('0');
  const pr = must(rowValue('PR'));
  const a = must(pr.querySelector('a.pr-badge'));
  expect([a.textContent, a.getAttribute('href'), a.getAttribute('target'), a.getAttribute('rel')]).toEqual(['#12', 'https://github.com/o/r/pull/12', '_blank', 'noopener noreferrer']);
  expect(pr.querySelector('.ts')?.textContent).toBe('open');
  const ci = must(pr.querySelector('.kanban-ci-chip'));
  expect([ci.className, ci.textContent?.trim()]).toEqual(['kanban-ci-chip ci-failing', 'CI RED']);
  const risk = must(rowValue('Risk'));
  expect([risk.querySelector('.kanban-risk-chip')?.className, risk.querySelector('.kanban-risk-chip')?.textContent]).toEqual(['kanban-risk-chip risk-high', 'risk: high']);
  expect(risk.querySelector('.ts')?.textContent).toBe("inferred: BOUNDARIES touch 'judge.py'");
  const judge = must(rowValue('Judge'));
  expect([judge.querySelector('.badge')?.className, judge.querySelector('.badge')?.textContent, judge.querySelector('.ts')?.textContent]).toEqual(['badge badge-off', 'blocked_race', 'a guard survived']);
  expect(rowValue('Error')?.querySelector('.task-modal-error')?.textContent).toBe('tmux window disappeared');
  expect(rowValue('Reworks')?.textContent).toBe('2');
});

test('a sparse item: only what it has; nothing at all says "No details recorded."', () => {
  open({ pr_url: 'https://gitlab.example/mr/3', attempt: null, rework_count: 0, risk: 'extreme' });
  expect(qa('.task-modal-row-label').map((l) => l.textContent)).toEqual(['PR']);
  const pr = must(rowValue('PR'));
  expect(pr.querySelector('a')?.textContent).toBe('PR');
  expect(pr.querySelector('.ts')).toBeNull();
  expect(pr.querySelector('.kanban-ci-chip')?.textContent?.trim()).toBe('ci unknown');
  open({ risk: 'low' });
  expect(must(rowValue('Risk')).querySelector('.ts')).toBeNull();
  open({ judge_state: 'clean' });
  expect(must(rowValue('Judge')).querySelector('.ts')).toBeNull();
  open({});
  expect(qa('.task-modal-row')).toHaveLength(0);
  expect(must(q('.task-modal-side')).textContent).toContain('No details recorded.');
});

test('the close button and Escape close it', () => {
  open({ title: 'x' });
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(dialog()).toBeNull();
  open({ title: 'y' });
  fireEvent.keyDown(must(dialog()), { key: 'Escape' });
  expect(dialog()).toBeNull();
});

test('opening another item replaces the content, nothing from the last one lingers', () => {
  open({ title: 'first', brief: 'FIRST BRIEF' });
  open({ title: 'second' });
  expect(must(q('.task-modal-title')).textContent).toBe('second');
  expect(must(dialog()).textContent).not.toContain('FIRST BRIEF');
});

test('a stray scrollIntoView call is not made on open', () => {
  open({ status: 'awaiting_review', review_history: history(2) });
  expect(scrolled).toEqual([]);
});
