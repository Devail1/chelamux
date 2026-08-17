// THE ORCHESTRATOR ROLE BADGE'S CSS CONTRACT (CMX-302 rework round 2, PR #376) — two
// pure cascade properties tests/sidebar.test.mjs cannot see, because bootDashboardDom
// never loads style.css into its jsdom (0 <style>/<link> in that helper — it boots the
// module graph only). The judge corrupted both and the whole suite (3210 tests) stayed
// green:
//   1. `.ar-role.orchestrator { display: none }` — the badge's own visible text was
//      removed by CMX-302 (the crown icon + title tooltip replaced it), so CMX-300's
//      colourblind-safe shape cue now lives ENTIRELY in this block rendering at all.
//      sidebar.test.mjs's `assert.ok(badge, ...)` only proves the <span> exists in the
//      DOM tree — jsdom builds DOM nodes for `display:none` elements exactly like any
//      other, so that assertion cannot see a collapsed rule.
//   2. `.ar-role.orchestrator { width: 180px }` — CMX-302's stated objective is that the
//      badge stop being wide enough to truncate the session name next to it (the old
//      "Orchestrator" text pill was the reported bug). No test read the rendered width
//      at all before this file.
//
// This runs the REAL style.css through the REAL dashboard boot (bootDashboardDom, the
// same real orchestratorSubscribe() round trip tests/sidebar.test.mjs drives), then
// injects style.css into that same jsdom document afterward and reads the CASCADED
// value with getComputedStyle on the badge nav.js actually rendered — not a hand-typed
// fixture that could drift from _agentRowHtml's real class names. Same recipe as
// tests/decisions_modal_css.test.mjs / tests/gs_files_pointer_events_css.test.mjs.
//
// Run: node --test tests/sidebar_role_badge_css.test.mjs (tests/test_js_suites.py runs
// every .test.mjs inside pytest, by discovery; needs `npm ci` for jsdom).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it
import { bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const styleCss = readFileSync(join(ROOT, 'static', 'style.css'), 'utf8');

const BODY = `
<div class="app">
  <aside class="sidebar">
    <section class="side-section">
      <span class="side-count" id="hdr-agents">-/-</span>
      <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
    </section>
  </aside>
</div>`;

const agent = (name, over = {}) => ({
    name, window_id: '@1', online: true, session_status: 'idle', ...over,
});

let dom, nav, util, orchestrator, badge;

before(async () => {
    ({ dom, modules: { nav, util, orchestrator } } = await bootDashboardDom({
        body: BODY,
        canvasStub: true,
        // Same real subscribe round trip as tests/sidebar.test.mjs — the badge only
        // exists once onOrchestratorChange redraws the row off a real /api/orchestrator/
        // subscribe response.
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
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        },
        extraModules: ['util.js', 'nav.js', 'orchestrator.js'],
    }));
    // main.js fires an unawaited initial refresh() on load, which would otherwise
    // overwrite the manual renderSidebarAgents() call below once its stubbed fetch
    // settles (DEFEAT_SHAPES: a race window, not a code bug — same fix as
    // tests/dashboard_default_view.test.mjs / tests/sidebar_agent_detail_orchestrator_wiring.test.mjs).
    await flush();
    await flush();

    // bootDashboardDom builds no <style> at all — inject the REAL style.css into the
    // SAME document the sidebar was rendered into, after the fact, exactly like a
    // browser's cascade applies regardless of DOM-vs-CSS load order.
    const styleEl = dom.window.document.createElement('style');
    styleEl.textContent = styleCss;
    dom.window.document.head.appendChild(styleEl);

    const rows = [agent('orch', { window_id: '@1' })];
    // onOrchestratorChange's listener (nav.js) redraws off `_agentsCache`, not the
    // rows array passed to renderSidebarAgents — real callers reach it through
    // util.setAgentsCache too (see tests/sidebar.test.mjs's identical setup).
    util.setAgentsCache(rows);
    nav.renderSidebarAgents(rows);
    await orchestrator.orchestratorSubscribe('@1');
    badge = dom.window.document.querySelector('#sidebar-agents .agent-row[data-agent="orch"] .ar-role');
    assert.ok(badge, 'sanity: the orchestrator role badge did not render at all');
});

test('🔴 GUARD: the orchestrator role badge is actually VISIBLE — .ar-role.orchestrator renders display:flex under the REAL stylesheet', () => {
    assert.equal(dom.window.getComputedStyle(badge).display, 'flex',
        '.ar-role.orchestrator must render display:flex. CMX-300\'s colourblind-safe shape ' +
        'cue now lives entirely in this element (its own text was removed by CMX-302 in favour ' +
        'of a crown icon + title tooltip) — display:none collapses the whole cue to nothing ' +
        'while every DOM-only assertion (element exists, has the right class) stays green');
});

test('🔴 GUARD: the orchestrator role badge stays ICON-NARROW — .ar-role.orchestrator renders width:18px under the REAL stylesheet', () => {
    assert.equal(dom.window.getComputedStyle(badge).width, '18px',
        '.ar-role.orchestrator must render width:18px. CMX-302\'s stated objective is that the ' +
        'badge stop being wide enough to truncate the session name next to it — a wider computed ' +
        'width reproduces the exact bug this ticket fixed, and no DOM-only assertion can see it');
});

test('🔴 GUARD (CMX-302 rework round 4): the badge stays ICON-NARROW through padding too — .ar-role.orchestrator renders padding:0 under the REAL stylesheet', () => {
    // `width` is only one half of the box. box-sizing:border-box clamps CONTENT to the
    // declared width but not padding, so `padding: 0 80px` regrows the exact same
    // box — a badge wide enough to truncate the session name beside it, this ticket's
    // reported bug — while getComputedStyle(badge).width above still reads 18px.
    const style = dom.window.getComputedStyle(badge);
    assert.equal(style.paddingLeft, '0px',
        '.ar-role.orchestrator must render zero left padding — nonzero padding regrows the ' +
        'badge\'s box exactly like the wide `width` this ticket fixed, invisible to a width-only check');
    assert.equal(style.paddingRight, '0px',
        '.ar-role.orchestrator must render zero right padding — see the left-padding assertion above');
});
