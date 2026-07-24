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
// The views are Feed · Wall · Work · Knowledge · Agents · Personas · Cost.
// `agent-detail` is a virtual drill-in — reachable, no nav item. See viewreg.js
// for the entry shape.
// ---------------------------------------------------------------------------
import { refreshAgents } from './agents.js';
import { refreshCost } from './cost.js';
import { enterFeed, tickFeed } from './feed.js';
import { _kn, knBackToGlance, refreshKnowledge } from './knowledge.js';
import { renderAgentDetail } from './nav.js';
import { refreshPersonas } from './personas.js';
import { renderTerminals, startTermTimer, stopTermTimer } from './terminals.js';
import { pollWork } from './work.js';

export const VIEWS = [
    {
        id: 'feed',
        label: 'Feed',
        // A lucide `rss` mark, not a glyph: the old ≡ read exactly like the sidebar
        // toggle. `lucide` names an inline SVG from util.js's vendored set (see
        // _navItemHtml); a plain `icon` string is still a unicode glyph.
        lucide: 'rss',
        // AGENT LANES: the log, grouped under the agent that produced each row, with
        // the agents that need you sorted to the top. Entering re-reads the fleet AND
        // the log from scratch; the SSE `log` delta accelerates it, and this tick is
        // the fallback that keeps it correct if the stream never connects.
        enter: () => enterFeed(),
        tick: () => tickFeed(),
    },
    {
        id: 'terminals',
        label: 'Wall',
        // lucide (not a glyph) so the nav rail's five icons share one box — see the
        // Feed note above. `layout-grid` reads as the wall's grid of live tiles.
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
        id: 'knowledge',
        label: 'Knowledge',
        // lucide `book-open` — the knowledge base, as a fixed box.
        lucide: 'book-open',
        // Entering from the nav lands on the glance overview, not whatever concept
        // was last open.
        enter: () => { if (typeof knBackToGlance === 'function' && _kn.tree) knBackToGlance(); },
        tick: () => refreshKnowledge(),
    },
    {
        id: 'agents',
        label: 'Agents',
        // lucide `bot` — the agent fleet, as a fixed box.
        lucide: 'bot',
        tick: () => refreshAgents(),
    },
    {
        id: 'personas',
        label: 'Personas',
        // lucide `drama` — the theatre masks: the declared persona layer (judge · critic ·
        // orchestrator). Read-only: it renders the registry, it never launches a persona.
        lucide: 'drama',
        // The decisions log used to ride alongside the persona cards (cmx-106); it now
        // lives in the topbar's Decisions popover (main.js seeds + ticks it,
        // decisions.js — CMX-171 moved it out of the sidebar), so this view is
        // persona cards only.
        enter: () => refreshPersonas(),
        tick: () => refreshPersonas(),
    },
    {
        id: 'cost',
        label: 'Cost',
        // lucide `dollar-sign` — fleet spend, as a fixed box.
        lucide: 'dollar-sign',
        // Entering and ticking both just re-pull /api/cost (scoped by the tab's own
        // Live/Today/7d/30d selector state) + /api/agents (for project) and re-render.
        enter: () => refreshCost(),
        tick: () => refreshCost(),
    },
    {
        id: 'agent-detail',
        label: 'Agent',
        virtual: true,          // a drill-in, not a destination: no nav item, no palette
        tick: () => renderAgentDetail(),
    },
];
