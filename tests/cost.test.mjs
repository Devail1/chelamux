// THE COST TAB, IN A REAL DOM — a fleet spend view over data chela already
// ingests (statusLine cost.total_cost_usd -> context_snapshots.cost_usd ->
// /api/cost?window=...). A Live/Today/7d/30d selector scopes which window the
// tab fetches; these tests drive the real renderCostTable()/refreshCost()/
// setCostWindow() into a real #cost-table (jsdom) and assert what RENDERS —
// project subtotals, agent rows, the fleet-total footer, and which window
// param actually gets fetched — not what the source merely mentions.
//
// Run: node --test tests/cost.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = `<div class="panel" id="panel-cost">
    <div class="work-toolbar">
        <div class="work-seg" id="cost-window" role="group" aria-label="Cost window">
            <button type="button" class="work-seg-btn cost-window-btn" data-win="live" aria-pressed="true">Live</button>
            <button type="button" class="work-seg-btn cost-window-btn" data-win="today" aria-pressed="false">Today</button>
            <button type="button" class="work-seg-btn cost-window-btn" data-win="7d" aria-pressed="false">7d</button>
            <button type="button" class="work-seg-btn cost-window-btn" data-win="30d" aria-pressed="false">30d</button>
        </div>
    </div>
    <div id="cost-table"></div>
</div>`;

let cost;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement', 'Element', 'Node']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.window.chela = globalThis.window.chela || {};
    cost = await import('../chela/dashboard/static/js/cost.js');
});

beforeEach(() => {
    document.getElementById('cost-table').innerHTML = '';
    localStorage.clear();
    // setCostWindow('live') both resets the module's selected-window state and
    // refetches — safe here since the default fetch (see `before`) resolves an
    // empty object for any url, and refreshCost's own try/catch swallows a
    // non-array payload into the "unavailable" empty state rather than throwing.
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    return cost.setCostWindow('live');
});

const projectRows = () => [...document.querySelectorAll('.cost-project-row')];
const agentRows = () => [...document.querySelectorAll('.cost-agent-row')];
const totalRow = () => document.querySelector('.cost-total-row');

test('renders one project-subtotal row per distinct project, and one agent row per agent', () => {
    cost.renderCostTable([
        { name: 'cmx-92', project: 'chelamux', model: 'Sonnet', cost_usd: 1.5 },
        { name: 'cmx-88', project: 'chelamux', model: 'Sonnet', cost_usd: 0.5 },
        { name: 'nautilus-hub', project: 'nautilus', model: 'Opus', cost_usd: 3.0 },
    ]);
    assert.equal(projectRows().length, 2, 'expected one subtotal row per project');
    assert.equal(agentRows().length, 3, 'expected one row per agent');
});

test('projects are ordered by total spend, descending — the biggest spender leads', () => {
    cost.renderCostTable([
        { name: 'a', project: 'small', model: 'Sonnet', cost_usd: 0.1 },
        { name: 'b', project: 'big', model: 'Opus', cost_usd: 9.0 },
    ]);
    const names = projectRows().map(r => r.textContent.trim().split('\n')[0].trim());
    assert.deepEqual(names.map(n => n.split('$')[0].trim()), ['big', 'small']);
});

test('a project subtotal is the sum of ITS agents, not the whole fleet', () => {
    cost.renderCostTable([
        { name: 'a', project: 'p1', model: 'Sonnet', cost_usd: 1.0 },
        { name: 'b', project: 'p1', model: 'Sonnet', cost_usd: 2.0 },
        { name: 'c', project: 'p2', model: 'Sonnet', cost_usd: 100.0 },
    ]);
    const p1Row = projectRows().find(r => r.textContent.includes('p1'));
    assert.ok(p1Row.textContent.includes('$3.00'), 'p1 subtotal should be 1.00 + 2.00 = 3.00, not fleet-wide');
});

test('the fleet-total footer sums every agent across every project', () => {
    cost.renderCostTable([
        { name: 'a', project: 'p1', model: 'Sonnet', cost_usd: 1.25 },
        { name: 'b', project: 'p2', model: 'Opus', cost_usd: 2.75 },
    ]);
    assert.ok(totalRow(), 'no fleet-total row rendered');
    assert.ok(totalRow().textContent.includes('$4.00'), 'fleet total should be 1.25 + 2.75 = 4.00');
});

test('an agent with no cost sample yet shows a dash, and does not corrupt the subtotal', () => {
    cost.renderCostTable([
        { name: 'fresh-agent', project: 'p1', model: 'Sonnet', cost_usd: null },
        { name: 'seasoned-agent', project: 'p1', model: 'Sonnet', cost_usd: 2.0 },
    ]);
    const rows = agentRows();
    const freshRow = rows.find(r => r.textContent.includes('fresh-agent'));
    assert.ok(freshRow.textContent.includes('—'), 'an agent with no cost sample should render a dash');
    const p1Row = projectRows()[0];
    assert.ok(p1Row.textContent.includes('$2.00'), 'the null-cost agent must not be counted as $0 in a way that hides the known total');
});

test('an empty payload renders the empty-state message, not a blank table', () => {
    cost.renderCostTable([]);
    assert.equal(projectRows().length, 0);
    assert.ok(document.querySelector('#cost-table .side-empty'), 'expected an empty-state message');
});

// WIRING — the render tests above call renderCostTable() with a hand-built fixture,
// which proves the RENDER but not the FETCH+JOIN. refreshCost() fetches /api/agents
// (for cwd) and /api/cost (for cost, scoped by the selected window) in parallel and
// joins them by agent name via the SAME project rule the sidebar uses
// (_agentProject). If it stopped threading either payload through, the table would
// render wrong or empty against healthy APIs and every test above would still pass.
test('refreshCost fetches agents+cost, joins by name, and groups by project like the sidebar', async () => {
    const requested = [];
    globalThis.fetch = (url) => {
        requested.push(String(url));
        if (String(url).includes('/api/cost')) {
            return Promise.resolve({
                ok: true, status: 200,
                json: () => Promise.resolve([
                    { name: 'cmx-92', model: 'Sonnet', cost_usd: 1.5 },
                    { name: 'cmx-88', model: 'Sonnet', cost_usd: 0.75 },
                ]),
            });
        }
        // Same rule the sidebar groups by (_agentProject): cwd BASENAME. Two
        // sessions sharing a repo root — e.g. two persistent project shells,
        // not dispatched worktree agents (those get their own hash dir and so
        // their own row, matching the sidebar's existing behaviour) — share
        // that basename and so land in one project bucket.
        return Promise.resolve({
            ok: true, status: 200,
            json: () => Promise.resolve([
                { name: 'cmx-92', cwd: '/home/x/projects/chelamux' },
                { name: 'cmx-88', cwd: '/home/x/projects/chelamux' },
            ]),
        });
    };
    await cost.refreshCost();
    assert.ok(requested.some(u => u.includes('/api/cost') && u.includes('window=live')),
        'refreshCost did not fetch /api/cost with the default (live) window');
    assert.ok(requested.some(u => u.includes('/api/agents') && !u.includes('/cost')), 'refreshCost did not fetch /api/agents for cwd');
    // Both agents' cwd basename is the SAME dir -> grouped as one project subtotal.
    assert.equal(projectRows().length, 1, 'both agents share a cwd basename and should collapse into one project');
    // The project row must be named after the cwd basename ('chelamux'), NOT the
    // '(unknown)' fallback bucket — if the /api/agents cwd payload stopped being
    // threaded through the name-join, every agent would fall back to '(unknown)'
    // and this count-only assertion above would still pass against a bucket that
    // is real but wrongly labelled.
    assert.ok(projectRows()[0].textContent.includes('chelamux'),
        'the project row should be named after the cwd basename (chelamux), not the (unknown) fallback');
    assert.ok(!projectRows()[0].textContent.includes('(unknown)'),
        'the cwd payload was not threaded through — agents fell back to the (unknown) bucket');
    assert.ok(totalRow().textContent.includes('$2.25'), 'fleet total should be 1.5 + 0.75 = 2.25');
});

test('refreshCost falls back to a grouping bucket when an agent has no resolvable cwd', async () => {
    globalThis.fetch = (url) => {
        if (String(url).includes('/api/cost')) {
            return Promise.resolve({
                ok: true, status: 200,
                json: () => Promise.resolve([{ name: 'lone-shell', model: null, cost_usd: 0.42 }]),
            });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([{ name: 'lone-shell', cwd: null }]) });
    };
    await cost.refreshCost();
    assert.equal(projectRows().length, 1);
    assert.ok(projectRows()[0].textContent.includes('(unknown)'), 'a cwd-less agent should land in the (unknown) bucket, not crash the render');
});

// WINDOW SELECTOR — setCostWindow() must (a) persist the pick so a reload doesn't
// silently reset to Live, (b) flip which button reads pressed, and (c) actually
// change the `window=` param on the next fetch — a selector that updates the UI
// but not the fetch would look right while showing stale (Live) data forever.
test('setCostWindow persists the pick, updates aria-pressed, and re-fetches with the new window', async () => {
    let lastCostUrl = null;
    globalThis.fetch = (url) => {
        if (String(url).includes('/api/cost')) lastCostUrl = String(url);
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    };
    await cost.setCostWindow('7d');
    assert.equal(localStorage.getItem('chela_cost_window'), '7d', 'the picked window should persist to localStorage');
    assert.ok(lastCostUrl && lastCostUrl.includes('window=7d'), 'setCostWindow(\'7d\') did not re-fetch /api/cost?window=7d');
    const liveBtn = document.querySelector('.cost-window-btn[data-win="live"]');
    const sevenDBtn = document.querySelector('.cost-window-btn[data-win="7d"]');
    assert.equal(liveBtn.getAttribute('aria-pressed'), 'false', 'Live should no longer read pressed after switching to 7d');
    assert.equal(sevenDBtn.getAttribute('aria-pressed'), 'true', '7d should read pressed after being selected');
    assert.ok(sevenDBtn.classList.contains('active'));
});

test('setCostWindow falls back to live for an unrecognized window key', async () => {
    let lastCostUrl = null;
    globalThis.fetch = (url) => {
        if (String(url).includes('/api/cost')) lastCostUrl = String(url);
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    };
    await cost.setCostWindow('nonsense');
    assert.ok(lastCostUrl && lastCostUrl.includes('window=live'), 'an unrecognized window key should fall back to live, not be sent verbatim');
});

// WIRING — every test above calls cost.setCostWindow() via the MODULE EXPORT.
// index.html's selector buttons don't import the module; they call
// onclick="chela.setCostWindow(...)" against the window.chela namespace. If cost.js
// stopped registering setCostWindow onto window.chela, every test above would still
// pass (they never touch window.chela) while every real click in the shipped
// dashboard would throw "chela.setCostWindow is not a function". This test drives
// the actual production entry point instead of the module export.
test('setCostWindow is reachable via window.chela — the entry point index.html\'s onclick actually calls', async () => {
    assert.equal(typeof window.chela.setCostWindow, 'function',
        'window.chela.setCostWindow must be registered; index.html\'s onclick="chela.setCostWindow(...)" is the only production entry point for the selector');
    let lastCostUrl = null;
    globalThis.fetch = (url) => {
        if (String(url).includes('/api/cost')) lastCostUrl = String(url);
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    };
    await window.chela.setCostWindow('30d');
    assert.ok(lastCostUrl && lastCostUrl.includes('window=30d'),
        'calling setCostWindow through window.chela did not drive the real fetch path');
    const thirtyDBtn = document.querySelector('.cost-window-btn[data-win="30d"]');
    assert.equal(thirtyDBtn.getAttribute('aria-pressed'), 'true',
        '30d should read pressed after being selected through the window.chela entry point');
});
