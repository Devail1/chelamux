// CMX-221: Settings > Cost, ported from tests/settings_cost.test.mjs ($ by project, CMX-287) and
// settings_usage.test.mjs (tokens by window, CMX-38).
import { fireEvent } from '@testing-library/react';
import { expect, test } from 'vitest';
import { calls, must, openOn, routes, settle } from './setup.tsx';

const AGENTS = [{ name: 'runner-east', cwd: '/srv/chelamux' }, { name: 'runner-west', cwd: '/srv/nautilus/' }];
const seg = (group: string, attr: string, v: string) => must(document.querySelector<HTMLElement>(`#${group} [data-${attr}="${v}"]`), `${group} ${v}`);
const table = () => must(document.getElementById('cost-table'));
const cells = (sel: string, col: string) => [...table().querySelectorAll(sel)].map((tr) => tr.querySelector(col)?.textContent?.trim());
const costByName = (sel: string, nameCol: string) =>
  Object.fromEntries([...table().querySelectorAll(sel)].map((tr) => [tr.querySelector(nameCol)?.textContent?.trim(), tr.querySelector('td:last-child')?.textContent?.trim()]));

test('the Cost tab joins /api/agents (the project) and /api/cost into a project-grouped table with a fleet total', async () => {
  routes['/api/agents'] = AGENTS;
  routes['/api/cost'] = [{ name: 'runner-east', model: 'sonnet', cost_usd: 1.5 }, { name: 'runner-west', model: 'opus', cost_usd: 2.25 }];
  await openOn('cost');
  expect(cells('.cost-project-row', 'td:first-child')).toEqual(['nautilus', 'chelamux']); // heaviest first
  expect(cells('.cost-agent-row', 'td:nth-child(2)')).toEqual(['runner-west', 'runner-east']);
  expect(costByName('.cost-project-row', 'td:first-child')).toEqual({ chelamux: '$1.50', nautilus: '$2.25' });
  expect(must(table().querySelector('.cost-total-row td:last-child')).textContent).toBe('$3.75');
  expect(calls.find((c) => c.path === '/api/cost')?.url).toBe('/api/cost?window=live');
});

test('a window segment re-fetches for that window, marks itself pressed, and is kept per browser', async () => {
  routes['/api/cost'] = [];
  await openOn('cost');
  routes['/api/cost'] = [{ name: 'runner-east', model: 'sonnet', cost_usd: 9 }];
  fireEvent.click(seg('cost-window', 'win', '7d'));
  await settle();
  expect(calls.filter((c) => c.path === '/api/cost').map((c) => c.url)).toEqual(['/api/cost?window=live', '/api/cost?window=7d']);
  expect(table().textContent).toMatch(/\$9\.00/);
  expect([seg('cost-window', 'win', '7d').className, seg('cost-window', 'win', '7d').getAttribute('aria-pressed')]).toEqual(['work-seg-btn cost-window-btn active', 'true']);
  expect(seg('cost-window', 'win', 'live').getAttribute('aria-pressed')).toBe('false');
  expect(localStorage.getItem('chela_cost_window')).toBe('7d');
});

test('the stored window is the one a reopen asks for; a bad stored value falls back to Live', async () => {
  localStorage.setItem('chela_cost_window', '30d');
  await openOn('cost');
  expect(calls.find((c) => c.path === '/api/cost')?.url).toBe('/api/cost?window=30d');
});

test('a non-array /api/cost reads as no data, not a throw; a failed read says unavailable', async () => {
  routes['/api/cost'] = {};
  await openOn('cost');
  expect(table().textContent).toMatch(/^No cost data yet/);
  routes['/api/cost'] = new Error('down');
  fireEvent.click(seg('cost-window', 'win', 'today'));
  await settle();
  expect(table().textContent).toBe('Cost data unavailable.');
});

test('a null cost is unknown (-), never $0.00; a real zero still reads $0.00', async () => {
  routes['/api/agents'] = AGENTS;
  routes['/api/cost'] = [{ name: 'runner-east', model: 'sonnet', cost_usd: null }, { name: 'runner-west', model: 'opus', cost_usd: 0 }];
  await openOn('cost');
  expect(costByName('.cost-agent-row', 'td:nth-child(2)')).toEqual({ 'runner-east': '-', 'runner-west': '$0.00' });
  expect(costByName('.cost-project-row', 'td:first-child')).toEqual({ chelamux: '-', nautilus: '$0.00' });
});

test('the Cost tab reads nothing until it is shown', async () => {
  await openOn('general');
  expect(calls.some((c) => c.path === '/api/cost' || c.path === '/api/usage')).toBe(false);
});

// --- Usage ---------------------------------------------------------------------------------

const NOW = Math.floor(Date.now() / 1000);
const PAYLOAD = {
  limits: {
    five_hour: { used_pct: null, reason: 'last sample 45 min old' },
    seven_day: { used_pct: 42, resets_at: NOW + 3 * 86400 + 600, burn_pct_per_h: 1.5, projected_pct: 150, hits_100_before_reset: true },
  },
  windows: {
    '30m': { rows: [
      { label: 'open-mmo', ai_title: 'Grind the dungeon loop', model: 'claude-opus-5-5', requests: 40, input: 1200, cache_write: 3_400_000, cache_read: 0, output: 900, weighted: 4_300_000, cache_hit: 0.04, cache_broken: true, spark: [0, 2, 5] },
      { label: 'review-west', model: 'claude-sonnet-5-5', requests: 12, input: 10, cache_write: 0, cache_read: 9e9, output: 5, weighted: 40_000, cache_hit: 0.95, cache_broken: false, spark: [] },
    ] },
    today: { rows: [{ label: 'night-shift', requests: 1, weighted: 77 }] },
  },
  roots: { extra: ['/mnt/c/Users/*/.claude/projects'], source: 'config', scanned: ['/h/.claude/projects', '/mnt/c/Users/liav/.claude/projects'] },
};
const bar = (l: string) => must(document.querySelector(`.usage-limit[data-limit="${l}"]`), `${l} bar`);
const urows = () => [...document.querySelectorAll('#usage-table tbody tr')];

async function openUsage() {
  routes['/api/usage'] = PAYLOAD;
  await openOn('cost');
  fireEvent.click(seg('cost-view', 'view', 'usage'));
  await settle();
}

test('the Cost | Usage toggle swaps the panes, reads /api/usage, and is kept per browser', async () => {
  await openOn('cost');
  expect([must(document.getElementById('cost-pane')).hidden, must(document.getElementById('usage-pane')).hidden]).toEqual([false, true]);
  expect(calls.some((c) => c.path === '/api/usage')).toBe(false);
  routes['/api/usage'] = PAYLOAD;
  fireEvent.click(seg('cost-view', 'view', 'usage'));
  await settle();
  expect([must(document.getElementById('cost-pane')).hidden, must(document.getElementById('usage-pane')).hidden]).toEqual([true, false]);
  expect(calls.filter((c) => c.path === '/api/usage').length).toBe(1);
  expect(seg('cost-view', 'view', 'usage').getAttribute('aria-pressed')).toBe('true');
  expect(localStorage.getItem('chela_cost_view')).toBe('usage');
});

test('a limit with no fresh sample is UNKNOWN with its reason, never 0%; a known one fills to its %', async () => {
  await openUsage();
  expect(bar('5h').className).toBe('usage-limit unknown');
  expect(bar('5h').querySelector('.usage-limit-val')?.textContent).toBe('unknown');
  expect(bar('5h').textContent).not.toMatch(/\b0%/);
  expect(bar('5h').textContent).toMatch(/45 min old/);
  expect(bar('7d').querySelector('.usage-limit-val')?.textContent).toBe('42%');
  expect((bar('7d').querySelector('.usage-bar-fill') as HTMLElement).style.width).toBe('42%');
  expect(bar('7d').textContent).toMatch(/resets in 3d 0h/);
  expect(bar('7d').textContent).toMatch(/burn 1\.5%\/h▲ REACHES 100% BEFORE RESET/);
});

test('a burn under 100% shows the projection, not the flag; an unknown burn says n/a', async () => {
  routes['/api/usage'] = { ...PAYLOAD, limits: {
    five_hour: { used_pct: 85, resets_at: NOW + 3600, burn_pct_per_h: 6, projected_pct: 91, hits_100_before_reset: false },
    seven_day: { used_pct: 61, resets_at: NOW + 86400, burn_pct_per_h: null, projected_pct: null, hits_100_before_reset: null },
  } };
  await openOn('cost');
  fireEvent.click(seg('cost-view', 'view', 'usage'));
  await settle();
  expect(bar('5h').textContent).toMatch(/projected 91% at reset/);
  expect(bar('5h').textContent).not.toMatch(/REACHES/);
  expect(bar('5h').className).toBe('usage-limit high');
  expect(bar('7d').textContent).toMatch(/burn n\/a/);
  expect(bar('7d').textContent).not.toMatch(/projected|REACHES/);
  expect(bar('7d').className).toBe('usage-limit warn');
});

test('a cache-broken row says so in a word and a shape; a healthy row does not; tokens are compact', async () => {
  await openUsage();
  expect(urows().length).toBe(2);
  const [broken, ok] = urows();
  expect(broken?.className).toBe('usage-row broken');
  expect(broken?.querySelector('.usage-hit')?.textContent).toBe('4% ▲ CACHE BROKEN');
  expect(ok?.querySelector('.usage-hit')?.textContent).toBe('95%');
  expect([...(broken?.querySelectorAll('td.num') ?? [])].map((td) => td.textContent)).toEqual(['40', '1.2k', '3.4M', '0', '900', '4.3M', '4% ▲ CACHE BROKEN']);
  expect(ok?.querySelector('td:nth-child(6)')?.textContent).toBe('9.0B');
  expect(broken?.querySelectorAll('svg.usage-spark rect').length).toBe(3);
  expect(ok?.querySelector('svg.usage-spark')).toBeNull();
  expect(broken?.querySelector('.usage-sub')?.textContent).toBe('Grind the dungeon loop');
  expect(ok?.querySelector('.usage-sub')).toBeNull();
});

test('the 30m / Today switch re-renders from that window, without a refetch', async () => {
  await openUsage();
  fireEvent.click(seg('usage-window', 'win', 'today'));
  await settle(1);
  expect(urows().map((r) => r.querySelector('td')?.textContent)).toEqual(['night-shift']);
  expect(seg('usage-window', 'win', 'today').getAttribute('aria-pressed')).toBe('true');
  expect(calls.filter((c) => c.path === '/api/usage').length).toBe(1);
  expect(localStorage.getItem('chela_usage_window')).toBe('today');
});

test.each([
  ['a rejected fetch', new Error('Failed to fetch')],
  ['a JSON {error}', { error: 'boom' }],
  ['a payload without limits', { windows: {} }],
])('%s right after a good payload: the bars go UNKNOWN, the rows and roots go, and a window switch brings none back', async (_w, failure) => {
  await openUsage();
  expect(bar('7d').querySelector('.usage-limit-val')?.textContent).toBe('42%');
  routes['/api/usage'] = failure;
  fireEvent.click(seg('cost-view', 'view', 'usage')); // the shown view's segment re-reads it
  await settle();
  expect(calls.filter((c) => c.path === '/api/usage').length).toBe(2);
  for (const l of ['5h', '7d']) {
    expect([bar(l).className, bar(l).querySelector('.usage-limit-val')?.textContent, (bar(l).querySelector('.usage-bar-fill') as HTMLElement).style.width]).toEqual(['usage-limit unknown', 'unknown', '0px']);
  }
  expect(must(document.getElementById('usage-limits')).textContent).not.toMatch(/\d\s*%/);
  expect(must(document.getElementById('usage-table')).textContent).toBe('Usage data unavailable.');
  expect(must(document.getElementById('usage-roots')).textContent).toBe('');
  fireEvent.click(seg('usage-window', 'win', 'today'));
  await settle(1);
  expect(urows().length).toBe(0);
});

test('the scanned roots are shown read-only, with where the setting came from', async () => {
  await openUsage();
  const roots = must(document.getElementById('usage-roots')).textContent;
  expect(roots).toBe('Extra roots (~/.chela/config.json)/mnt/c/Users/*/.claude/projectsScanning/h/.claude/projects, /mnt/c/Users/liav/.claude/projects');
  expect(must(document.getElementById('usage-pane')).querySelectorAll('textarea, input, button:not(.usage-window-btn)').length).toBe(0);
  expect(calls.every((c) => c.method === 'GET')).toBe(true);
});

test('an explicitly empty extra-roots setting reads "none", not a blank', async () => {
  routes['/api/usage'] = { ...PAYLOAD, roots: { extra: [], source: 'env', scanned: [] } };
  await openOn('cost');
  fireEvent.click(seg('cost-view', 'view', 'usage'));
  await settle();
  expect(must(document.getElementById('usage-roots')).textContent).toBe('Extra roots (CHELA_USAGE_EXTRA_ROOTS)noneScanningnothing');
});

test('an empty window says no requests', async () => {
  routes['/api/usage'] = { ...PAYLOAD, windows: { '30m': { rows: [] } } };
  await openOn('cost');
  fireEvent.click(seg('cost-view', 'view', 'usage'));
  await settle();
  expect(must(document.getElementById('usage-table')).textContent).toBe('No Claude Code requests in this window.');
});
