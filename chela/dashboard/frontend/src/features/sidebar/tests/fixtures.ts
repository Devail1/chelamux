import { vi } from 'vitest';
import type { SidebarActions } from '../index.ts';
import { type AgentRow, type RunCard, type SidebarState, normalizeView } from '../model.ts';

export const NOW = Date.parse('2026-10-19T12:00:00Z');

export const wants = (a: AgentRow) => a.needs_human === true || a.session_status === 'waiting';

let wid = 100;
export function win(name: string, over: Partial<AgentRow> = {}): AgentRow {
  return { name, window_id: `@${wid++}`, claude_running: true, session_status: 'idle', cwd: '/srv/code/alpha', ...over };
}

export function runWin(task: string, role: 'agent' | 'judge', run: Partial<RunCard> = {}, over: Partial<AgentRow> = {}): AgentRow {
  return win(`${role === 'judge' ? 'judge-' : ''}liavacc/${task.toLowerCase()}`, {
    cwd: `/home/u/.chela/worktrees/x/${task}`, dispatched: true,
    run: { task_id: task, role, status: 'running', title: `Task ${task}`, ...run }, ...over,
  });
}

export function state(agents: AgentRow[], over: Partial<SidebarState> = {}): SidebarState {
  return {
    agents, wants, orchWid: null, view: normalizeView({ activity: 'all', sort: 'name' }), order: [],
    custom: { groups: [], assign: {} }, archived: [], sessions: [], labels: {}, now: NOW, collapsed: [],
    ctx: {}, onWall: [], active: null, altHeld: false, ...over,
  };
}

export function actions() {
  return {
    selectAgent: vi.fn<SidebarActions['selectAgent']>(),
    toggleGroup: vi.fn<SidebarActions['toggleGroup']>(),
    groupNewSession: vi.fn<SidebarActions['groupNewSession']>(),
    toggleArchivedSection: vi.fn<SidebarActions['toggleArchivedSection']>(),
    unarchiveRow: vi.fn<SidebarActions['unarchiveRow']>(() => Promise.resolve()),
    groupMenuAction: vi.fn<SidebarActions['groupMenuAction']>(),
    rowMenuAction: vi.fn<SidebarActions['rowMenuAction']>(),
    moveToGroup: vi.fn<SidebarActions['moveToGroup']>(),
    viewMenuAction: vi.fn<SidebarActions['viewMenuAction']>(),
    newCustomGroup: vi.fn<SidebarActions['newCustomGroup']>(() => null),
    renameCustomGroup: vi.fn<SidebarActions['renameCustomGroup']>(),
    deleteCustomGroup: vi.fn<SidebarActions['deleteCustomGroup']>(),
  };
}
