// THE COST TAB, IN A REAL DOM — a current-snapshot table over data chela already
// ingests (statusLine cost.total_cost_usd -> context_snapshots.cost_usd ->
// /api/agents/context). v1 is deliberately NOT a time-series: these tests drive
// the real renderCostTable()/refreshCost() into a real #cost-table (jsdom) and
// assert what RENDERS — project subtotals, agent rows, and the fleet-total
// footer — not what the source merely mentions.
//
// Run: node --test tests/cost.test.mjs  (pytest runs it via tests/test_js_suites.py)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = '<div class="panel" id="panel-cost"><div id="cost-table"></div></div>';

let cost;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
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
// (for cwd) and /api/agents/context (for cost) in parallel and joins them by agent
// name via the SAME project rule the sidebar uses (_agentProject). If it stopped
// threading either payload through, the table would render wrong or empty against
// healthy APIs and every test above would still pass.
test('refreshCost fetches agents+context, joins by name, and groups by project like the sidebar', async () => {
    const requested = [];
    globalThis.fetch = (url) => {
        requested.push(String(url));
        if (String(url).includes('/api/agents/context')) {
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
    assert.ok(requested.some(u => u.includes('/api/agents/context')), 'refreshCost did not fetch /api/agents/context');
    assert.ok(requested.some(u => u.includes('/api/agents') && !u.includes('/context')), 'refreshCost did not fetch /api/agents for cwd');
    // Both agents' cwd basename is the SAME dir -> grouped as one project subtotal.
    assert.equal(projectRows().length, 1, 'both agents share a cwd basename and should collapse into one project');
    assert.ok(totalRow().textContent.includes('$2.25'), 'fleet total should be 1.5 + 0.75 = 2.25');
});

test('refreshCost falls back to a grouping bucket when an agent has no resolvable cwd', async () => {
    globalThis.fetch = (url) => {
        if (String(url).includes('/api/agents/context')) {
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
