// CMX-212 P21: the agent-detail drill-in, its actions and the send-message dialog (ported from
// nav.js renderAgentDetail and static/js/agents.js by CMX-219; the page's wiring is
// tests/sidebar.test.mjs, dashboard_default_view and sidebar_agent_detail_orchestrator_wiring).
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { context } from '../../../shared/store/context.ts';
import { AgentDetail, SendMessage, detailAgent, openSendMsg, redrawAgentDetail, setAgentDetailHost } from '../index.ts';

let calls: { url: string; body: unknown }[] = [];
let role = 'Plain session';
const host = { selectView: vi.fn(), roleLabel: vi.fn(() => role), dotColor: vi.fn(() => 'yellow') };
let answer = true;

beforeEach(() => {
  calls = [];
  role = 'Plain session';
  answer = true;
  host.selectView.mockClear();
  setAgentDetailHost(host);
  agents.set([]);
  context.set([]);
  detailAgent.set(null);
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    calls.push({ url, body: opts?.body ? JSON.parse(String(opts.body)) : undefined });
    return Promise.resolve({ json: () => Promise.resolve([]) });
  });
  vi.stubGlobal('confirm', () => answer);
  render(<div id="agent-detail"><AgentDetail /><SendMessage /></div>);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const $ = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const settle = () => act(() => new Promise((r) => setTimeout(r, 0)));
const rows = () => Object.fromEntries([...document.querySelectorAll('.detail-grid .k')].map((k) => [k.textContent, k.nextElementSibling?.textContent]));
const buttons = () => [...document.querySelectorAll('.canvas-toolbar button')].map((b) => b.textContent);
async function drill(name: string, list: object[]) {
  act(() => agents.set(list as never));
  act(() => detailAgent.set(name));
  await settle();
}

test('nothing drawn while no agent is drilled into', () => {
  expect($('.detail-head')).toBeNull();
});

test('an agent whose window is gone says so, with a Back', async () => {
  await drill('ghost', []);
  expect($('.detail-title')?.textContent).toBe('ghost');
  expect($('.side-empty')?.textContent).toBe("This agent's window is no longer present.");
  fireEvent.click($('.detail-back') as HTMLElement);
  // terminals on (the jsdom default boot): back to the Wall
  expect(host.selectView.mock.calls).toEqual([['terminals']]);
});

test('the metadata rows, the attention dot and the recap', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(Date.parse('2026-10-21T12:00:00Z'));
  await drill('w1', [{ name: 'w1', window_id: '@4', claude_running: true, window_type: 'claude', session_status: 'busy', liveness: 'alive', cwd: '/p/w1',
    schedule_next_run: '2026-10-21T13:00:00Z', ai_title: 'Port things', recap: 'did X', recap_ts: '2026-10-21T11:55:00Z' }]);
  expect(rows()).toEqual({ Window: '@4', Type: 'claude', Role: 'Plain session', Claude: 'running', Status: 'busy', Liveness: 'alive', CWD: '/p/w1',
    'Next run': expect.stringMatching(/^\d\d:\d\d Oct 21$/), Title: 'Port things' });
  expect($('.health-dot')?.className).toBe('health-dot yellow');
  expect($('.detail-recap-head')?.textContent).toBe('Recap · 5m ago');
  expect($('.detail-recap')?.textContent).toBe('did X');
  vi.useRealTimers();
});

test('a stopped shell falls back to its defaults', async () => {
  await drill('s', [{ name: 's' }]);
  expect(rows()).toEqual({ Window: '-', Type: 'shell', Role: 'Plain session', Claude: 'stopped', Status: 'offline', Liveness: '-', CWD: '-' });
  expect($('.detail-recap')).toBeNull();
});

test('the Role row follows the host, redrawn when it says the slot moved', async () => {
  await drill('w', [{ name: 'w', window_id: '@2' }]);
  expect(rows().Role).toBe('Plain session');
  role = 'Orchestrator';
  act(() => redrawAgentDetail());
  expect(rows().Role).toBe('Orchestrator');
});

test('CMX-41: the PR badge says when the PR is history', async () => {
  const url = 'https://github.com/acme/widgets/pull/613';
  for (const [state, want] of [['merged', 'PR #613 · merged'], ['closed', 'PR #613 · closed'], ['open', 'PR #613'], [undefined, 'PR #613']]) {
    await drill('p', [{ name: 'p', pr: { url, number: 613, state } }]);
    expect([$('.pr-badge')?.textContent, $('.pr-badge')?.getAttribute('href')]).toEqual([want, url]);
  }
  await drill('p', [{ name: 'p', pr: { url } }]);
  expect($('.pr-badge')?.textContent).toBe('PR');
});

test('the actions: Trigger with schedules; Restart + Stop while Claude runs, else Start; no Message with a Wall', async () => {
  await drill('a', [{ name: 'a', claude_running: true, has_schedules: true }]);
  expect(buttons()).toEqual(['Trigger', 'Restart', 'Stop']);
  await drill('a', [{ name: 'a' }]);
  expect(buttons()).toEqual(['Start']);
});

test('Stop and Restart confirm first; a declined confirm sends nothing', async () => {
  await drill('a', [{ name: 'a', claude_running: true }]);
  answer = false;
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
  await settle();
  expect(calls.filter((c) => c.url !== '/api/agents/context')).toEqual([]);
  answer = true;
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  expect(screen.getByRole('button', { name: 'Stopping...' }).hasAttribute('disabled')).toBe(true);
  await settle();
  expect(calls.filter((c) => c.url === '/api/agents/stop')).toEqual([{ url: '/api/agents/stop', body: { agent: 'a' } }]);
});

test('each action posts its own route with the agent; Trigger stays clickable', async () => {
  await drill('a', [{ name: 'a', claude_running: true, has_schedules: true }]);
  fireEvent.click(screen.getByRole('button', { name: 'Trigger' }));
  await settle();
  expect(screen.getByRole('button', { name: 'Trigger' }).hasAttribute('disabled')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
  await settle();
  await drill('b', [{ name: 'b' }]);
  fireEvent.click(screen.getByRole('button', { name: 'Start' }));
  expect(screen.getByRole('button', { name: 'Starting...' })).toBeTruthy();
  await settle();
  expect(calls.filter((c) => c.url.startsWith('/api/agents/') && c.url !== '/api/agents/context')).toEqual([
    { url: '/api/agents/trigger', body: { agent: 'a' } }, { url: '/api/agents/restart', body: { agent: 'a' } }, { url: '/api/agents/start', body: { agent: 'b' } }]);
});

test('drilling in reads the context; its bar shows %, model, cost and the estimate hint', async () => {
  await drill('a1', [{ name: 'a1' }]);
  expect(calls.map((c) => c.url)).toContain('/api/agents/context');
  expect($('#ctx-a1 .context-label')?.textContent).toBe('Context: --');
  act(() => context.set([{ name: 'a1', used: '150k', total: '200k', used_pct: 75, model: 'opus', cost_usd: 1.5, estimated: true, session_name: 'cmx-1' }]));
  const label = $('#ctx-a1 .context-label') as HTMLElement;
  expect(label.textContent).toBe('Context: 150k/200k (75%~) · opus · $1.5 · est');
  expect(label.title).toBe('cmx-1 · estimate from transcript; run `chela install-statusline` for exact context %');
  expect([$('#ctx-a1 .context-bar-fill')?.className, $('#ctx-a1 .context-bar-fill')?.style.width]).toEqual(['context-bar-fill ctx-warn', '75%']);
  act(() => context.set([{ name: 'a1', used: '10k', total: '200k', used_pct: 81, session_name: 'cmx-1' }]));
  expect([label.textContent, label.title, $('#ctx-a1 .context-bar-fill')?.className]).toEqual(['Context: 10k/200k (81%)', 'cmx-1', 'context-bar-fill ctx-danger']);
  act(() => context.set([{ name: 'a1', used_pct: 60 }]));
  expect([label.hasAttribute('title'), $('#ctx-a1 .context-bar-fill')?.className]).toEqual([false, 'context-bar-fill']);
  act(() => context.set([{ name: 'a1', used_pct: null }]));
  expect(label.textContent).toBe('Context: unavailable');
});

test('send a message: the dialog names the agent, a blank message sends nothing, Send posts and closes', async () => {
  act(() => openSendMsg('w9'));
  expect(screen.getByRole('dialog', { name: 'Send Message to w9' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await settle();
  expect(calls).toEqual([]);
  fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: '  hi there  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await settle();
  expect(calls).toEqual([{ url: '/api/agents/msg', body: { agent: 'w9', message: 'hi there' } }]);
  expect(screen.queryByRole('dialog')).toBeNull();
  act(() => openSendMsg('w9'));
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('a button that stays (Restart on a running agent) comes back clickable, also when the request fails', async () => {
  await drill('a', [{ name: 'a', claude_running: true }]);
  fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
  await settle();
  expect(screen.getByRole('button', { name: 'Restart' }).hasAttribute('disabled')).toBe(false);
  // only the action fails: the refresh that follows reads as usual
  vi.stubGlobal('fetch', (url: string) => (url === '/api/agents/restart' ? Promise.reject(new Error('down')) : Promise.resolve({ json: () => Promise.resolve([]) })));
  fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
  await settle();
  expect(screen.getByRole('button', { name: 'Restart' }).hasAttribute('disabled')).toBe(false);
});
