// SETTINGS → COST tab → USAGE view (CMX-38), in a REAL DOM — same harness as
// tests/settings_cost.test.mjs: the real settings modal sliced from index.html,
// the real stylesheet, the real nav.js/usage.js module graph via main.js, and a
// mocked /api/usage. Every switch is driven through the REAL rendered onclick
// attribute (DEFEAT_SHAPES #5), not by calling the handler directly.
//
// Properties:
//   1. The "Cost | Usage" toggle swaps panes and fetches /api/usage.
//   2. A limit with used_pct null renders as UNKNOWN — never "0%".
//   3. A cache_broken row carries the WORD and the SHAPE (▲ CACHE BROKEN); a
//      healthy row carries neither. Rows render in the order served
//      (heaviest weighted first).
//   4. The 30m/Today switch re-renders from that window's rows.
//
// Run: node --test tests/settings_usage.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const HTML = readFileSync(join(ROOT, 'templates', 'index.html'), 'utf8');
const CSS = readFileSync(join(ROOT, 'static', 'style.css'), 'utf8');
const SETTINGS_START = HTML.indexOf('<div class="drawer-scrim" id="drawer-scrim"');
const SETTINGS_END = HTML.indexOf('<!-- "+ new" popover');
if (SETTINGS_START < 0 || SETTINGS_END < 0) throw new Error('index.html markers for the settings modal moved — update this test');
const BODY = HTML.slice(SETTINGS_START, SETTINGS_END);

const NOW = Math.floor(Date.now() / 1000);
const ROW = (o) => ({
    session_id: 'x', requests: 20, input: 100, cache_write: 0, cache_read: 0, output: 50,
    weighted: 1000, cache_hit: 0.95, cache_broken: false, model: 'claude-opus-5-5', spark: [0, 1, 3], ...o,
});
const PAYLOAD = {
    limits: {
        five_hour: { used_pct: null, reason: 'newest sample is 45 min old' },
        seven_day: { used_pct: 42, resets_at: NOW + 3 * 86400, burn_pct_per_h: 1.5,
            projected_pct: 150, hits_100_before_reset: true },
    },
    windows: {
        '30m': { rows: [
            ROW({ label: 'open-mmo · bbbbbbbb', weighted: 9_000_000, cache_hit: 0.04, cache_broken: true }),
            ROW({ label: 'review-west', weighted: 40_000 }),
        ] },
        today: { rows: [ROW({ label: 'night-shift', weighted: 77 })] },
    },
    roots: { extra: ['/mnt/c/Users/*/.claude/projects'], scanned: ['/h/.claude/projects'] },
};

let fetchCalls = [];
function flush() { return new Promise(r => setTimeout(r, 0)); }

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><head><style>${CSS}</style></head><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    globalThis.fetch = (url, opts) => {
        const u = String(url);
        fetchCalls.push({ url: u, opts: opts || null });
        const body = u.startsWith('/api/usage') ? PAYLOAD
            : (u.startsWith('/api/agents') || u.startsWith('/api/cost')) ? [] : {};
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = true;
    await import('../chela/dashboard/static/js/main.js');
});

beforeEach(() => {
    document.getElementById('settings-drawer').classList.remove('open');
    localStorage.removeItem('chela_cost_view');
    localStorage.removeItem('chela_usage_window');
    fetchCalls = [];
});

function click(sel) {
    const el = document.querySelector(sel);
    assert.ok(el, `no ${sel} rendered`);
    new Function('chela', el.getAttribute('onclick') || '').call(el, window.chela);
}

async function openUsage() {
    window.chela.toggleSettings();
    await flush();
    window.chela.selectSettingsTab('cost');
    await flush(); await flush();
    click('#cost-view .cost-view-btn[data-view="usage"]');
    await flush(); await flush();
}

test('the Cost | Usage toggle swaps panes and fetches /api/usage', async () => {
    window.chela.toggleSettings();
    await flush();
    window.chela.selectSettingsTab('cost');
    await flush(); await flush();
    assert.equal(document.getElementById('usage-pane').hidden, true, 'Usage pane shown before choosing it');
    assert.equal(document.getElementById('cost-pane').hidden, false);
    fetchCalls = [];
    click('#cost-view .cost-view-btn[data-view="usage"]');
    await flush(); await flush();
    assert.equal(document.getElementById('usage-pane').hidden, false, 'Usage pane not shown');
    assert.equal(document.getElementById('cost-pane').hidden, true, 'Cost pane still shown');
    assert.ok(fetchCalls.some(c => c.url.endsWith('/api/usage')), 'switching to Usage did not fetch /api/usage');
    const on = document.querySelector('#cost-view .cost-view-btn.active');
    assert.equal(on && on.dataset.view, 'usage');
    assert.equal(on.getAttribute('aria-pressed'), 'true');
});

test('a limit with no fresh sample renders UNKNOWN, never 0%', async () => {
    await openUsage();
    const five = document.querySelector('.usage-limit[data-limit="5h"]');
    assert.ok(five, 'no 5h limit bar');
    assert.ok(five.classList.contains('unknown'), '5h bar not marked unknown');
    assert.equal(five.querySelector('.usage-limit-val').textContent.trim(), 'unknown');
    assert.doesNotMatch(five.textContent, /\b0%/, 'unknown limit rendered as 0%');
    assert.match(five.textContent, /45 min old/, 'unknown limit hides its reason');
    const seven = document.querySelector('.usage-limit[data-limit="7d"]');
    assert.equal(seven.querySelector('.usage-limit-val').textContent.trim(), '42%');
    assert.equal(seven.querySelector('.usage-bar-fill').style.width, '42%');
    assert.match(seven.textContent, /REACHES 100% BEFORE RESET/);
});

test('a cache-broken row says so in words and a shape; a healthy row does not', async () => {
    await openUsage();
    const rows = [...document.querySelectorAll('#usage-table tbody tr')];
    assert.equal(rows.length, 2);
    assert.match(rows[0].cells[0].textContent, /open-mmo/, 'heaviest row is not first');
    assert.match(rows[0].querySelector('.usage-hit').textContent, /▲ CACHE BROKEN/);
    assert.match(rows[0].querySelector('.usage-hit').textContent, /4%/);
    assert.doesNotMatch(rows[1].textContent, /CACHE BROKEN|▲/);
    assert.match(rows[1].querySelector('.usage-hit').textContent, /95%/);
});

test('the 30m / Today switch re-renders from that window', async () => {
    await openUsage();
    click('#usage-window .usage-window-btn[data-win="today"]');
    await flush();
    const rows = [...document.querySelectorAll('#usage-table tbody tr')];
    assert.equal(rows.length, 1);
    assert.match(rows[0].cells[0].textContent, /night-shift/);
    const on = document.querySelector('#usage-window .usage-window-btn.active');
    assert.equal(on && on.dataset.win, 'today');
});
