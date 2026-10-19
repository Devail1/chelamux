import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { createBridge } from '../../../shared/bridge/index.ts';
import { CONTEXT_SHOWN_FROM, Sidebar, legacyActions, mountSidebar } from '../index.ts';
import { DISPATCHED_KEY, OTHER_KEY, type SidebarState, sidebarOrder } from '../model.ts';
import { NOW, actions, runWin, state, win } from './fixtures.ts';

afterEach(cleanup);

function draw(s: SidebarState | null, acts = actions()) {
  const { container } = render(<Sidebar state={s} actions={acts} />);
  return { el: container, acts };
}

const rowOf = (el: HTMLElement, name: string) => {
  const row = el.querySelector<HTMLElement>(`.agent-row[data-agent="${name}"]`);
  if (!row) throw new Error(`no row for ${name}`);
  return row;
};
const dot = (row: HTMLElement) => row.querySelector('.term-status-dot');
const groupKeys = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('.side-group')].map(g => g.dataset.g);
const meta = (row: HTMLElement, kind: string) => row.querySelector<HTMLElement>(`.ar-meta [data-meta="${kind}"]`);

// --- the row: title first, metadata as icons, the state as a shape ---------------------

// The widths themselves (the title keeps its room, the metadata shrinks) are measured in a
// real browser: tests/browser/react_islands.test.mjs.
test('the title comes first and the metadata after it, both on the one row line', () => {
  const a = win('orch', { ai_title: 'A long session title' });
  const { el } = draw(state([a], { orchWid: a.window_id ?? null, labels: { [a.window_id ?? '']: 'A long session title' } }));
  const row = rowOf(el, 'orch');
  const kids = [...row.children].map(c => c.className);
  expect(kids.indexOf('agent-row-name')).toBeGreaterThan(-1);
  expect(kids.indexOf('agent-row-name')).toBeLessThan(kids.indexOf('ar-meta'));
  expect(kids).toEqual(['term-status-dot idle', 'agent-row-name', 'ar-meta', 'row-more']);
  expect(row.querySelector('.agent-row-name')?.textContent).toBe('A long session title');
});

test('each metadata kind renders its icon (or number) and a tooltip naming it', () => {
  const orch = win('orch');
  const pr = runWin('CMX-9', 'agent', { pr_url: 'https://github.com/o/r/pull/673' }, { pr: { url: 'https://github.com/o/r/pull/673', state: 'open', draft: true } });
  const judging = runWin('CMX-8', 'judge', { status: 'awaiting_review', judge_state: 'running' },
    { judge_battery: { state: 'testing', label: '⚖️ testing · 0/6 · 14m', done: 0, total: 6 } });
  const hot = win('hot');
  const { el } = draw(state([orch, pr, judging, hot], { orchWid: orch.window_id ?? null, ctx: { [hot.window_id ?? '']: 62.4 } }));

  const crown = meta(rowOf(el, 'orch'), 'orchestrator');
  expect(crown?.querySelector('svg.lucide-crown')).toBeTruthy();
  expect(crown?.title).toBe('Orchestrator: holds the decisions inbox');
  expect(crown?.getAttribute('aria-label')).toBe('Orchestrator: holds the decisions inbox');

  const prChip = meta(rowOf(el, 'liavacc/cmx-9'), 'pr');
  expect(prChip?.querySelector('svg.lucide-git-pull-request')).toBeTruthy();
  expect(prChip?.textContent).toBe('#673');
  expect(prChip?.title).toBe('PR #673 · draft');
  expect(prChip?.className).toBe('ar-pr draft');

  const flask = meta(rowOf(el, 'judge-liavacc/cmx-8'), 'judging');
  expect(flask?.querySelector('svg.lucide-flask-conical')).toBeTruthy();
  expect(flask?.textContent).toBe('0/6');
  expect(flask?.title).toBe('Judge testing · 0/6 · 14m');

  const ctx = meta(rowOf(el, 'hot'), 'context');
  expect(ctx?.textContent).toBe('62%');
  expect(ctx?.title).toBe('Context 62% used');
  expect(ctx?.className).toBe('ar-ctx warn');
  // only the orchestrator's row has a crown
  expect(meta(rowOf(el, 'hot'), 'orchestrator')).toBeNull();
});

test(`context % shows from ${CONTEXT_SHOWN_FROM}% only, coloured past 60 and 80`, () => {
  const rows = [49.4, 50, 61, 81].map(p => win(`c${p}`));
  const ctx = Object.fromEntries(rows.map((a, i) => [a.window_id ?? '', [49.4, 50, 61, 81][i] ?? 0]));
  const { el } = draw(state(rows, { ctx }));
  expect(CONTEXT_SHOWN_FROM).toBe(50);
  expect(meta(rowOf(el, 'c49.4'), 'context')).toBeNull();
  expect(meta(rowOf(el, 'c50'), 'context')?.className).toBe('ar-ctx');
  expect(meta(rowOf(el, 'c61'), 'context')?.className).toBe('ar-ctx warn');
  expect(meta(rowOf(el, 'c81'), 'context')?.className).toBe('ar-ctx danger');
});

test('the PR chip: a known state is a class, an unknown one is not; no PR url, or Show PR off, no chip', () => {
  const url = 'https://github.com/o/r/pull/5';
  const merged = runWin('CMX-1', 'agent', { pr_url: url }, { pr: { url, state: 'merged' } });
  const odd = runWin('CMX-2', 'agent', { pr_url: url.replace('5', '6') }, { pr: { url: url.replace('5', '6'), state: 'weird' } });
  const none = runWin('CMX-3', 'agent');
  const { el } = draw(state([merged, odd, none]));
  expect(meta(rowOf(el, 'liavacc/cmx-1'), 'pr')?.className).toBe('ar-pr merged');
  expect(meta(rowOf(el, 'liavacc/cmx-2'), 'pr')?.className).toBe('ar-pr');
  expect(meta(rowOf(el, 'liavacc/cmx-2'), 'pr')?.title).toBe('PR #6');
  expect(meta(rowOf(el, 'liavacc/cmx-3'), 'pr')).toBeNull();
  cleanup();
  const off = draw(state([merged], { view: { ...state([]).view, showPR: false } }));
  expect(meta(rowOf(off.el, 'liavacc/cmx-1'), 'pr')).toBeNull();
});

test('a judge battery with no total reads its phase; a dead one shows no count', () => {
  const phase = runWin('CMX-4', 'judge', {}, { judge_battery: { state: 'testing', label: '⚖️ testing · setup · 1m', phase: 'baseline', total: null } });
  const died = runWin('CMX-5', 'judge', { status: 'awaiting_review' }, { judge_battery: { state: 'died', label: '⚖️ judge run died' } });
  const { el } = draw(state([phase, died]));
  expect(meta(rowOf(el, 'judge-liavacc/cmx-4'), 'judging')?.textContent).toBe('baseline');
  expect(meta(rowOf(el, 'judge-liavacc/cmx-5'), 'judging')).toBeNull();
  expect(dot(rowOf(el, 'judge-liavacc/cmx-5'))?.getAttribute('aria-label')).toBe('Dispatched run: ⚖️ judge run died');
});

test('no status WORD in the row text; the dot names it (aria-label) and the tooltip says it', () => {
  const rows = [win('w', { session_status: 'busy' }), win('q', { session_status: 'waiting' }),
    win('i'), win('d', { done: true }), runWin('CMX-7', 'agent', { status: 'changes_requested' })];
  const { el } = draw(state(rows));
  for (const [name, word] of [['w', 'working'], ['q', 'waiting'], ['i', 'idle'], ['d', 'done']] as const) {
    const row = rowOf(el, name);
    expect(row.textContent).not.toContain(word);
    expect(dot(row)?.getAttribute('aria-label')).toBe(`Session: ${word}`);
    expect(dot(row)?.getAttribute('role')).toBe('img');
    expect(dot(row)?.classList.contains(word)).toBe(true);
    expect(row.title.split('\n')).toContain(word);
  }
  const run = rowOf(el, 'liavacc/cmx-7');
  expect(run.textContent).not.toContain('rework');
  expect(dot(run)?.getAttribute('aria-label')).toBe('Dispatched run: rework');
  expect(run.title.split('\n')[0]).toBe('CMX-7 · Task CMX-7: rework');
});

test('`done` outranks waiting: a done row is never drawn as blocked on you', () => {
  const { el } = draw(state([win('dw', { done: true, session_status: 'busy' })]));
  expect(dot(rowOf(el, 'dw'))?.className).toBe('term-status-dot done');
});

test('a dispatched run and a human session render DISTINCT shapes, even in the same state', () => {
  const human = win('human', { session_status: 'busy' });
  const run = runWin('CMX-6', 'agent', { status: 'running' });
  const { el } = draw(state([human, run]));
  const h = dot(rowOf(el, 'human'));
  const r = dot(rowOf(el, 'liavacc/cmx-6'));
  expect(h?.className).toBe('term-status-dot working');
  expect(r?.className).toBe('term-status-dot dispatched working');
  expect(r?.getAttribute('aria-label')).toBe('Dispatched run: working');
  expect(rowOf(el, 'liavacc/cmx-6').classList.contains('run-row')).toBe(true);
  expect(rowOf(el, 'human').classList.contains('run-row')).toBe(false);
});

test('the tooltip: label, window name and id, the state, the extras and how long ago', () => {
  const a = win('key', { ai_title: 'Fix it', recap: 'Tests green', recap_ts: new Date(NOW - 180_000).toISOString() });
  const b = win('plain');
  const { el } = draw(state([a, b], { labels: { [a.window_id ?? '']: 'Fix it' }, onWall: [b.window_id ?? ''] }));
  const row = rowOf(el, 'key');
  expect(row.title).toBe(`Fix it (key) · ${a.window_id}\nidle\nFix it, Tests green\n3m ago`);
  const name = row.querySelector('.agent-row-name');
  expect(name?.textContent).toBe('Fix it');
  expect(name?.getAttribute('data-key')).toBe('key');
  expect(name?.getAttribute('title')).toBe('window: key');
  expect(rowOf(el, 'plain').title).toBe(`plain · ${b.window_id}\nidle (open on the wall)`);
  expect(rowOf(el, 'plain').querySelector('.agent-row-name')?.hasAttribute('data-key')).toBe(false);
  expect(rowOf(el, 'plain').classList.contains('on-wall')).toBe(true);
  expect(row.classList.contains('on-wall')).toBe(false);
  expect(row.querySelector('.ar-wid')?.textContent).toBe(a.window_id);
});

test('ago: seconds, minutes, hours, days', () => {
  const at = (sec: number) => win(`t${sec}`, { recap_ts: new Date(NOW - sec * 1000).toISOString() });
  const rows = [30, 120, 7200, 172800].map(at);
  const { el } = draw(state(rows));
  expect(['30s ago', '2m ago', '2h ago', '2d ago'])
    .toEqual(rows.map(a => rowOf(el, a.name ?? '').title.split('\n').pop()));
});

test('a run row lists its agent and judge windows in the tooltip', () => {
  const ag = runWin('CMX-3', 'agent');
  const ju = runWin('CMX-3', 'judge');
  const { el } = draw(state([ag, ju]));
  expect(rowOf(el, ag.name ?? '').title).toBe(
    `CMX-3 · Task CMX-3: working\nagent ${ag.name} · ${ag.window_id}\njudge ${ju.name} · ${ju.window_id}`);
  expect(el.querySelectorAll('.agent-row').length).toBe(1);
});

// --- groups and order -------------------------------------------------------------------

test('Dispatched renders after EVERY folder group, Other included, whatever the stored order says', () => {
  const rows = [runWin('CMX-1', 'agent'), win('b', { cwd: '/srv/code/beta' }), win('o', { cwd: null }), win('a')];
  const { el } = draw(state(rows, { order: [DISPATCHED_KEY, '/srv/code/beta', OTHER_KEY] }));
  // alpha (unstored) lands after the last stored folder, beta; Other keeps its stored place
  expect(groupKeys(el)).toEqual(['/srv/code/beta', '/srv/code/alpha', OTHER_KEY, DISPATCHED_KEY]);
  const head = el.querySelector(`.side-group[data-g="${DISPATCHED_KEY}"] .group-count`);
  expect(head?.textContent).toBe('1');
  expect(head?.getAttribute('title')).toBe('1 run');
});

test('sidebarOrder() equals the order the rows render in (Needs you first, a collapsed group still counted)', () => {
  const rows = [win('z', { cwd: '/srv/code/zeta' }), win('a2'), win('a1'), win('needs', { cwd: '/srv/code/zeta', session_status: 'waiting' }),
    runWin('CMX-2', 'agent'), win('m', { cwd: '/srv/code/mid' })];
  const s = state(rows, { order: ['/srv/code/zeta', '/srv/code/alpha'] });
  const { el } = draw(s);
  const rendered = [...el.querySelectorAll<HTMLElement>('.agent-row')]
    .map(r => rows.find(a => a.name === r.dataset.agent)?.window_id);
  expect(sidebarOrder(s)).toEqual(rendered);
  // negative control: the input order is not the rendered one, so a pass-through would be RED
  expect(rendered).not.toEqual(rows.map(a => a.window_id));
  // collapsing a group hides nothing from the order
  cleanup();
  expect(sidebarOrder({ ...s, collapsed: ['/srv/code/alpha'] })).toEqual(rendered);
  expect(sidebarOrder(null)).toEqual([]);
});

test('sidebarOrder() lifts the View filters: a filtered-out row keeps its slot', () => {
  const old = win('old', { last_activity: (NOW - 30 * 86400000) / 1000 });
  const fresh = win('fresh', { last_activity: NOW / 1000 });
  const s = state([old, fresh], { view: { ...state([]).view, activity: '1d', sort: 'activity' } });
  const { el } = draw(s);
  expect(el.querySelector('.agent-row[data-agent="old"]')).toBeNull();
  expect(sidebarOrder(s)).toEqual([fresh.window_id, old.window_id]);
});

test('a group head: name, caret, menu and "+" (only with a folder); a collapsed group says so', () => {
  const { el, acts } = draw(state([win('a'), runWin('CMX-1', 'agent')], { collapsed: ['/srv/code/alpha'] }));
  const g = el.querySelector<HTMLElement>('.side-group[data-g="/srv/code/alpha"]');
  expect(g?.className).toBe('side-group collapsed');
  const head = g?.querySelector<HTMLElement>('.group-head');
  expect(head?.getAttribute('aria-expanded')).toBe('false');
  expect(head?.querySelector('.group-name')?.textContent).toBe('alpha');
  expect(head?.querySelector('.group-name')?.getAttribute('title')).toBe('/srv/code/alpha');
  expect(head?.querySelector('.group-count')).toBeNull();
  const d = el.querySelector(`.side-group[data-g="${DISPATCHED_KEY}"]`);
  expect(d?.className).toBe('side-group');
  expect(d?.querySelector('.group-head')?.getAttribute('aria-expanded')).toBe('true');
  expect(d?.querySelector('.group-add')).toBeNull();

  fireEvent.click(head as HTMLElement);
  expect(acts.toggleGroup.mock.calls).toEqual([['/srv/code/alpha']]);
  fireEvent.click(g?.querySelector('.group-add') as Element);
  expect(acts.groupNewSession.mock.calls).toEqual([['/srv/code/alpha']]);
  fireEvent.click(g?.querySelector('.group-more') as Element);
  fireEvent.contextMenu(head as HTMLElement);
  expect(acts.openGroupMenu.mock.calls.map(c => c[1])).toEqual(['/srv/code/alpha', '/srv/code/alpha']);
  // the buttons' clicks stop at the button: the head did not toggle again
  expect(acts.toggleGroup).toHaveBeenCalledTimes(1);
  expect(g?.querySelector('.group-add')?.getAttribute('aria-label')).toBe('New session in alpha');
  expect(g?.querySelector('.group-more')?.getAttribute('aria-label')).toBe('alpha menu');
});

test('Show empty groups keeps a folder with no rows, marked empty; Group by None draws one flat list', () => {
  const s = state([win('a')], { order: ['/srv/code/gone'], view: { ...state([]).view, showEmpty: true } });
  let { el } = draw(s);
  expect(el.querySelector('.side-group[data-g="/srv/code/gone"]')?.className).toBe('side-group empty');
  cleanup();
  ({ el } = draw(state([win('a'), win('b', { cwd: '/x/b' })], { view: { ...state([]).view, groupBy: 'none' } })));
  expect(el.querySelectorAll('.side-group').length).toBe(1);
  expect(el.querySelector('.side-group')?.className).toBe('side-group flat');
  expect(el.querySelector('.group-head')).toBeNull();
  expect(el.querySelectorAll('.side-group.flat .agent-row').length).toBe(2);
});

test('Needs you sits above the groups with its count', () => {
  const { el } = draw(state([win('a'), win('q', { needs_human: true })]));
  const ny = el.firstElementChild;
  expect(ny?.className).toBe('side-triage side-needs-you');
  expect(ny?.querySelector('.triage-count')?.textContent).toBe('1');
  expect(ny?.querySelector('.agent-row')?.getAttribute('data-agent')).toBe('q');
});

// --- the row's wiring ---------------------------------------------------------------------

test('a click opens the row; right-click and its ⋯ open the row menu with the row as anchor', () => {
  const run = runWin('CMX-1', 'agent');
  const { el, acts } = draw(state([win('a'), run], { active: 'a' }));
  const anchors: unknown[] = [];
  acts.openRowMenu.mockImplementation(ev => { anchors.push(ev.currentTarget); });
  const row = rowOf(el, 'a');
  expect(row.classList.contains('active')).toBe(true);
  expect(rowOf(el, run.name ?? '').classList.contains('active')).toBe(false);
  fireEvent.click(row);
  expect(acts.selectAgent.mock.calls).toEqual([['a']]);
  fireEvent.contextMenu(row);
  expect(acts.openRowMenu.mock.calls[0]?.[1]).toBe(`w:${row.dataset.item?.slice(2)}`);
  expect(anchors[0]).toBe(row);
  const more = rowOf(el, run.name ?? '').querySelector<HTMLElement>('.row-more');
  expect(more?.getAttribute('aria-label')).toBe('Run menu');
  expect(row.querySelector('.row-more')?.getAttribute('aria-label')).toBe('Row menu');
  fireEvent.click(more as HTMLElement);
  expect(acts.openRowMenu.mock.calls[1]?.[1]).toBe('run:CMX-1');
  expect(anchors[1]).toBe(more);
  expect(acts.selectAgent).toHaveBeenCalledTimes(1);   // the ⋯ click stopped at the button
  expect(rowOf(el, run.name ?? '').dataset.run).toBe('CMX-1');
  expect(row.hasAttribute('data-run')).toBe(false);
});

test('holding Alt numbers the first nine VISIBLE rows: not a collapsed group, not the archive', () => {
  const rows = [win('q', { session_status: 'waiting' }), ...Array.from({ length: 10 }, (_, i) => win(`r${i}`)),
    win('hidden', { cwd: '/srv/code/beta' })];
  const s = state(rows, { altHeld: true, collapsed: ['/srv/code/beta'] });
  const { el } = draw(s);
  const nums = [...el.querySelectorAll<HTMLElement>('.agent-row')]
    .map(r => [r.dataset.agent, r.querySelector('.ar-altnum')?.textContent ?? null]);
  expect(nums.slice(0, 10)).toEqual([['q', '1'], ...Array.from({ length: 8 }, (_, i) => [`r${i}`, String(i + 2)]), ['r8', null]]);
  expect(Object.fromEntries(nums)['hidden']).toBeNull();
  expect(el.querySelector('.ar-altnum')?.getAttribute('aria-hidden')).toBe('true');
  cleanup();
  expect(draw({ ...s, altHeld: false }).el.querySelector('.ar-altnum')).toBeNull();
});

// --- the archive (its actions stay legacy's until slice B) ---------------------------

test('Status All draws the Archived section at the bottom: closed sessions and hidden rows, each with Unarchive', async () => {
  const a = win('arch');
  const live = win('live');
  const s = state([a, live], {
    archived: [`w:${a.window_id}`], view: { ...state([]).view, status: 'all' },
    sessions: [{ key: 's:1', name: 'old session', cwd: '/srv/code/gone/', archived_at: NOW / 1000 - 7200 }],
  });
  const { el, acts } = draw(s);
  const sec = el.querySelector<HTMLElement>('.side-archived');
  expect(sec?.className).toBe('side-group side-archived collapsed');
  expect(sec?.querySelector('.archived-count')?.textContent).toBe('2');
  expect(groupKeys(el).at(-1)).toBe('~archived');
  const closed = sec?.querySelector<HTMLElement>('.session-row');
  expect(closed?.querySelector('.agent-row-name')?.textContent).toBe('old session');
  expect(closed?.querySelector('.ar-folder')?.textContent).toBe('gone');
  expect(closed?.title).toBe('old session: archived (closed)\n/srv/code/gone/\narchived 2h ago');
  expect(closed?.querySelector('.row-unarchive')?.getAttribute('title')).toBe('Unarchive: resume this session in a new window');
  const hidden = sec?.querySelector<HTMLElement>('.agent-row[data-agent="arch"]');
  expect(hidden?.className).toContain('archived');
  expect(hidden?.querySelector('.row-unarchive')?.getAttribute('title')).toBe('Unarchive');
  expect(el.querySelector('.side-archived-toggle')?.textContent).toBe('Hide archived');

  fireEvent.click(sec?.querySelector('.group-head') as Element);
  expect(acts.toggleArchivedSection).toHaveBeenCalledTimes(1);
  fireEvent.click(el.querySelector('.side-archived-toggle') as Element);
  expect(acts.toggleShowArchived).toHaveBeenCalledTimes(1);

  let done!: () => void;
  acts.unarchiveRow.mockImplementation(() => new Promise<void>(r => { done = r; }));
  const btn = hidden?.querySelector<HTMLButtonElement>('.row-unarchive') as HTMLButtonElement;
  // two clicks in one batch, before React re-renders the button disabled: still one resume
  act(() => {
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  expect(acts.selectAgent).not.toHaveBeenCalled();   // the click stops at the button
  fireEvent.click(btn);
  expect(acts.unarchiveRow.mock.calls).toEqual([[null, `w:${a.window_id}`]]);
  const finishHidden = done;
  expect(btn.disabled).toBe(true);
  expect(btn.textContent).toBe('Unarchiving…');   // a hidden live row is shown again, not resumed
  fireEvent.click(closed?.querySelector('.row-unarchive') as Element);
  expect(closed?.querySelector('.row-unarchive')?.textContent).toBe('Resuming…');
  await act(async () => { finishHidden(); done(); });
  expect(btn.disabled).toBe(false);
  expect(btn.textContent).toBe('Unarchive');
});

test('the archive: open when opened or under Status Archived; Active hides it behind "Show archived (N)"', () => {
  const a = win('arch');
  const base = { archived: [`w:${a.window_id}`] };
  let { el } = draw(state([a, win('x')], { ...base, collapsed: ['~archived:open'], view: { ...state([]).view, status: 'all' } }));
  expect(el.querySelector('.side-archived')?.className).toBe('side-group side-archived');
  expect(el.querySelector('.side-archived .group-head')?.getAttribute('aria-expanded')).toBe('true');
  cleanup();
  ({ el } = draw(state([a], { ...base, view: { ...state([]).view, status: 'archived' } })));
  expect(el.querySelector('.side-archived')?.className).toBe('side-group side-archived');
  expect(el.querySelector('.side-empty')).toBeNull();
  cleanup();
  ({ el } = draw(state([a, win('x')], base)));
  expect(el.querySelector('.side-archived')).toBeNull();
  expect(el.querySelector('.side-archived-toggle')?.textContent).toBe('Show archived (1)');
  cleanup();
  ({ el } = draw(state([win('x')])));
  expect(el.querySelector('.side-archived-toggle')).toBeNull();
});

test('no rows: "No agents"; rows the view filters all out: "No sessions match this view"', () => {
  expect(draw(null).el.textContent).toBe('No agents');
  cleanup();
  expect(draw(state([])).el.textContent).toBe('No agents');
  cleanup();
  const { el } = draw(state([win('a')], { view: { ...state([]).view, env: ['judge'] } }));
  expect(el.querySelector('.side-empty')?.textContent).toBe('No sessions match this view');
});

// --- the island ---------------------------------------------------------------------------

test('mountSidebar repaints SYNCHRONOUSLY on every publish (legacy reads the rows right back)', () => {
  document.body.innerHTML = '<div id="sidebar-agents"><div class="side-empty">stale</div></div>';
  const bridge = createBridge();
  const acts = actions();
  const root = mountSidebar(window, bridge, acts);
  const host = document.getElementById('sidebar-agents') as HTMLElement;
  expect(host.textContent).toBe('No agents');
  bridge.sidebar.set(state([win('one')]));
  expect(host.querySelector('.agent-row')?.getAttribute('data-agent')).toBe('one');
  act(() => root?.unmount());
  document.body.innerHTML = '';
  expect(mountSidebar(window, bridge, acts)).toBeNull();
});

test('legacyActions calls window.chela at CALL time (nav.js registers after the island boots)', () => {
  const w = window as unknown as { chela?: Record<string, (...a: unknown[]) => unknown> };
  delete w.chela;
  const acts = legacyActions(window);
  expect(() => acts.selectAgent('a')).not.toThrow();
  const calls: unknown[][] = [];
  const rec = (name: string) => (...a: unknown[]) => { calls.push([name, ...a]); return name; };
  w.chela = Object.fromEntries(['selectAgent', 'toggleGroup', 'openGroupMenu', 'openRowMenu', 'groupNewSession',
    'toggleArchivedSection', 'toggleShowArchived', 'unarchiveRow'].map(n => [n, rec(n)]));
  const ev = { preventDefault() {}, stopPropagation() {}, currentTarget: document.body };
  acts.selectAgent('a');
  acts.toggleGroup('g');
  acts.openGroupMenu(ev, 'g');
  acts.openRowMenu(ev, 'r');
  acts.groupNewSession('g');
  acts.toggleArchivedSection();
  acts.toggleShowArchived();
  expect(acts.unarchiveRow(null, 'k')).toBe('unarchiveRow');
  expect(calls).toEqual([['selectAgent', 'a'], ['toggleGroup', 'g'], ['openGroupMenu', ev, 'g'], ['openRowMenu', ev, 'r'],
    ['groupNewSession', 'g'], ['toggleArchivedSection'], ['toggleShowArchived'], ['unarchiveRow', null, 'k']]);
  delete w.chela;
});
