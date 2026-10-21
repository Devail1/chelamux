// Who holds the decisions inbox (chela/inbox.py's single `orchestrator` slot), as
// /api/orchestrator/status last reported it. shared/orchestrator owns the reads and writes;
// the Wall's pane toggle, the sidebar row and the Decisions inbox all render this ONE value,
// so they can never disagree about who owns the role.
import { createStore } from '../bridge/index.ts';

export interface OrchestratorStatus {
  wid: string | null;
  name: string | null;
  /** ok / unregistered / dangling / gone / unstamped (chela/inbox.py). */
  state: string;
  why: string;
  queued: number;
}

export const orchestrator = createStore<OrchestratorStatus>({ wid: null, name: null, state: 'unregistered', why: '', queued: 0 });
