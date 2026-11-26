// CMX-434: the "Suggest names" dialog: it lists the live interactive sessions, shows each one's
// suggestion editable, flags a bad or duplicate name inline (and refuses to apply), and applies
// names through the rename API and titles through the title API, reporting each row.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { SuggestNames, closeSuggestNames, openSuggestNames } from '../index.ts';

type Call = { url: string; body: unknown; resolve: (b: unknown) => void; reject: (e: Error) => void };
let pending: Call[] = [];
let done: Call[] = [];

beforeEach(() => {
  pending = [];
  done = [];
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) =>
    new Promise((resolve, reject) => {
      pending.push({
        url, body: opts?.body ? JSON.parse(String(opts.body)) : undefined,
        resolve: (b) => resolve({ ok: true, json: () => Promise.resolve(b) }), reject,
      });
    }));
  agents.set([
    { window_id: '@1', name: 'tetris', claude_running: true },
    { window_id: '@2', name: 'Mine', claude_running: true, manual_name: true },
    { window_id: '@3', name: 'cmx-9', claude_running: true, dispatched: true },
    { window_id: '@4', name: 'bash', claude_running: false },
  ]);
});
afterEach(() => {
  act(() => closeSuggestNames());
  cleanup();
  vi.unstubAllGlobals();
});

async function answer(url: string, body: unknown) {
  const i = pending.findIndex(p => p.url === url);
  if (i < 0) throw new Error(`no pending fetch for ${url}; pending: ${pending.map(p => p.url).join(', ')}`);
  const [p] = pending.splice(i, 1);
  if (!p) return;
  done.push(p);
  await act(async () => {
    p.resolve(body);
  });
}

/** Answer every pending request, in order, from `table` (by url), until none is left. */
async function answerAll(table: Record<string, unknown>) {
  for (let n = 0; n < 50 && pending.length; n++) {
    const p = pending[0];
    if (!p) break;
    if (!(p.url in table)) throw new Error(`unexpected request ${p.url}`);
    await answer(p.url, table[p.url]);
  }
}

const SUGGEST = '/api/agents/suggest-names';
const ROWS = [
  { wid: '@1', name: 'tetris', manual_name: false, title: 'Tetris-style blocks', title_pending: null, content: true, note: null,
    suggested_name: 'tipstack', suggested_title: 'Tipstack billing' },
  { wid: '@2', name: 'Mine', manual_name: true, title: null, title_pending: 'Queued one', content: true, note: null,
    suggested_name: 'mine-api', suggested_title: 'Mine API' },
];

async function openWith(rows: unknown = ROWS, error: string | null = null) {
  render(<SuggestNames />);
  act(() => openSuggestNames());
  expect(screen.getByRole('dialog', { name: 'Suggest names' })).toBeTruthy();
  expect(screen.getByText('Reading each session…')).toBeTruthy();
  await answer(SUGGEST, { ok: true, rows, error });
}

const input = (label: string) => screen.getByRole('textbox', { name: label }) as HTMLInputElement;
const button = (name: RegExp | string) => screen.getByRole('button', { name }) as HTMLButtonElement;
const type = (label: string, value: string) => fireEvent.change(input(label), { target: { value } });

test('opens on the interactive sessions only; nothing is applied by opening it', async () => {
  await openWith();
  expect(done.map(c => [c.url, c.body])).toEqual([[SUGGEST, { wids: ['@1', '@2'] }]]);
  expect(input('New name for tetris').value).toBe('tipstack');
  expect(input('New title for tetris').value).toBe('Tipstack billing');
  // the manual row is pre-unchecked, the other checked
  expect(screen.getByRole('switch', { name: 'Apply to tetris' }).getAttribute('aria-checked')).toBe('true');
  expect(screen.getByRole('switch', { name: 'Apply to Mine' }).getAttribute('aria-checked')).toBe('false');
  expect(screen.getByText('(manual)')).toBeTruthy();
  expect(screen.getByText('pending')).toBeTruthy();       // its queued title
  expect(button('Apply selected (1)').disabled).toBe(false);
  expect(pending).toEqual([]);
});

test('the toggle includes dispatched runs and judges: a fresh request', async () => {
  await openWith();
  fireEvent.click(screen.getByRole('switch', { name: 'Include dispatched runs and judges' }));
  expect(screen.getByText('Reading each session…')).toBeTruthy();
  await answer(SUGGEST, { ok: true, rows: [], error: null });
  expect(done.map(c => c.body)).toEqual([{ wids: ['@1', '@2'] }, { wids: ['@1', '@2', '@3'] }]);
  expect(screen.getByText('No live sessions to name.')).toBeTruthy();
});

test('a reply to a superseded request is dropped: flipping the toggle mid-read ignores the old answer', async () => {
  render(<SuggestNames />);
  act(() => openSuggestNames());
  fireEvent.click(screen.getByRole('switch', { name: 'Include dispatched runs and judges' }));
  expect(pending.map(p => p.body)).toEqual([{ wids: ['@1', '@2'] }, { wids: ['@1', '@2', '@3'] }]);
  // the first (stale) request answers late, with rows: none of them may land
  await answer(SUGGEST, { ok: true, rows: ROWS, error: null });
  expect(screen.getByText('Reading each session…')).toBeTruthy();
  expect(screen.queryByRole('textbox', { name: 'New name for tetris' })).toBeNull();
  // the current request's answer is what shows
  await answer(SUGGEST, { ok: true, rows: [], error: null });
  expect(screen.getByText('No live sessions to name.')).toBeTruthy();
  expect(screen.queryByRole('textbox', { name: 'New name for tetris' })).toBeNull();
});

test('a stale request\'s failure is dropped: the current read still shows its rows', async () => {
  render(<SuggestNames />);
  act(() => openSuggestNames());
  fireEvent.click(screen.getByRole('switch', { name: 'Include dispatched runs and judges' }));
  expect(pending).toHaveLength(2);
  const stale = pending.shift();
  if (!stale) throw new Error('no stale request');
  await act(async () => {
    stale.reject(new Error('down'));
  });
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText('Reading each session…')).toBeTruthy();
  await answer(SUGGEST, { ok: true, rows: ROWS, error: null });
  expect(input('New name for tetris').value).toBe('tipstack');
});

test('an edit touches only its own row: name, title and the apply toggle', async () => {
  await openWith();
  type('New name for tetris', 'blocks');
  type('New title for tetris', 'Blocks game');
  expect(input('New name for tetris').value).toBe('blocks');
  expect(input('New title for tetris').value).toBe('Blocks game');
  expect(input('New name for Mine').value).toBe('mine-api');
  expect(input('New title for Mine').value).toBe('Mine API');
  // checking Mine leaves tetris checked; unchecking tetris leaves Mine checked
  const sw = (name: string) => screen.getByRole('switch', { name }).getAttribute('aria-checked');
  fireEvent.click(screen.getByRole('switch', { name: 'Apply to Mine' }));
  expect([sw('Apply to tetris'), sw('Apply to Mine')]).toEqual(['true', 'true']);
  fireEvent.click(screen.getByRole('switch', { name: 'Apply to tetris' }));
  expect([sw('Apply to tetris'), sw('Apply to Mine')]).toEqual(['false', 'true']);
  expect(button('Apply selected (1)').disabled).toBe(false);
});

test('a duplicate name is rejected inline and blocks the apply; fixing it unblocks', async () => {
  await openWith();
  type('New name for tetris', 'cmx-9');   // the dispatched window's name, listed or not
  expect(screen.getByRole('alert').textContent).toBe('Another window is named cmx-9');
  expect(button('Apply selected (1)').disabled).toBe(true);
  expect(button('Apply all').disabled).toBe(true);
  fireEvent.click(button('Apply selected (1)'));
  type('New name for tetris', 'Bad Name');
  expect(screen.getByRole('alert').textContent).toBe("Name must be 1-20 lowercase letters, digits or '-'");
  type('New name for tetris', 'tipstack');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(button('Apply selected (1)').disabled).toBe(false);
  expect(pending).toEqual([]);
});

test('edit then apply: the name through the rename API, the title through the title API; each row reports', async () => {
  await openWith();
  type('New title for tetris', 'Tipstack: billing v2');
  fireEvent.click(button('Apply selected (1)'));
  await answerAll({
    '/api/agents/%401/rename': { ok: true, wid: '@1', name: 'tipstack' },
    '/api/agents/%401/title': { ok: true, state: 'pending', reason: 'busy' },
  });
  expect(done.slice(1).map(c => [c.url, c.body])).toEqual([
    ['/api/agents/%401/rename', { name: 'tipstack' }],
    ['/api/agents/%401/title', { title: 'Tipstack: billing v2' }],
  ]);
  expect(screen.getByText('Renamed · Title pending (busy)')).toBeTruthy();
  // the row now reads its new name, and its title as pending; the manual row was not touched
  expect(screen.getByRole('switch', { name: 'Apply to tipstack' }).getAttribute('aria-checked')).toBe('false');
  expect(screen.getAllByText('pending')).toHaveLength(2);
});

test('Apply all checks every row (the manual one too); a partial failure is shown on its row', async () => {
  await openWith();
  fireEvent.click(button('Apply all'));
  await answerAll({
    '/api/agents/%401/rename': { ok: true },
    '/api/agents/%401/title': { ok: true, state: 'applied', reason: null },
    '/api/agents/%402/rename': { ok: false, error: 'another window is already named mine-api' },
    '/api/agents/%402/title': { ok: false, state: 'refused', reason: 'no Claude session runs in this pane' },
  });
  expect(done.slice(1).map(c => c.url)).toEqual([
    '/api/agents/%401/rename', '/api/agents/%401/title', '/api/agents/%402/rename', '/api/agents/%402/title']);
  expect(screen.getByText('Renamed · Title set')).toBeTruthy();
  const failed = screen.getByText('Name failed: another window is already named mine-api · Title failed: no Claude session runs in this pane');
  expect(failed.className).toBe('sn-result sn-failed');
  // the failed row stays checked, for a retry
  expect(screen.getByRole('switch', { name: 'Apply to Mine' }).getAttribute('aria-checked')).toBe('true');
});

test('a failed suggestion read says so; the server\'s model error keeps the rows editable', async () => {
  render(<SuggestNames />);
  act(() => openSuggestNames());
  const p = pending.shift();
  await act(async () => {
    p?.reject(new Error('down'));
  });
  expect(screen.getByRole('alert').textContent).toBe('Could not read the sessions.');
  expect(button('Apply all').disabled).toBe(true);
  act(() => closeSuggestNames());
  act(() => openSuggestNames());
  await answer(SUGGEST, { ok: false, error: 'wids must be a list of window ids' });
  expect(screen.getByRole('alert').textContent).toBe('wids must be a list of window ids');
  act(() => closeSuggestNames());
  act(() => openSuggestNames());
  await answer(SUGGEST, { ok: true, rows: [{ ...ROWS[0], note: 'suggestion failed', suggested_name: 'tetris', suggested_title: 'Tetris-style blocks' }], error: 'model call failed' });
  expect(screen.getByRole('alert').textContent).toBe('No suggestions: model call failed. Edit the rows by hand.');
  expect(screen.getByText('suggestion failed')).toBeTruthy();
  expect(input('New name for tetris').value).toBe('tetris');
});
