// CMX-109 / CMX-393: the gear menu (ported from tests/topbarmenu.test.mjs's behaviour half by
// CMX-219; that file keeps the page half). ONE trigger opens ONE menu: Jump to (⌘K), New...
// (the launch menu, at the gear), Share current session, Notifications, Settings, then the
// readouts. The safety kill switch is never inside it. Plus the readouts' own rules
// (agents.js / header.js before): the account-wide 5h / 7d usage from the freshest sample.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { context, loadContext, type ContextRow } from '../../../shared/store/context.ts';
import { LaunchMenu, closeLaunchMenu } from '../../launcher/index.ts';
import { closePalette, palette } from '../../palette/index.ts';
import { closeSettings, selectSettingsTab, settings } from '../../settings/index.ts';
import { PrimaryMenu, SessionsCount, refreshSummary, summary } from '../index.ts';
import { RL_STALE_MS, agentsCount, readingAge, rlReading, rlResetTooltip, soonest } from '../model.ts';

const NOW = Date.parse('2026-10-21T12:00:00Z');

beforeEach(() => {
  context.set([]);
  summary.set(null);
  vi.stubGlobal('fetch', () => Promise.resolve({ json: () => Promise.resolve({}) }));
  render(<><PrimaryMenu /><LaunchMenu /><button id="btn-shares" type="button">0 sharing</button></>);
});
afterEach(() => {
  act(() => { closeLaunchMenu(); closePalette(); closeSettings(); });
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const gear = () => document.getElementById('btn-primary-menu') as HTMLButtonElement;
const menu = () => document.querySelector('[role="menu"]') as HTMLElement | null;
const items = () => [...document.querySelectorAll<HTMLElement>('[role="menu"] [role="menuitem"]')];
const item = (label: string) => {
  const el = items().find((i) => i.querySelector('.ov-label')?.textContent === label);
  if (!el) throw new Error(`no "${label}"`);
  return el;
};
const open = () => fireEvent.keyDown(gear(), { key: 'Enter' });
const pick = (label: string) => fireEvent.keyDown(item(label), { key: 'Enter' });

test('ONE trigger, a real button named for what it does, opens ONE menu with every folded action', () => {
  expect(gear().tagName).toBe('BUTTON');
  expect(gear().getAttribute('aria-label')).toBe('Settings & menu');
  expect(gear().getAttribute('aria-haspopup')).toBe('menu');
  open();
  expect(screen.getByRole('menu', { name: 'Settings & menu' })).toBe(menu()); // named by its trigger
  expect(items().map((i) => i.querySelector('.ov-label')?.textContent)).toEqual(['Jump to…', 'New…', 'Share current session', 'Notifications', 'Settings']);
  expect(item('Jump to…').querySelector('.ov-val')?.textContent).toBe('⌘K');
  expect(items().every((i) => i.querySelector('.ov-ic svg'))).toBe(true);
});

test('the safety kill switch is never inside the menu', () => {
  open();
  expect(menu()?.contains(document.getElementById('btn-shares'))).toBe(false);
  expect(menu()?.textContent).not.toMatch(/sharing/);
});

test('Share current session with no pane open falls back to the palette and closes the menu', () => {
  open();
  pick('Share current session');
  expect(palette.get()).toMatchObject({ open: true });
  expect(menu()).toBeNull();
});

test('Jump to… opens the palette, empty, and closes the menu', () => {
  open();
  pick('Jump to…');
  expect(palette.get()).toMatchObject({ open: true, query: '', fromSidebar: false });
  expect(menu()).toBeNull();
});

test.each([
  ['Notifications', 'notifications'],
  ['Settings', 'timing'],
] as const)('%s opens Settings on the %s tab and closes the menu', (label, tab) => {
  act(() => selectSettingsTab('timing'));
  open();
  pick(label);
  expect(settings.get()).toMatchObject({ open: true, tab });
  expect(menu()).toBeNull();
});

test('a legacy surface not loaded yet makes a pick a no-op, not a throw', () => {
  (window as unknown as { chela?: object }).chela = undefined;
  open();
  expect(() => pick('Share current session')).not.toThrow();
});

test('New… closes the menu and opens the launch menu at the gear', async () => {
  open();
  pick('New…');
  expect(menu()).toBeNull();
  await act(() => new Promise((r) => setTimeout(r, 10)));
  expect(document.querySelector('.popover.launch-menu')).not.toBeNull();
});

test('the readouts: usage only once sampled; Schedules, Next run, Updated from the summary', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  open();
  expect(document.getElementById('hdr-ratelimit-pill')).toBeNull();
  expect(document.getElementById('hdr-weekly-rl-pill')).toBeNull();
  expect(['hdr-schedules', 'hdr-next', 'hdr-updated'].map((id) => document.getElementById(id)?.textContent)).toEqual(['-', '-', '-']);
  act(() => context.set([{ name: 'a', ts: new Date(NOW - 60000).toISOString(), rate_limit_pct: 85.4, rate_limit_resets_at: NOW / 1000 + 3600, weekly_rl_pct: 61 }]));
  const five = document.getElementById('hdr-ratelimit-pill') as HTMLElement;
  expect([five.textContent, five.querySelector('.value')?.className, five.title, five.style.opacity]).toEqual(['5h usage85%', 'value red', 'Resets in 1 hr 0 min · updated 1 min ago', '']);
  expect(document.querySelector('#hdr-weekly-rl-pill .value')?.className).toBe('value yellow');
  vi.stubGlobal('fetch', () => Promise.resolve({ json: () => Promise.resolve({ schedules_active: 2, schedules_total: 5, next_runs: { b: '2026-10-21T12:30:00Z', a: '2026-10-21T14:00:00Z' } }) }));
  await act(() => refreshSummary());
  expect(['hdr-schedules', 'hdr-next'].map((id) => document.getElementById(id)?.textContent)).toEqual(['2/5', 'b in 30m']);
  expect(document.getElementById('hdr-updated')?.textContent).toMatch(/^\d\d:\d\d:\d\d$/);
  vi.stubGlobal('fetch', () => Promise.resolve({ json: () => Promise.resolve({ next_runs: {} }) }));
  await act(() => refreshSummary());
  expect(document.getElementById('hdr-next')?.textContent).toBe('none');
});

test('a stale usage sample is drawn dimmed; with no tooltip facts it says what it measures', () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  open();
  act(() => context.set([{ name: 'a', ts: new Date(NOW - RL_STALE_MS - 1000).toISOString(), rate_limit_pct: 10 }]));
  const five = document.getElementById('hdr-ratelimit-pill') as HTMLElement;
  expect(five.style.opacity).toBe('0.5');
  act(() => context.set([{ name: 'a', rate_limit_pct: 10 }]));
  expect((document.getElementById('hdr-ratelimit-pill') as HTMLElement).title).toBe('5-hour usage');
});

test('the Sessions count: online/total toned by how many are up, or the window count', () => {
  render(<SessionsCount />);
  const count = () => document.getElementById('hdr-agents') as HTMLElement;
  expect([count().textContent, count().className]).toEqual(['-/-', 'side-count']);
  act(() => summary.set({ data: { agents_total: 3, agents_online: 3 }, at: new Date() }));
  expect([count().textContent, count().className]).toEqual(['3/3', 'value green']);
  act(() => summary.set({ data: { agents_total: 0, windows_total: 4 }, at: new Date() }));
  expect([count().textContent, count().className]).toEqual(['4 windows', 'value']);
});

test('agentsCount and soonest', () => {
  expect(agentsCount({ agents_total: 3, agents_online: 1 })).toEqual({ text: '1/3', tone: 'yellow' });
  expect(agentsCount({ agents_total: 3, agents_online: 0 })).toEqual({ text: '0/3', tone: 'red' });
  expect(agentsCount({})).toEqual({ text: '0 windows', tone: '' });
  expect(soonest({ z: '2026-01-02', a: '2026-01-01' })).toEqual(['a', '2026-01-01']);
  expect(soonest(undefined)).toBeNull();
});

test('rlReading: the FRESHEST sample wins, account-wide; none sampled is null', () => {
  const rows: ContextRow[] = [
    { name: 'old', ts: '2026-10-21T11:00:00Z', rate_limit_pct: 90, rate_limit_resets_at: 1 },
    { name: 'new', ts: '2026-10-21T11:50:00Z', rate_limit_pct: 40.6, rate_limit_resets_at: null },
    { name: 'none', ts: '2026-10-21T11:59:00Z', rate_limit_pct: null },
  ];
  expect(rlReading(rows, 'rate_limit_pct', 'rate_limit_resets_at', NOW)).toEqual({ pct: 41, tone: '', stale: false, tip: 'updated 10 min ago' }); // exactly RL_STALE_MS old: not stale yet
  expect(rlReading(rows, 'weekly_rl_pct', 'weekly_rl_resets_at', NOW)).toBeNull();
  expect([60, 60.5, 80, 81].map((p) => rlReading([{ rate_limit_pct: p }], 'rate_limit_pct', 'rate_limit_resets_at', NOW)?.tone)).toEqual(['', 'yellow', 'yellow', 'red']);
});

test('rlResetTooltip: never "Resets in NaN"; days for long waits', () => {
  const at = (ms: number) => (NOW + ms) / 1000;
  expect(rlResetTooltip(null, NOW)).toBeNull();
  expect(rlResetTooltip(NaN, NOW)).toBeNull();
  expect(rlResetTooltip(at(0), NOW)).toBe('Resets now');
  expect(rlResetTooltip(at(20000), NOW)).toBe('Resets soon');
  expect(rlResetTooltip(at(5 * 60000), NOW)).toBe('Resets in 5 min');
  expect(rlResetTooltip(at(88 * 60000), NOW)).toBe('Resets in 1 hr 28 min');
  expect(rlResetTooltip(at(24 * 3600000), NOW)).toBe('Resets in 1 day');
  expect(rlResetTooltip(at(50 * 3600000), NOW)).toBe('Resets in 2 days 2 hr');
});

test('readingAge', () => {
  expect(readingAge(null, NOW)).toBeNull();
  expect(readingAge('not a date', NOW)).toBeNull();
  expect(readingAge(new Date(NOW + 60000).toISOString(), NOW)).toBeNull();
  expect(readingAge(new Date(NOW - 10000).toISOString(), NOW)).toBe('updated just now');
  expect(readingAge(new Date(NOW - 59 * 60000).toISOString(), NOW)).toBe('updated 59 min ago');
  expect(readingAge(new Date(NOW - 125 * 60000).toISOString(), NOW)).toBe('updated 2 hr 5 min ago');
});

test('loadContext publishes a list, keeps the last on anything else, and reads /api/agents/context by default', async () => {
  await loadContext(Promise.resolve([{ name: 'a' }]));
  expect(context.get()).toEqual([{ name: 'a' }]);
  await loadContext(Promise.resolve({ error: 'x' }));
  expect(context.get()).toEqual([{ name: 'a' }]);
  const urls: string[] = [];
  vi.stubGlobal('fetch', (u: string) => {
    urls.push(u);
    return Promise.resolve({ json: () => Promise.resolve([]) });
  });
  await loadContext();
  expect(urls).toEqual(['/api/agents/context']);
  expect(context.get()).toEqual([]);
});
