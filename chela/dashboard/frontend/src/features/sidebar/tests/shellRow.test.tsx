// CMX-330: a plain shell's sidebar row reads `Shell` and its cwd, by the pane header's rule
// (panehead headLabel, 10-19), until Claude starts in it. A name a human gave it still wins.
// Driven through the controller's real label pass and the real row, with the page's host.
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { installBridge } from '../../../shared/bridge/index.ts';
import { displayLabel, type HeadAgent } from '../../panehead/index.ts';
import { configureSidebar, renderSidebarAgents } from '../controller.ts';
import { Sidebar } from '../index.ts';
import type { AgentRow } from '../model.ts';
import { actions, win } from './fixtures.ts';

const bridge = installBridge(window);

beforeEach(() => {
  document.body.innerHTML = '<div id="sidebar-agents"></div>';
  // nav.ts's host: the wall's CMX-62 label, which never looks at whether Claude runs.
  configureSidebar({
    label: a => displayLabel(a as HeadAgent, a.window_id ?? ''), onWall: () => [], orchWid: () => null,
    wants: () => false, tabSignal() {}, adoptDefault() {}, openPane() {}, launchProject() {},
  });
});

afterEach(cleanup);

/** What the row shows once the controller has labelled it: the title and the cwd chip. */
function shown(a: AgentRow) {
  renderSidebarAgents([a]);
  const s = bridge.sidebar.get();
  if (!s) throw new Error('nothing published');
  const { container } = render(<Sidebar state={s} actions={actions()} />);
  const row = container.querySelector<HTMLElement>(`.agent-row[data-agent="${a.name}"]`);
  if (!row) throw new Error(`no row for ${a.name}`);
  const cwd = row.querySelector<HTMLElement>('.ar-meta [data-meta="cwd"]');
  return {
    title: row.querySelector('.agent-row-name')?.textContent,
    cwd: cwd?.textContent ?? null,
    cwdTip: cwd?.getAttribute('title') ?? null,
    label: s.labels[a.window_id ?? ''],
  };
}

test('a plain shell reads Shell with its cwd, never its tmux window name', () => {
  const a = win('liavedunix-3', { claude_running: false, shell_cwd: '~' });
  expect(shown(a)).toEqual({ title: 'Shell', cwd: '~', cwdTip: 'Shell in ~', label: 'Shell' });
});

test('a shell a human named keeps its name, and still shows its cwd', () => {
  const a = win('build-box', { claude_running: false, manual_name: true, shell_cwd: '~/projects/x' });
  expect(shown(a)).toMatchObject({ title: 'build-box', cwd: '~/projects/x', label: 'build-box' });
});

test('once Claude runs in the pane the row goes back to the session label, with no cwd chip', () => {
  const a = win('liavedunix-3', { claude_running: true, ai_title: 'Fix the sidebar', shell_cwd: null });
  expect(shown(a)).toMatchObject({ title: 'Fix the sidebar', cwd: null, label: 'Fix the sidebar' });
  // a row that does not say whether Claude runs reads as a session, as the pane header does
  const b = win('liavedunix-4', { claude_running: undefined });
  expect(shown(b)).toMatchObject({ title: 'liavedunix-4', cwd: null });
});
