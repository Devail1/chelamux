// The per-session diff modal (CMX-299; ported from tests/diff_modal_wiring.test.mjs's modal
// half, CMX-237). The Files chip that opens it is still legacy: tests/diff_modal_wiring.test.mjs
// drives that click on the real page.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { DiffModal, closeDiffModal, openDiffModal } from '../index.ts';

const STATE = {
  is_git: true,
  has_head: true,
  files: [
    { path: 'a.py', status: 'modified', additions: 3, deletions: 1 },
    { path: 'b.py', status: 'added', additions: 5, deletions: 0 },
  ],
  additions: 8,
  deletions: 1,
};
const PATCH = ['diff --git a/a.py b/a.py', '--- a/a.py', '+++ b/a.py', '@@ -1,2 +1,2 @@', ' ctx', '-old', '+new'].join('\n') + '\n';

/** fetch, answered by hand: `answer(url, body)` resolves the pending call for `url`. */
let pending: { url: string; resolve: (body: unknown) => void; reject: (e: Error) => void }[] = [];
const urls = () => pending.map((p) => p.url);
async function answer(url: string, body: unknown) {
  const i = pending.findIndex((p) => p.url === url);
  if (i < 0) throw new Error(`no pending fetch for ${url}; pending: ${urls().join(', ')}`);
  const [p] = pending.splice(i, 1);
  await act(async () => {
    p?.resolve(body);
  });
}
async function fail(url: string) {
  const i = pending.findIndex((p) => p.url === url);
  const [p] = pending.splice(i, 1);
  await act(async () => {
    p?.reject(new Error('network down'));
  });
}

beforeEach(() => {
  pending = [];
  vi.stubGlobal('fetch', (url: string) =>
    new Promise((resolve, reject) => {
      pending.push({ url, resolve: (body) => resolve({ ok: true, json: () => Promise.resolve(body) }), reject });
    }),
  );
});
afterEach(() => {
  act(() => closeDiffModal());
  cleanup();
  vi.unstubAllGlobals();
});

const DIFF1 = '/api/agents/%401/diff';
const patchUrl = (wid: string, path: string) => `/api/agents/${encodeURIComponent(wid)}/diff/patch?path=${encodeURIComponent(path)}`;
const dialog = () => screen.queryByRole('dialog', { name: 'Changed files' });
const rows = () => [...document.querySelectorAll<HTMLElement>('.diff-file-row')];
const patchView = () => document.querySelector('.diff-patch-view');

async function openOn(wid = '@1', state: unknown = STATE) {
  render(<DiffModal />);
  act(() => openDiffModal(wid));
  await answer(`/api/agents/${encodeURIComponent(wid)}/diff`, state);
}

test('closed until a session is targeted; opening shows Loading, then the fetched list', async () => {
  render(<DiffModal />);
  expect(dialog()).toBeNull();
  act(() => openDiffModal(''));
  expect(dialog()).toBeNull();
  act(() => openDiffModal('@1'));
  expect(dialog()?.className).toBe('modal modal-diff');
  expect(dialog()?.textContent).toContain('Loading…');
  expect(urls()).toEqual([DIFF1]);
  await answer(DIFF1, STATE);
  expect(rows().map((r) => r.dataset.diffFile)).toEqual(['a.py', 'b.py']);
  expect(document.querySelector('.diff-modal-summary')?.textContent).toBe('2 files changed · +8 −1');
  expect(patchView()?.textContent).toBe('Select a file to view its diff.');
});

test('each row carries its OWN status glyph, title and +/- stat', async () => {
  await openOn();
  const [a, b] = rows();
  const chip = (r?: HTMLElement) => r?.querySelector('.diff-status-chip');
  expect(chip(a)?.className).toBe('diff-status-chip diff-status-modified');
  expect(chip(b)?.className).toBe('diff-status-chip diff-status-added');
  expect([chip(a)?.textContent, chip(b)?.textContent]).toEqual(['M', 'A']);
  expect([chip(a)?.getAttribute('title'), chip(b)?.getAttribute('title')]).toEqual(['modified', 'added']);
  expect([a, b].map((r) => r?.querySelector('.diff-file-stat')?.textContent)).toEqual(['+3 −1', '+5 −0']);
});

test('a click on a row (on its child) loads that file\'s patch, single-select', async () => {
  await openOn();
  const [a, b] = rows();
  fireEvent.click(a?.querySelector('.diff-file-path') as Element);
  expect(a?.classList.contains('active')).toBe(true);
  expect(patchView()?.textContent).toBe('Loading…');
  await answer(patchUrl('@1', 'a.py'), { ok: true, patch: PATCH });
  const view = patchView() as Element;
  const count = (cls: string) => view.querySelectorAll(`.${cls}`).length;
  expect([count('diff-line-meta'), count('diff-line-hunk'), count('diff-line-add'), count('diff-line-del'), count('diff-line-ctx')]).toEqual([2, 1, 1, 1, 2]);
  // The newline is BETWEEN lines: the copied text is the patch, minus its trailing newline.
  expect(view.querySelector('pre')?.textContent).toBe(PATCH.replace(/\n$/, ''));
  fireEvent.click(b?.querySelector('.diff-file-stat') as Element);
  expect(a?.classList.contains('active')).toBe(false);
  expect(b?.classList.contains('active')).toBe(true);
  expect(patchView()?.textContent).toBe('Loading…');
});

test('Enter and Space on a focused row load its patch too; other keys do not', async () => {
  await openOn();
  const [a, b] = rows();
  fireEvent.keyDown(a as Element, { key: 'x' });
  expect(urls()).toEqual([]);
  fireEvent.keyDown(a as Element, { key: 'Enter' });
  fireEvent.keyDown(b as Element, { key: ' ' });
  expect(urls()).toEqual([patchUrl('@1', 'a.py'), patchUrl('@1', 'b.py')]);
});

test('a late patch for the previous file never overwrites the current one', async () => {
  await openOn();
  const [a, b] = rows();
  fireEvent.click(a as Element);
  fireEvent.click(b as Element);
  await answer(patchUrl('@1', 'b.py'), { ok: true, patch: '+b only\n' });
  await answer(patchUrl('@1', 'a.py'), { ok: true, patch: '+a stale\n' });
  expect(patchView()?.textContent).toBe('+b only');
});

test('a late /diff for a closed session never renders into the next one', async () => {
  render(<DiffModal />);
  act(() => openDiffModal('@1'));
  act(() => closeDiffModal());
  act(() => openDiffModal('@2'));
  await answer(DIFF1, STATE);
  expect(rows()).toEqual([]);
  expect(dialog()?.textContent).toContain('Loading…');
  await answer('/api/agents/%402/diff', { ...STATE, files: [{ path: 'z.py', status: 'deleted', additions: 0, deletions: 9 }] });
  expect(rows().map((r) => r.dataset.diffFile)).toEqual(['z.py']);
});

test('opening another session clears the previous list before its fetch resolves', async () => {
  await openOn();
  act(() => openDiffModal('@2'));
  expect(rows()).toEqual([]);
  expect(dialog()?.textContent).toContain('Loading…');
});

test('a late patch after the modal closed and reopened on the same session is dropped', async () => {
  await openOn();
  fireEvent.click(rows()[0] as Element);
  act(() => closeDiffModal());
  act(() => openDiffModal('@1'));
  await answer(DIFF1, STATE);
  await answer(patchUrl('@1', 'a.py'), { ok: true, patch: '+stale\n' });
  expect(patchView()?.textContent).toBe('Select a file to view its diff.');
});

test('Escape closes it', async () => {
  await openOn();
  fireEvent.keyDown(dialog() as Element, { key: 'Escape' });
  await act(async () => {});
  expect(dialog()).toBeNull();
});

test('the close button closes it, and is named', async () => {
  await openOn();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await act(async () => {});
  expect(dialog()).toBeNull();
});

test('a press on the backdrop closes it; one inside the dialog does not', async () => {
  await openOn();
  // Radix arms its outside-press listener on the next macrotask after opening.
  await act(() => new Promise((r) => setTimeout(r, 0)));
  // A primary-button press is judged on its click (Radix defers it), as a real one lands.
  const press = async (el: Element) => {
    fireEvent.pointerDown(el, { button: 0 });
    fireEvent.click(el);
    await act(async () => {});
  };
  await press(dialog() as Element);
  expect(dialog()).not.toBeNull();
  await press(dialog()?.parentElement as Element);
  expect(dialog()).toBeNull();
});

test('a path with HTML in it is text, not markup', async () => {
  await openOn('@1', { ...STATE, files: [{ path: '<img src=x>.py', status: 'added', additions: 1, deletions: 0 }] });
  expect(document.querySelector('.diff-file-path')?.textContent).toBe('<img src=x>.py');
  expect(document.querySelector('.diff-file-row img')).toBeNull();
  fireEvent.click(rows()[0] as Element);
  await answer(patchUrl('@1', '<img src=x>.py'), { ok: true, patch: '+<b>bold</b>\n' });
  expect(patchView()?.querySelector('b')).toBeNull();
  expect(patchView()?.textContent).toBe('+<b>bold</b>');
});

test('a clean worktree says "Nothing to show."; an empty patch says so too', async () => {
  await openOn('@1', { is_git: true, has_head: true, files: [] });
  expect(document.querySelector('.diff-file-list-empty')?.textContent).toBe('Nothing to show.');
  expect(document.querySelector('.diff-modal-summary')?.textContent).toBe('No changes');
  act(() => openDiffModal('@1'));
  await answer(DIFF1, STATE);
  fireEvent.click(rows()[0] as Element);
  await answer(patchUrl('@1', 'a.py'), { ok: true, patch: '' });
  expect(patchView()?.textContent).toBe('No diff text for this file.');
});

test('a refused patch shows the server\'s own reason, else a generic one; a failed read too', async () => {
  await openOn();
  const [a, b] = rows();
  fireEvent.click(a as Element);
  await answer(patchUrl('@1', 'a.py'), { ok: false, error: 'path is outside the worktree' });
  expect(patchView()?.textContent).toBe('path is outside the worktree');
  fireEvent.click(b as Element);
  await answer(patchUrl('@1', 'b.py'), { ok: false });
  expect(patchView()?.textContent).toBe('Could not load diff.');
  fireEvent.click(a as Element);
  await answer(patchUrl('@1', 'a.py'), null);
  expect(patchView()?.textContent).toBe('Could not load diff.');
  fireEvent.click(b as Element);
  await fail(patchUrl('@1', 'b.py'));
  expect(patchView()?.textContent).toBe('Could not load diff.');
});

test('a failed /diff read says so: unknown never reads as "not a git repository"', async () => {
  render(<DiffModal />);
  act(() => openDiffModal('@1'));
  await fail(DIFF1);
  expect(dialog()?.textContent).toContain('Could not load the changed files.');
  expect(dialog()?.textContent).not.toContain('Not a git repository');
  expect(dialog()?.textContent).not.toContain('Loading…');
});

test('a null /diff body is unknown too, not "not a git repository"', async () => {
  await openOn('@1', null);
  expect(dialog()?.textContent).toContain('Could not load the changed files.');
  expect(document.querySelector('.diff-modal-summary')).toBeNull();
});
