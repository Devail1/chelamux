// CMX-242 (CMX-212 P27): a pane's footer. Ported from the footer halves of window_id
// (the id chip and its copy), wallnav (branch, context, model, cost, PR chip, action bar),
// em_dash_rewrite_branches (the tooltip) and dashboard_scale_nav_a11y (the context chip's
// non-hue text, the footer's completeness) .test.mjs.
import { act, fireEvent, render } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import type { ContextRow } from '../../../shared/store/context.ts';
import { DiffModal } from '../../diffmodal/index.ts';
import type { HeadAgent } from '../../panehead/index.ts';
import { TileFoot, type TileFootProps } from '../index.ts';
import { must, settle, stubFetch } from './setup.ts';

const AGENT: HeadAgent = { window_id: '@32', name: 'w', session_status: 'idle', claude_running: true };
const READING: ContextRow = { window_id: '@32', used_pct: 74, used: '147.5K', total: '1M', model: 'opus-5', cost_usd: 12.34, branch: 'cmx-242' };
const PR = { url: 'https://example.invalid/pull/613', number: 613, repository: 'acme/widgets', state: 'open', draft: false };

/** One footer inside a pane, with a header beside it, as terminals.js builds them. */
function foot(over: Partial<TileFootProps> = {}) {
  const props: TileFootProps = { wid: '@32', wall: true, agent: AGENT, ctx: READING, ...over };
  const pane = document.createElement('div');
  pane.className = props.wall ? 'grid-stack-item-content' : 'term-pane';
  pane.innerHTML = '<div class="gs-head"></div>';
  const slot = document.createElement('div');
  pane.append(slot);
  document.body.append(pane);
  const r = render(<TileFoot {...props} />, { container: slot });
  const q = (sel: string) => pane.querySelector<HTMLElement>(sel);
  return { ...r, pane, q, again: (o: Partial<TileFootProps>) => r.rerender(<TileFoot {...props} {...o} />) };
}

const shown = (el: HTMLElement | null) => !!el && !el.hidden;
const text = (el: Element | null) => (el?.textContent ?? '').trim();

// --- what the bar carries ------------------------------------------------------------

test('a wall footer is complete: id, model, PR, cost, branch, context % with tokens, Files, fill', () => {
  const { q } = foot({ agent: { ...AGENT, pr: PR } });
  expect(text(q('.gs-wid'))).toBe('@32');
  expect(text(q('.gs-model'))).toBe('opus-5');
  expect(shown(q('.gs-pr'))).toBe(true);
  expect(text(q('.gs-cost'))).toBe('$12.34');
  expect(text(q('.gs-branch'))).toBe('cmx-242');
  expect(q('.gs-branch svg.gs-branch-icon')).not.toBeNull();
  expect(text(q('.gs-ctx'))).toBe('74% · 147.5K/1M');
  expect(must(q('.gs-files')).getAttribute('title')).toBe('Changed files');
  expect(q('.gs-files svg')).not.toBeNull();
  expect(must(q('.term-ctx-fill')).style.width).toBe('74%');
  for (const sel of ['.gs-model', '.gs-cost', '.gs-branch', '.gs-ctx']) expect(shown(q(sel)), sel).toBe(true);
});

test('the single pane carries no PR, cost or action bar, and the shared chips all', () => {
  const { q } = foot({ wall: false, agent: { ...AGENT, pr: PR, session_status: 'waiting' } });
  expect(q('.gs-pr')).toBeNull();
  expect(q('.gs-cost')).toBeNull();
  expect(q('.term-action-bar')).toBeNull();
  for (const sel of ['.gs-wid', '.gs-model', '.gs-branch', '.gs-ctx', '.gs-files', '.term-ctx-fill']) expect(q(sel), sel).not.toBeNull();
  expect(text(q('.gs-ctx'))).toBe('74% · 147.5K/1M');
});

test('no reading: every reading chip hides, the fill is empty and the tooltip says so', () => {
  const { q } = foot({ ctx: undefined });
  for (const sel of ['.gs-model', '.gs-cost', '.gs-branch', '.gs-ctx']) expect(shown(q(sel)), sel).toBe(false);
  expect(must(q('.term-ctx-fill')).style.width).toBe('0px');
  expect(must(q('.term-ctx-bar')).title).toBe('Context: --');
  // the id and Files chips need no reading
  expect(shown(q('.gs-wid'))).toBe(true);
  expect(shown(q('.gs-files'))).toBe(true);
});

test('a row with no used_pct is no reading either', () => {
  const { q } = foot({ ctx: { window_id: '@32', model: 'opus-5' } });
  expect(shown(q('.gs-model'))).toBe(false);
  expect(must(q('.term-ctx-bar')).title).toBe('Context: --');
});

test('a reading without a model, cost or branch hides just those chips', () => {
  const { q } = foot({ ctx: { window_id: '@32', used_pct: 10, used: '1K', total: '10K' } });
  expect(shown(q('.gs-ctx'))).toBe(true);
  for (const sel of ['.gs-model', '.gs-cost', '.gs-branch']) expect(shown(q(sel)), sel).toBe(false);
  expect(q('.gs-branch svg')).toBeNull();
});

test('the tooltip names the reading, the model and the cost; an estimate says so and dims', () => {
  const { q, again } = foot();
  expect(must(q('.term-ctx-bar')).title).toBe('Context: 147.5K/1M (74%) · opus-5 · $12.34');
  expect(must(q('.gs-head')).title).toBe('Context: 147.5K/1M (74%) · opus-5 · $12.34');
  again({ ctx: undefined });
  expect(must(q('.gs-head')).title).toBe('');   // a reading that went away leaves no stale tooltip
  again({ ctx: { window_id: '@32', used_pct: 25, used: '50k', total: '200k', estimated: true } });
  expect(must(q('.term-ctx-bar')).title).toBe('Context: 50k/200k (25%~) · estimate; run `chela install-statusline` for exact');
  expect(text(q('.gs-ctx'))).toBe('25%~ · 50k/200k');
  expect(must(q('.term-ctx-fill')).classList.contains('est')).toBe(true);
});

test('the chip shows the bare % when the token counts are missing', () => {
  const { q } = foot({ ctx: { window_id: '@32', used_pct: 30 } });
  expect(text(q('.gs-ctx'))).toBe('30%');
});

test('a zero cost is a real cost, never a hidden one', () => {
  const { q } = foot({ ctx: { ...READING, cost_usd: 0 } });
  expect(text(q('.gs-cost'))).toBe('$0.00');
});

test('the fill never runs past the bar', () => {
  const { q } = foot({ ctx: { ...READING, used_pct: 130 } });
  expect(must(q('.term-ctx-fill')).style.width).toBe('100%');
});

// The number carries the threshold; the class only reinforces it (never colour alone).
test.each([
  [60, ''], [61, 'ctx-warn'], [80, 'ctx-warn'], [81, 'ctx-danger'],
])('at %i%% the chip and the fill carry %j, beside the number', (pct, cls) => {
  const { q } = foot({ ctx: { ...READING, used_pct: pct } });
  const chip = must(q('.gs-ctx'));
  expect(text(chip)).toBe(`${pct}% · 147.5K/1M`);
  for (const c of ['ctx-warn', 'ctx-danger']) {
    expect(chip.classList.contains(c), c).toBe(c === cls);
    expect(must(q('.term-ctx-fill')).classList.contains(c), c).toBe(c === cls);
  }
});

test('a window id that is not `@N` gets no id chip', () => {
  const { q } = foot({ wid: 'shell-1' });
  expect(q('.gs-wid')).toBeNull();
});

// --- the PR chip (CMX-41) ----------------------------------------------------------

test.each([
  [{ state: 'open', draft: false }, '#613', true, false],
  [{ state: 'open', draft: true }, '#613 draft', false, true],
  [{ state: 'merged' }, '#613 merged', false, true],
  [{ state: 'closed' }, '#613 closed', false, true],
  [{ state: 'unknown' }, '#613', false, false],
])('a PR %j reads %j (flag %j, dim %j)', (over, label, flag, dim) => {
  const { q } = foot({ agent: { ...AGENT, pr: { ...PR, ...over } } });
  const chip = must(q('.gs-pr'));
  expect(chip.hidden).toBe(false);
  expect(chip.getAttribute('href')).toBe(PR.url);
  expect(text(chip)).toBe(label);
  expect(!!chip.querySelector('.gs-pr-flag')).toBe(flag);
  expect(chip.classList.contains('gs-pr-dim')).toBe(dim);
  expect(chip.title).toBe(`Open PR ${label} (acme/widgets)${over.state === 'unknown' ? ' (state unknown)' : ''}`);
});

test('no PR: the chip hides and links nowhere', () => {
  const { q } = foot();
  const chip = must(q('.gs-pr'));
  expect(chip.hidden).toBe(true);
  expect(chip.hasAttribute('href')).toBe(false);
  expect(text(chip)).toBe('');
});

// --- the action bar --------------------------------------------------------------------

test('a pane waiting on a question gets the Answer bar; a permission gate gets Approve', () => {
  const { q, pane, again } = foot({ agent: { ...AGENT, session_status: 'waiting' } });
  const bar = must(q('.term-action-bar'));
  expect(bar.hidden).toBe(false);
  expect(bar.className).toBe('term-action-bar action-answer');
  expect(text(bar.querySelector('.term-action-label'))).toBe('Needs an answer');
  expect(text(bar.querySelector('.term-action-btn'))).toBe('Answer');
  expect(pane.classList.contains('has-term-action')).toBe(true);
  again({ agent: { ...AGENT, session_status: 'busy', needs_human: true } });
  expect(bar.className).toBe('term-action-bar action-approve');
  expect(text(bar.querySelector('.term-action-label'))).toBe('Needs approval');
  expect(text(bar.querySelector('.term-action-btn'))).toBe('Approve');
});

test('Answer sends Enter to THIS pane, through the key path, and is disabled while it does', async () => {
  const calls = stubFetch({ ok: true });
  const { q } = foot({ agent: { ...AGENT, session_status: 'waiting' } });
  const btn = must(q('.term-action-btn')) as HTMLButtonElement;
  fireEvent.click(btn);
  expect(btn.disabled).toBe(true);
  await settle();
  expect(calls).toEqual([{ url: '/api/term/key', method: 'POST', body: { agent: '@32', key: 'Enter' } }]);
  expect(btn.disabled).toBe(false);
});

test('a key send that fails still gives the button back', async () => {
  vi.stubGlobal('fetch', () => Promise.reject(new Error('down')));
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  const { q } = foot({ agent: { ...AGENT, session_status: 'waiting' } });
  const btn = must(q('.term-action-btn')) as HTMLButtonElement;
  fireEvent.click(btn);
  await settle();
  expect(btn.disabled).toBe(false);
  expect(err).toHaveBeenCalled();
});

test('a finished pane with a reviewable PR gets the Review bar, linking the PR', () => {
  const { q } = foot({ agent: { ...AGENT, pr: PR } });
  const bar = must(q('.term-action-bar'));
  expect(bar.className).toBe('term-action-bar action-review');
  expect(text(bar.querySelector('.term-action-label'))).toBe('PR ready for review');
  expect(must(bar.querySelector('a.term-action-btn')).getAttribute('href')).toBe(PR.url);
});

test('no action: the bar hides and the pane gives the terminal its room back', () => {
  const { q, pane, again } = foot({ agent: { ...AGENT, session_status: 'waiting' } });
  expect(pane.classList.contains('has-term-action')).toBe(true);
  again({ agent: AGENT });
  expect(must(q('.term-action-bar')).hidden).toBe(true);
  expect(text(q('.term-action-bar'))).toBe('');
  expect(pane.classList.contains('has-term-action')).toBe(false);
});

test('the pane wears term-waiting exactly while it wants a human, on a wall tile and a single pane', () => {
  for (const wall of [true, false]) {
    const { pane, again, unmount } = foot({ wall, agent: { ...AGENT, needs_human: true } });
    expect(pane.classList.contains('term-waiting'), String(wall)).toBe(true);
    again({ agent: AGENT });
    expect(pane.classList.contains('term-waiting'), String(wall)).toBe(false);
    unmount();
  }
});

// --- the id chip copies itself (CMX-417) ------------------------------------------------

test('a click copies the id and says so, for 1.5 s', async () => {
  const copied: string[] = [];
  vi.stubGlobal('navigator', { clipboard: { writeText: (t: string) => { copied.push(t); return Promise.resolve(); } } });
  const { q } = foot();
  fireEvent.click(must(q('.gs-wid')));
  await settle();
  expect(copied).toEqual(['@32']);
  const toast = must(q('.gs-wid-toast'));
  expect(text(toast)).toBe('Copied @32');
  expect(toast.getAttribute('role')).toBe('status');
  vi.useFakeTimers();
  fireEvent.click(must(q('.gs-wid')));
  await act(async () => { await Promise.resolve(); });
  await act(() => vi.advanceTimersByTimeAsync(1499));
  expect(q('.gs-wid-toast')).not.toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(2));
  expect(q('.gs-wid-toast')).toBeNull();
});

function legacyCopy(result: boolean | 'throws') {
  const runs: string[] = [];
  document.execCommand = ((cmd: string) => {
    if (result === 'throws') throw new Error('unsupported');
    runs.push(`${cmd}:${document.querySelector('textarea')?.value ?? ''}`);
    return result;
  }) as typeof document.execCommand;
  return runs;
}

test('no clipboard API: it copies through a selected textarea, and leaves none behind', async () => {
  vi.stubGlobal('navigator', {});
  const runs = legacyCopy(true);
  const { q } = foot();
  fireEvent.click(must(q('.gs-wid')));
  await settle();
  expect(runs).toEqual(['copy:@32']);
  expect(document.querySelectorAll('textarea').length).toBe(0);
  expect(text(q('.gs-wid-toast'))).toBe('Copied @32');
});

test('a clipboard that refuses falls back; a fallback that fails says so, never "Copied"', async () => {
  vi.stubGlobal('navigator', { clipboard: { writeText: () => Promise.reject(new Error('denied')) } });
  legacyCopy(false);
  const { q } = foot();
  fireEvent.click(must(q('.gs-wid')));
  await settle();
  expect(text(q('.gs-wid-toast'))).toBe('Copy failed: @32');
  legacyCopy('throws');
  fireEvent.click(must(q('.gs-wid')));
  await settle();
  expect(text(q('.gs-wid-toast'))).toBe('Copy failed: @32');
});

// --- the Files chip (CMX-299) ------------------------------------------------------------

test('the Files chip opens the diff modal on THIS pane', async () => {
  const calls = stubFetch({ is_git: true, has_head: true, files: [], additions: 0, deletions: 0 });
  render(<DiffModal />);   // the island the page mounts
  const { q } = foot();
  fireEvent.click(must(q('.gs-files')));
  await settle();
  expect(calls.map((c) => c.url)).toEqual(['/api/agents/%4032/diff']);
  expect(document.querySelector('[role="dialog"].modal-diff')).not.toBeNull();
});
