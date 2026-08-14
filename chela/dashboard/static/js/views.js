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
// CMX-279 (measured, not assumed — asked which of the seven views he actually
// opens, Liav named exactly two): the dashboard is Wall · Work. CMX-230 had
// tried demoting the other five (Feed, Knowledge, Agents, Personas, Cost) into
// a quieter secondary nav group rather than deleting them — this ticket
// supersedes that call: the five are gone outright, not re-parented, because
// nobody was opening them either way and cutting nav count is a bigger visual
// win than any redesign of a group that was never read. Their dedicated
// renderer modules (feed.js, cost.js, personas.js) are deleted with them;
// agents.js and knowledge.js survive trimmed, since both also back surfaces
// that stay (the agent-detail drill-in and the sidebar rate-limit pills; the
// task-modal brief's markdown renderer, respectively — see those files' own
// headers).
//
// `agent-detail` is a virtual drill-in — reachable (from the always-visible
// sidebar Sessions list, the Wall, or the palette), no nav item of its own.
// See viewreg.js for the entry shape.
// ---------------------------------------------------------------------------
import { renderAgentDetail } from './nav.js';
import { renderTerminals, startTermTimer, stopTermTimer } from './terminals.js';
import { pollWork } from './work.js';

export const VIEWS = [
    {
        id: 'terminals',
        label: 'Wall',
        // lucide (not a glyph) so the nav rail's icons share one box. `layout-grid`
        // reads as the wall's grid of live tiles.
        lucide: 'layout-grid',
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
        // lucide `columns-3` — the Dispatch/Kanban/Schedules board, as a fixed box.
        lucide: 'columns-3',
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
        id: 'agent-detail',
        label: 'Agent',
        virtual: true,          // a drill-in, not a destination: no nav item, no palette
        tick: () => renderAgentDetail(),
    },
];
