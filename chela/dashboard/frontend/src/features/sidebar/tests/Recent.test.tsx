// CMX-278: Recent sessions, ported from tests/sidebar_recent_sessions.test.mjs (CMX-208,
// CMX-437, CMX-11, CMX-156), plus Liav's 10-19 rows: a session live in a window of this epoch
// is never listed (matched by SESSION ID, never by window address), and a judge's session sits
// under the dispatcher toggle with no Resume.
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { Recent } from '../index.ts';
import { type AgentRow, type RecentRow, type RecentState, dispatcherToggleLabel, recentView } from '../model.ts';
import { actions, win } from './fixtures.ts';

afterEach(cleanup);

const ROW: RecentRow = {
  store: 'session-ids', wid: '@5', session_id: 'bbbbbbbb-1111-2222-3333-444444444444',
  cwd: '/home/u/projects/five', label: 'five', stamped_epoch: '786-1784045825', dismiss_key: 'bbbbbbbb-1111-2222-3333-444444444444',
};
const OTHER: RecentRow = { ...ROW, wid: '@6', session_id: 'dddddddd-1111-2222-3333-444444444444', label: 'six', dismiss_key: 'dddddddd-1111-2222-3333-444444444444' };
// A dispatcher-owned row: no session id, dismissed by its address key (CMX-11).
const DISP: RecentRow = {
  store: 'session-ids', wid: '@138', cwd: '/home/u/.chela/worktrees/chelamux/judge-cmx-206', label: 'judge-cmx-206',
  stamped_epoch: '786-1784045825', dismiss_key: 'row:session-ids|@138|786-1784045825',
};
// The 10-19 shape: a judge's session the server still offered as resumable.
const JUDGE: RecentRow = { ...ROW, wid: '@19', session_id: 'eeeeeeee-1111-2222-3333-444444444444', label: 'judge-liavacc/cmx-274', dismiss_key: 'eeeeeeee-1111-2222-3333-444444444444' };

function recent(over: Partial<RecentState> = {}): RecentState {
  return { rows: [], dispatcher_rows: [], revealed: false, undo: null, ...over };
}

function draw(st: RecentState, agents: AgentRow[] = [], acts = actions()) {
  const { container } = render(<Recent recent={st} agents={agents} actions={acts} />);
  return { el: container, acts };
}

const resumeBtns = (el: HTMLElement) => [...el.querySelectorAll<HTMLButtonElement>('.recent-resume')];
const names = (el: HTMLElement, sel = '.recent-row') =>
  [...el.querySelectorAll(`${sel} .agent-row-name`)].map(n => n.textContent);
const toggle = (el: HTMLElement) => el.querySelector<HTMLButtonElement>('.recent-toggle-dispatcher');

// --- what the section shows ------------------------------------------------------------

test('nothing to resume: no section at all', () => {
  expect(draw(recent()).el.innerHTML).toBe('');
});

test('a resumable row: its label, cwd, a Resume and a ×; the count; the label falls back to the cwd', () => {
  const { el } = draw(recent({ rows: [ROW, { ...OTHER, label: '' }] }));
  expect(el.querySelector('#side-recent-section')).toBeTruthy();
  expect(names(el)).toEqual(['five', OTHER.cwd]);
  const row = el.querySelector('.recent-row') as HTMLElement;
  expect(row.textContent).toContain(ROW.cwd);
  expect(resumeBtns(el).map(b => [b.dataset.wid, b.dataset.session])).toEqual([['@5', ROW.session_id], ['@6', OTHER.session_id]]);
  expect(row.querySelector('.recent-dismiss')?.getAttribute('data-dismiss')).toBe(ROW.session_id);
  expect(el.querySelector('#hdr-recent')?.textContent).toBe('2');
});

test('an untrusted label is text, never markup', () => {
  const { el } = draw(recent({ rows: [{ ...ROW, label: '<img src=x>' }] }));
  expect(el.querySelector('.agent-row-name')?.textContent).toBe('<img src=x>');
  expect(el.querySelector('img')).toBeNull();
});

test('10-19: a recent row whose SESSION is live now is omitted; the same WINDOW address running another session is not', () => {
  // The resumed session runs at a NEW address (@15) in this epoch: matched by its id, it is gone.
  const resumed = win('cmx-251', { window_id: '@15', session_id: ROW.session_id });
  expect(names(draw(recent({ rows: [ROW, OTHER] }), [resumed]).el)).toEqual(['six']);
  cleanup();
  // Negative control: @5 (the dead row's own address) now runs a DIFFERENT session. A match by
  // window address would drop the row; it is still recent.
  const recycled = win('other', { window_id: '@5', session_id: 'ffffffff-1111-2222-3333-444444444444' });
  expect(names(draw(recent({ rows: [ROW, OTHER] }), [recycled]).el)).toEqual(['five', 'six']);
});

test('recentView: live by session id only; a judge row joins the hidden dispatcher rows', () => {
  const v = recentView(recent({ rows: [ROW, OTHER, JUDGE], dispatcher_rows: [DISP] }), new Set([OTHER.session_id ?? '']));
  expect(v.resumable.map(r => r.label)).toEqual(['five']);
  expect(v.hidden.map(r => r.label)).toEqual(['judge-liavacc/cmx-274', 'judge-cmx-206']);
  expect(v.relaunch).toEqual([]);
  // a live JUDGE session leaves the list entirely, toggle included
  expect(recentView(recent({ rows: [JUDGE] }), new Set([JUDGE.session_id ?? ''])).hidden).toEqual([]);
  // no session id never matches the live set
  expect(recentView(recent({ dispatcher_rows: [DISP] }), new Set([''])).hidden).toEqual([DISP]);
});

test('10-19: a judge session sits under the dispatcher toggle, and has no Resume there either', () => {
  const st = recent({ rows: [ROW, JUDGE] });
  const { el } = draw(st);
  expect(names(el)).toEqual(['five']);
  expect(el.querySelector('#hdr-recent')?.textContent).toBe('1');
  expect(toggle(el)?.textContent).toBe('Show 1 hidden dispatcher session');
  cleanup();
  const open = draw({ ...st, revealed: true }).el;
  const judge = [...open.querySelectorAll('.recent-row-dispatcher')];
  expect(judge.map(r => r.querySelector('.agent-row-name')?.textContent)).toEqual(['judge-liavacc/cmx-274']);
  expect(judge[0]?.querySelector('.recent-resume')).toBeNull();
  expect(judge[0]?.querySelector('.recent-dismiss')).toBeTruthy();
  expect(resumeBtns(open).map(b => b.dataset.session)).toEqual([ROW.session_id]);
});

test('dispatcher rows: hidden behind the toggle by default, revealed with NO Resume; the toggle copy', () => {
  const st = recent({ rows: [ROW], dispatcher_rows: [DISP, { ...DISP, wid: '@139', dismiss_key: 'row:b' }] });
  const { el, acts } = draw(st);
  expect(el.querySelectorAll('.recent-row-dispatcher')).toHaveLength(0);
  expect(toggle(el)?.getAttribute('aria-expanded')).toBe('false');
  fireEvent.click(toggle(el) as HTMLElement);
  expect(acts.toggleDispatcherSessions).toHaveBeenCalledTimes(1);
  cleanup();
  const open = draw({ ...st, revealed: true }).el;
  expect(open.querySelectorAll('.recent-row-dispatcher')).toHaveLength(2);
  expect(open.querySelectorAll('.recent-row-dispatcher .recent-resume')).toHaveLength(0);
  expect(toggle(open)?.textContent).toBe('Hide dispatcher sessions');
  expect([1, 2].map(n => dispatcherToggleLabel(n, false)))
    .toEqual(['Show 1 hidden dispatcher session', 'Show 2 hidden dispatcher sessions']);
});

test('only dispatcher rows: the section still shows (for the toggle), counting 0 resumable', () => {
  const { el } = draw(recent({ dispatcher_rows: [DISP] }));
  expect(el.querySelector('#hdr-recent')?.textContent).toBe('0');
  expect(toggle(el)).toBeTruthy();
});

test('CMX-156: a run the dispatcher will relaunch shows read-only OUTSIDE the toggle, saying why', () => {
  const relaunch = { ...DISP, wid: '@7', relaunch: 'CMX-137', dismiss_key: 'row:r' };
  const { el } = draw(recent({ dispatcher_rows: [relaunch, DISP] }));
  const shown = [...el.querySelectorAll('.recent-row-dispatcher')];
  expect(shown).toHaveLength(1);
  expect(shown[0]?.querySelector('.ar-recap')?.textContent).toBe('dispatcher will relaunch CMX-137');
  expect(shown[0]?.querySelector('.recent-resume')).toBeNull();
  expect(toggle(el)?.textContent).toBe('Show 1 hidden dispatcher session');
});

// --- what a click calls ------------------------------------------------------------------

test('Resume calls resumeSession with the row, once, however fast the second click', async () => {
  let done: (ok: boolean) => void = () => {};
  const acts = actions();
  acts.resumeSession.mockImplementation(() => new Promise<boolean>(r => { done = r; }));
  const { el } = draw(recent({ rows: [ROW] }), [], acts);
  const btn = resumeBtns(el)[0] as HTMLButtonElement;
  fireEvent.click(btn);
  fireEvent.click(btn);
  expect(acts.resumeSession).toHaveBeenCalledTimes(1);
  expect(acts.resumeSession).toHaveBeenCalledWith(ROW);
  expect(btn.disabled).toBe(true);
  expect(btn.textContent).toBe('Resuming…');
  await act(async () => { done(true); await Promise.resolve(); });
  expect(btn.disabled).toBe(true); // a resumed row stays spent until the list re-reads
});

test('a failed resume brings the button back', async () => {
  const acts = actions();
  acts.resumeSession.mockResolvedValue(false);
  const { el } = draw(recent({ rows: [ROW] }), [], acts);
  const btn = resumeBtns(el)[0] as HTMLButtonElement;
  await act(async () => { fireEvent.click(btn); await Promise.resolve(); });
  expect(btn.disabled).toBe(false);
  expect(btn.textContent).toBe('Resume');
  fireEvent.click(btn);
  expect(acts.resumeSession).toHaveBeenCalledTimes(2);
});

test('a resume that did not report success (no handler, an unknown answer) brings the button back too', async () => {
  const acts = actions();
  acts.resumeSession.mockResolvedValue(undefined);
  const { el } = draw(recent({ rows: [ROW] }), [], acts);
  const btn = resumeBtns(el)[0] as HTMLButtonElement;
  await act(async () => { fireEvent.click(btn); await Promise.resolve(); });
  expect(btn.disabled).toBe(false);
});

test('a cancelled swipe puts the row back and does not dismiss', () => {
  const { el, acts } = draw(recent({ rows: [ROW] }));
  const row = el.querySelector('.recent-row') as HTMLElement;
  fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 100 }] });
  fireEvent.touchMove(row, { touches: [{ clientX: 120, clientY: 102 }] });
  expect(row.style.transform).toBe('translateX(-80px)');
  fireEvent.touchCancel(row);
  expect(row.style.transform).toBe('');
  fireEvent.touchEnd(row, { changedTouches: [{ clientX: 100, clientY: 100 }] });
  expect(acts.dismissRecentSession).not.toHaveBeenCalled();
});

test('× dismisses by the row key, a dispatcher row by its address key; nothing else is called', () => {
  const { el, acts } = draw(recent({ rows: [ROW], dispatcher_rows: [DISP], revealed: true }));
  const xs = [...el.querySelectorAll<HTMLElement>('.recent-dismiss')];
  expect(xs.map(x => x.getAttribute('aria-label'))).toEqual(['Dismiss five', 'Dismiss judge-cmx-206']);
  xs.forEach(x => fireEvent.click(x));
  expect(acts.dismissRecentSession.mock.calls).toEqual([[ROW.session_id], [DISP.dismiss_key]]);
  expect(acts.selectAgent).not.toHaveBeenCalled();
  expect(acts.resumeSession).not.toHaveBeenCalled();
});

test('a swipe left past 70px dismisses; a short or mostly vertical one does not', () => {
  const { el, acts } = draw(recent({ rows: [ROW] }));
  const row = el.querySelector('.recent-row') as HTMLElement;
  const swipe = (dx: number, dy: number) => {
    fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 100 }] });
    fireEvent.touchMove(row, { touches: [{ clientX: 200 + dx, clientY: 100 + dy }] });
    fireEvent.touchEnd(row, { changedTouches: [{ clientX: 200 + dx, clientY: 100 + dy }] });
  };
  swipe(-60, 0);
  swipe(-90, 60);
  expect(acts.dismissRecentSession).not.toHaveBeenCalled();
  swipe(-90, 10);
  expect(acts.dismissRecentSession.mock.calls).toEqual([[ROW.session_id]]);
  expect(row.style.transform).toBe('');
});

test('Clear all: offered only for two or more resumable rows', () => {
  expect(draw(recent({ rows: [ROW], dispatcher_rows: [DISP] })).el.querySelector('#recent-clear-all')).toBeNull();
  cleanup();
  const { el, acts } = draw(recent({ rows: [ROW, OTHER] }));
  fireEvent.click(el.querySelector('#recent-clear-all') as HTMLElement);
  expect(acts.clearRecentSessions).toHaveBeenCalledTimes(1);
});

test('the Undo toast: its label in .recent-undo-text (CMX-22), Undo brings the keys back, a click elsewhere closes it', () => {
  const st = recent({ rows: [OTHER], undo: { sids: [ROW.session_id ?? ''], text: 'Dismissed five' } });
  const { acts } = draw(st);
  const toast = document.querySelector('.recent-undo-toast') as HTMLElement;
  expect(toast.parentElement?.id).toBe('run-toast-stack');
  expect(toast.querySelector('.recent-undo-text')?.textContent).toBe('Dismissed five');
  fireEvent.click(toast.querySelector('.recent-undo') as HTMLElement);
  expect(acts.undoDismissRecent.mock.calls).toEqual([[[ROW.session_id]]]);
  expect(acts.closeRecentUndo).not.toHaveBeenCalled();
  fireEvent.click(toast);
  expect(acts.closeRecentUndo).toHaveBeenCalledTimes(1);
  cleanup();
  // the toast outlives the last row (the list went empty with the dismiss)
  draw({ ...st, rows: [] });
  expect(document.querySelector('.recent-undo-toast')).toBeTruthy();
});
