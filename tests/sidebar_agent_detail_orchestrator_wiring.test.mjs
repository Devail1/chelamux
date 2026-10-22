// CMX-300 rework round 1 (PR #374, judge finding 1, WIRING): onOrchestratorChange (nav.js)
// has TWO effects: `renderSidebarAgents(_agentsCache || [])` AND a redraw of the open agent
// detail (its Role row; the agent-detail island since CMX-219, redrawn by
// `redrawAgentDetail()`). tests/sidebar.test.mjs closes the FIRST half (the sidebar badge) but
// can't reach the second there: with terminals on, `selectAgent` on a window-id'd agent always
// routes to the Wall, and 'orchestrator' role requires `a.window_id`.
//
// A terminals-OFF deployment is the one real configuration where BOTH are true at once:
// `selectAgent` always falls through to showAgentDetail, and a window-id'd agent can still hold
// the orchestrator slot. This file boots exactly that, opens the detail for a window-id'd
// agent, and proves its Role row updates off the REAL onOrchestratorChange listener with no
// redraw from the test itself.
//
// Run: pnpm exec vitest run tests/sidebar_agent_detail_orchestrator_wiring.test.mjs
// (pytest via tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { beforeAll, test } from 'vitest';
import assert from 'node:assert/strict';
import { APP_BODY, bootDashboardDom, flush, ORCHESTRATOR, AGENTS_STORE, VIEW_STORE, NAV } from './js_helpers/dashboard_dom.mjs';

let agentsStore, viewStore, nav, orchestrator;

beforeAll(async () => {
    ({ modules: { agents: { agents: agentsStore }, view: { view: viewStore }, nav, orchestrator } } = await bootDashboardDom({
        body: APP_BODY,
        terminalsEnabled: false,
        canvasStub: true,
        // Same fixture shape as tests/sidebar.test.mjs: wid and name are
        // DIFFERENT values (never body.wid echoed into both), matching
        // app.py's real _orchestrator_status_payload() (wid = the @id, name =
        // store['orchestrator_name'], the window's tmux NAME).
        fetchImpl: (url, opts) => {
            const u = String(url);
            if (u.includes('/api/orchestrator/subscribe')) {
                const body = opts && opts.body ? JSON.parse(opts.body) : {};
                return Promise.resolve({
                    ok: true, status: 200,
                    json: () => Promise.resolve({
                        ok: true, wid: body.wid, name: `${body.wid}-tmux-name`, state: 'registered', why: '', queued: 0,
                    }),
                });
            }
            if (u.includes('/api/orchestrator/release')) {
                return Promise.resolve({
                    ok: true, status: 200,
                    json: () => Promise.resolve({ ok: true, wid: null, name: null, state: 'unregistered', why: '', queued: 0 }),
                });
            }
            const body = u.includes('/api/agents/context') ? [] : {};
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
        },
        extraModules: [AGENTS_STORE, VIEW_STORE, NAV, ORCHESTRATOR],
    }));
    await flush();
    await flush();
});

function _detailRowValue(key) {
    const k = [...document.querySelectorAll('#agent-detail .detail-grid .k')].find(el => el.textContent === key);
    return k ? k.nextElementSibling.textContent : undefined;
}

test('an OPEN agent-detail panel updates its Role row off the real onOrchestratorChange listener — no manual re-render', async () => {
    agentsStore.set([{ name: 'cmx300-detail-wired', window_id: '@2', online: true }]);
    const prevFetch = globalThis.fetch;
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    try {
        nav.selectAgent('cmx300-detail-wired');   // terminals off -> always showAgentDetail
        await flush();
    } finally {
        globalThis.fetch = prevFetch;
    }
    assert.equal(_detailRowValue('Role'), 'Plain session',
        'agent-detail did not start as Plain session before the window ever held the inbox slot');

    // The ONLY thing that happens next is the real subscribe round trip — no
    // redraw call from the test itself.
    await orchestrator.orchestratorSubscribe('@2');
    await flush();
    assert.equal(_detailRowValue('Role'), 'Orchestrator',
        'the OPEN agent-detail panel did not update off the real subscribe round trip — ' +
        'onOrchestratorChange did not redraw the agent detail');

    await orchestrator.orchestratorRelease('@2');
    await flush();
    assert.equal(_detailRowValue('Role'), 'Plain session',
        'the OPEN agent-detail panel did not revert off the real release round trip — ' +
        'onOrchestratorChange did not redraw the agent detail on release either');
});
