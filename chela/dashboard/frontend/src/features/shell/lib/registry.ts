// THE VIEW REGISTRY (static/js/views.js until CMX-248): one declaration per view, and the nav,
// the view switch, the refresh loop and the palette all derive from it (../views.ts).
//
// CMX-279 (measured, not assumed: asked which of the seven views he actually opens, Liav named
// two): the dashboard is Wall and Work. Feed, Knowledge, Agents, Personas and Cost were deleted
// outright rather than demoted into a quieter nav group (CMX-230's attempt), since nobody opened
// them either way. `agent-detail` is a virtual drill-in: reachable from the sidebar's sessions,
// the Wall and the palette, with no nav row of its own.
import { startTermTimer, stopTermTimer } from '../../paneframe/index.ts';
import { renderTerminals } from '../../wall/index.ts';
import type { View } from '../views.ts';

export const VIEWS: readonly View[] = [
  {
    id: 'terminals',
    label: 'Wall',
    lucide: 'layout-grid',
    enabled: (ctx) => ctx.terminalsOn,
    // The wall holds LIVE iframes: entering reconciles by stable window id and never reloads a
    // pane (features/wall), so the hooks only own the 4 s timer.
    enter: () => startTermTimer(),
    exit: () => stopTermTimer(),
    tick: (context) => renderTerminals(context),
  },
  {
    id: 'work',
    label: 'Work',
    lucide: 'columns-3',
    // Visible on every view: the Work poll (a scheduler lane, features/work) fills them.
    badges: [
      { id: 'side-runs-count', title: 'Active runs', text: '0' },
      { id: 'side-pr-count', cls: 'badge-pr', title: 'Open PRs', text: '0 PR' },
    ],
    // No hooks: the view switch's refresh runs the Work poll.
  },
  {
    id: 'agent-detail',
    label: 'Agent',
    virtual: true,
    // No tick: the agent detail (features/agentdetail) redraws from the stores.
  },
];
