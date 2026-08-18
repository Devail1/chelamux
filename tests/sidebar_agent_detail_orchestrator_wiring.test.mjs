// CMX-300 rework round 1 (PR #374, judge finding 1, WIRING): onOrchestratorChange
// (nav.js:447-450) has TWO effects — `renderSidebarAgents(_agentsCache || [])`
// AND, when a detail panel is open, `renderAgentDetail()`. tests/sidebar.test.mjs
// closes the FIRST half (the sidebar badge) but can't reach the second one there:
// with TERMINALS_ON true (that file's boot config), `selectAgent` on ANY
// window-id'd agent always routes to the wall (terminals.js's focusPaneByWid ->
// selectView('terminals')) and never falls through to showAgentDetail —
// nav.js:140-151's own comment says so, and 'orchestrator' role requires
// `a.window_id` (nav.js:229), so a window-id-LESS agent (the only kind whose
// detail panel IS reachable there) can never actually become Orchestrator either.
//
// A terminals-OFF deployment is the one real configuration where BOTH are true at
// once: `selectAgent` always falls through to showAgentDetail (TERMINALS_ON is
// false, so the wall-focus branch never applies — see nav.js:152), and a
// window-id'd agent can still hold the orchestrator slot. This file boots exactly
// that (same recipe as tests/dashboard_default_view.test.mjs), opens the detail
// panel for a window-id'd agent, and proves its Role row updates off the REAL
// onOrchestratorChange listener with no renderAgentDetail() call from the test
// itself — dead-coding that half of the listener body goes red here even when
// the sidebar-only guard in sidebar.test.mjs would not catch it.
//
// Run: node --test tests/sidebar_agent_detail_orchestrator_wiring.test.mjs
// (pytest via tests/test_js_suites.py; needs `npm ci` for jsdom).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { bootDashboardDom, flush, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

const CANVAS_HTML = sliceTemplate(
    '<div class="panel" id="panel-agent-detail">', '<!-- /panel-work -->');

const BODY = `
<div class="app">
  <aside class="sidebar">
    <section class="side-section">
      <div class="side-list" id="side-nav"></div>
    </section>
    <section class="side-section">
      <span class="side-count" id="hdr-agents">-/-</span>
      <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
    </section>
  </aside>
  <main class="canvas" id="canvas">
    ${CANVAS_HTML}
  </main>
</div>`;

let util, nav, orchestrator;

before(async () => {
    ({ modules: { util, nav, orchestrator } } = await bootDashboardDom({
        body: BODY,
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
        extraModules: ['util.js', 'nav.js', 'orchestrator.js'],
    }));
    await flush();
    await flush();
    if (!document.getElementById('hdr-next')) document.body.appendChild(document.createElement('span')).id = 'hdr-next';
    if (!document.getElementById('hdr-updated')) document.body.appendChild(document.createElement('span')).id = 'hdr-updated';
});

function _detailRowValue(key) {
    const k = [...document.querySelectorAll('#agent-detail .detail-grid .k')].find(el => el.textContent === key);
    return k ? k.nextElementSibling.textContent : undefined;
}

test('an OPEN agent-detail panel updates its Role row off the real onOrchestratorChange listener — no manual re-render', async () => {
    util.setAgentsCache([{ name: 'cmx300-detail-wired', window_id: '@2', online: true }]);
    const prevFetch = globalThis.fetch;
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    try {
        window.chela.selectAgent('cmx300-detail-wired');   // terminals off -> always showAgentDetail
    } finally {
        globalThis.fetch = prevFetch;
    }
    assert.equal(_detailRowValue('Role'), 'Plain session',
        'agent-detail did not start as Plain session before the window ever held the inbox slot');

    // The ONLY thing that happens next is the real subscribe round trip — no
    // renderAgentDetail()/renderSidebarAgents() call from the test itself.
    await orchestrator.orchestratorSubscribe('@2');
    assert.equal(_detailRowValue('Role'), 'Orchestrator',
        'the OPEN agent-detail panel did not update off the real subscribe round trip — ' +
        'onOrchestratorChange did not call renderAgentDetail()');

    await orchestrator.orchestratorRelease('@2');
    assert.equal(_detailRowValue('Role'), 'Plain session',
        'the OPEN agent-detail panel did not revert off the real release round trip — ' +
        'onOrchestratorChange did not call renderAgentDetail() on release either');
});
