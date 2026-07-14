// ---------------------------------------------------------------------------
// THE VIEW REGISTRY — one declaration per view, and everything derives from it.
//
// Before this file, adding (or removing) a view meant editing FOUR places: the
// .side-item markup in index.html, the per-view timer if/else in selectView, a
// second if/else in main.js's refresh loop, and a third hardcoded list in the
// command palette. Four places to add, four to delete — so nothing was ever
// deleted, and the dashboard carried seven views.
//
// Now: the sidebar is rendered from this array (nav.js), selectView's enter/exit
// hooks come from this array, the global refresh calls this array's `tick`, and
// the palette lists this array. Deleting a view is deleting its entry below.
//
// The five views are Feed · Agents · Wall · Work · Knowledge. `agent-detail` is
// a virtual drill-in — reachable, no nav item. See viewreg.js for the entry shape.
// ---------------------------------------------------------------------------
import { refreshAgents } from './agents.js';
import { refreshFeed } from './feed.js';
import { _kn, knBackToGlance, refreshKnowledge } from './knowledge.js';
import { renderAgentDetail } from './nav.js';
import { renderTerminals, startTermTimer, stopTermTimer } from './terminals.js';
import { pollWork } from './work.js';

export const VIEWS = [
    {
        id: 'feed',
        label: 'Feed',
        icon: '≡',
        // Part 1 registers the view and wires it to /api/log; the timeline LAYOUT
        // is part 2 (being designed from real data), so the panel renders a plain
        // list on purpose. The SSE `log` delta accelerates it; this tick is the
        // fallback that keeps it correct if the stream never connects.
        enter: () => refreshFeed(),
        tick: () => refreshFeed(),
    },
    {
        id: 'agents',
        label: 'Agents',
        icon: '▢',
        tick: () => refreshAgents(),
    },
    {
        id: 'terminals',
        label: 'Wall',
        icon: '▦',
        enabled: ctx => !!ctx.terminalsOn,
        // The wall holds LIVE iframes: entering reconciles by stable window id and
        // never reloads a pane (terminals.js), so this hook only owns the timer.
        enter: () => startTermTimer(),
        exit: () => stopTermTimer(),
        tick: () => renderTerminals(),
    },
    {
        id: 'work',
        label: 'Work',
        icon: '▤',
        badges: [
            { id: 'side-runs-count', title: 'Active runs', text: '0' },
            { id: 'side-pr-count', cls: 'badge-pr', title: 'Open PRs', text: '0 PR' },
        ],
        // Dispatch + Kanban + Schedules, merged. work.js owns the ONE /api/dispatcher
        // poll for the whole app (it also feeds the badges above, which are visible on
        // every view) — so there is no timer to start here, only an immediate redraw.
        enter: () => pollWork(),
    },
    {
        id: 'knowledge',
        label: 'Knowledge',
        icon: '◆',
        // Entering from the nav lands on the glance overview, not whatever concept
        // was last open.
        enter: () => { if (typeof knBackToGlance === 'function' && _kn.tree) knBackToGlance(); },
        tick: () => refreshKnowledge(),
    },
    {
        id: 'agent-detail',
        label: 'Agent',
        virtual: true,          // a drill-in, not a destination: no nav item, no palette
        tick: () => renderAgentDetail(),
    },
];
