// CMX-383 — the real-browser harness's fixture: the dashboard's REAL shell
// (templates/index.html + chela/dashboard/static, byte-for-byte) served to a
// real Chromium, with every API response stubbed through Playwright request
// routing.
//
// ⛔ It never starts `chela dashboard` or the daemon: both read and write the
// LIVE ~/.chela (the 2026-09-28 port-file incident). No tmux, no ttyd — a
// terminal iframe's /term/<wid>/ src is answered with a blank page. And no
// network: the page lives on a fake origin nothing resolves, and every request
// that is not for that origin is ABORTED, so a test can never reach out.
import { readFileSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, extname, normalize, sep } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const STATIC = join(ROOT, 'chela', 'dashboard', 'static');
const TEMPLATE = join(ROOT, 'chela', 'dashboard', 'templates', 'index.html');

export const ORIGIN = 'http://chela.test';

// One agent per status state. The sidebar row and the pane header each derive
// the state through their OWN real code (nav.js / terminals.js tileState) from
// these same fields, so matching marks are a fact about the product, not the
// fixture. @2 is shared so `.safety-float`'s kill-switch pill is on screen.
// ai_title on EVERY agent: it is what renders the pane header's dim subtitle
// (terminals.js paneHead), and a fixture without one never draws the subtitle at
// all — so a guard on "title and subtitle share one line" had nothing to measure
// (judge on #545, round 1: flex-direction:column on .gs-grip survived).
export const AGENTS = [
    { name: 'a-working', window_id: '@1', online: true, session_status: 'busy', cwd: '/p/x', ai_title: 'Refactor the flux capacitor' },
    { name: 'b-waiting', window_id: '@2', online: true, session_status: 'waiting', needs_human: true, shared: true, cwd: '/p/x', ai_title: 'Refactor the flux capacitor' },
    { name: 'c-idle', window_id: '@3', online: true, session_status: 'idle', cwd: '/p/x', ai_title: 'Refactor the flux capacitor' },
    { name: 'd-done', window_id: '@4', online: true, session_status: 'idle', done: true, pr: { url: 'https://example.invalid/pr/1' }, cwd: '/p/x', ai_title: 'Refactor the flux capacitor' },
];
export const STATE_OF = { '@1': 'working', '@2': 'waiting', '@3': 'idle', '@4': 'done' };

const TYPES = {
    '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
    '.png': 'image/png', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.json': 'application/json',
    '.html': 'text/html', '.txt': 'text/plain',
};

// What Flask's render_template would produce with terminals enabled and the
// dispatched-tile flag off. The template's only Jinja is `{% if flag %}…{% endif %}`
// pairs; anything else is a loud error rather than a silently mis-rendered shell.
export function renderShell() {
    const flags = { terminals_enabled: true, wall_tile_dispatched: false };
    const html = readFileSync(TEMPLATE, 'utf8').replace(
        /\{%\s*if\s+(\w+)\s*%\}([\s\S]*?)\{%\s*endif\s*%\}/g,
        (_, flag, inner) => {
            if (!(flag in flags)) throw new Error(`renderShell: unknown template flag ${flag}`);
            return flags[flag] ? inner : '';
        });
    if (/\{%|\{\{/.test(html)) throw new Error('renderShell: unrendered Jinja left in templates/index.html');
    return html;
}

function apiBody(path) {
    if (path === '/api/agents') return AGENTS;
    if (path === '/api/agents/context') return [];
    if (path === '/api/summary') return { windows_total: AGENTS.length };
    if (path === '/api/agents/status_health') return { ok: true };
    if (path === '/api/rooms') return { rooms: {}, pending: [] };
    if (path === '/api/term/ready') return { ready: true };
    if (path === '/api/term/shared') return { shared: ['@2'] };
    // The sidebar foot's CPU/RAM/Disk strip stays hidden until a sample lands;
    // CMX-393's one-row footer guard needs it drawn.
    if (path === '/api/resources') return { cpu_pct: 8, mem_pct: 13, disk_pct: 31 };
    return {};
}

function staticFile(rel) {
    const file = normalize(join(STATIC, rel));
    if (!file.startsWith(STATIC + sep) || !existsSync(file) || !statSync(file).isFile()) return null;
    return file;
}

/** Route every request of `context` to the fixture. Returns the list of
 * requests it had to 404 or abort, so a test can assert none were unexpected. */
export async function routeFixture(context) {
    const misses = [];
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== ORIGIN) {
            misses.push(`ABORTED off-fixture request: ${url.href}`);
            return route.abort('blockedbyclient');
        }
        const path = url.pathname;
        if (path === '/') {
            return route.fulfill({ status: 200, contentType: 'text/html', body: renderShell() });
        }
        if (path.startsWith('/static/')) {
            const file = staticFile(decodeURIComponent(path.slice('/static/'.length)));
            if (!file) {
                misses.push(`404 ${path}`);
                return route.fulfill({ status: 404, body: '' });
            }
            return route.fulfill({
                status: 200, body: readFileSync(file),
                contentType: TYPES[extname(file)] || 'application/octet-stream',
            });
        }
        if (path.startsWith('/term/')) {
            // Stub terminal: a blank page in the ttyd background colour.
            return route.fulfill({
                status: 200, contentType: 'text/html',
                body: '<!doctype html><html><body style="margin:0;background:#0d1117"></body></html>',
            });
        }
        if (path === '/api/events') {
            // 204 tells EventSource to stop reconnecting; the poll paths still run.
            return route.fulfill({ status: 204, body: '' });
        }
        if (path.startsWith('/api/') || path.startsWith('/hooks/')) {
            return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(apiBody(path)) });
        }
        misses.push(`404 ${path}`);
        return route.fulfill({ status: 404, body: '' });
    });
    return misses;
}

// Printed verbatim by a suite that could not run, so tests/test_js_suites.py
// can turn node's quiet per-test skip into a LOUD pytest skip naming the cause.
export const DID_NOT_RUN = 'browser suite DID NOT RUN';

/** Launch Chromium. Returns `{ browser }`, or `{ why }` when it cannot. A
 * missing browser is treated exactly the way tests/test_js_suites.py treats a
 * missing node or jsdom: under CHELA_REQUIRE_JS_TESTS it THROWS (the suite
 * file fails), never a silent green; otherwise every test skips with `why`. */
export async function launchChromium() {
    let why;
    try {
        const { chromium } = await import('playwright');
        return { browser: await chromium.launch() };
    } catch (e) {
        why = e.code === 'ERR_MODULE_NOT_FOUND'
            ? 'the playwright package is not installed — run `npm ci`'
            : `Chromium could not launch (${String(e.message).split('\n')[0]}) — run ` +
              '`npx playwright install --only-shell chromium`';
    }
    const msg = `${DID_NOT_RUN}: ${why}`;
    if (process.env.CHELA_REQUIRE_JS_TESTS) {
        throw new Error(msg + ' (CHELA_REQUIRE_JS_TESTS is set: a silent skip is not green)');
    }
    console.log(msg);
    return { why: msg };
}

/** A fresh context + page on the fixture at `viewport`, the Wall in a 2×2
 * preset, booted and settled. */
export async function openDashboard(browser, { width, height, deviceScaleFactor = 1 }) {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor });
    await context.addInitScript(() => {
        try {
            localStorage.setItem('pc_term_mode', 'wall');
            localStorage.setItem('pc_wall_preset', JSON.stringify({ cols: 2, rows: 2 }));
        } catch (e) { /* storage unavailable — the defaults still boot the wall */ }
    });
    const misses = await routeFixture(context);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(ORIGIN + '/');
    await page.evaluate(() => document.fonts.ready);
    return { context, page, misses, errors };
}
