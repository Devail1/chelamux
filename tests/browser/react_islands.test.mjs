// CMX-178 — the React islands on a REAL dashboard: Flask's own index(), its manifest read and
// its static route, the committed build, and the legacy modules beside it.
//
// ⛔ ISOLATED: its own `chela dashboard` process on a free loopback port, with a temp
// CHELA_DIR, no env file and a tmux session name that does not exist. Never the live
// dashboard, never a restart of it (fixture.mjs explains the 2026-09-28 port-file incident).
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchChromium } from './fixture.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const VENV_PY = join(ROOT, '.venv', 'bin', 'python');

const { browser, why } = await launchChromium();

function freePort() {
    return new Promise((resolve, reject) => {
        const srv = createServer().once('error', reject).listen(0, '127.0.0.1', () => {
            const { port } = srv.address();
            srv.close(() => resolve(port));
        });
    });
}

let dash = null;
let state = null;
let base = '';
let version = '';
let failed = null;
before(async () => {
    if (!browser) return;
    try {
        const port = await freePort();
        state = mkdtempSync(join(tmpdir(), 'cmx178-'));
        const env = Object.fromEntries(Object.entries(process.env)
            .filter(([k]) => !k.startsWith('CHELA_') && !k.startsWith('NODE_CHANNEL')));
        Object.assign(env, {
            CHELA_DIR: join(state, 'chela'), CHELA_ENV_FILE: '', CHELA_DASH_HOST: '127.0.0.1',
            CHELA_DASHBOARD_PORT: String(port), CHELA_TMUX_SESSION: `cmx178-${port}-nonexistent`,
        });
        dash = spawn(existsSync(VENV_PY) ? VENV_PY : 'python3', ['-m', 'chela.main', 'dashboard'],
            { cwd: ROOT, env, stdio: 'ignore' });
        base = `http://127.0.0.1:${port}`;
        for (let i = 0; i < 150 && !version; i++) {
            assert.equal(dash.exitCode, null, 'the isolated dashboard exited');
            try { version = (await (await fetch(`${base}/api/version`)).json()).version; }
            catch { await new Promise(r => setTimeout(r, 200)); }
        }
        assert.ok(version, 'the isolated dashboard never answered /api/version');
    } catch (e) { failed = e; }
});
after(async () => {
    if (dash) { dash.kill('SIGKILL'); await new Promise(r => (dash.exitCode !== null || dash.signalCode) ? r() : dash.once('exit', r)); }
    if (state) rmSync(state, { recursive: true, force: true });
    if (browser) await browser.close();
});

test('the version island renders the real version in the Settings modal beside the legacy sidebar and wall', { skip: why }, async () => {
    if (failed) throw failed;
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(`${base}/`);
    const badge = page.locator('#version-island .version-badge');
    // CMX-291: the badge lives in the Settings modal's foot, on screen once the modal is open
    // (CMX-221: the modal is a React dialog, drawn only while open).
    await page.waitForFunction(() => typeof window.chela?.toggleSettings === 'function', null, { timeout: 15_000 });
    await page.evaluate(() => window.chela.toggleSettings());
    await page.waitForFunction(() => { const m = document.getElementById('settings-drawer'); return !!m && getComputedStyle(m).opacity === '1'; },
        null, { timeout: 5_000 });
    await badge.waitFor({ timeout: 15_000 });
    assert.equal(await badge.textContent(), `build ${version}`);
    assert.ok(await badge.isVisible(), 'the badge is not on screen in the open Settings modal');
    await page.evaluate(() => window.chela.toggleSettings());
    // Legacy ran: nav.js loaded the sidebar layout and published it through the bridge, and
    // terminals.js drew the wall's preset picker.
    await page.waitForFunction(() => window.chelaBridge?.sidebarLayout.get().loaded === true, null, { timeout: 15_000 });
    assert.ok(await page.locator('aside.sidebar').isVisible(), 'the legacy sidebar is on screen');
    assert.ok(await page.locator('#panel-terminals').isVisible(), 'the wall is on screen');
    await page.locator('#term-grid-presets > *').first().waitFor({ timeout: 15_000 });
    assert.deepEqual(errors, []);
    await page.close();
});

// CMX-276 (CMX-121): on the real page, at the default sidebar width, a row's TITLE keeps its
// room and the metadata gives way first; with a short title the metadata shows in full.
test('a sidebar row gives its title the width before its metadata', { skip: why }, async () => {
    if (failed) throw failed;
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(`${base}/`);
    await page.waitForFunction(() => window.chelaBridge?.sidebarLayout.get().loaded === true, null, { timeout: 15_000 });
    const measure = title => page.evaluate(t => {
        const a = { name: 'w', window_id: '@1', cwd: '/srv/code/alpha', session_status: 'busy' };
        window.chelaBridge.sidebar.set({
            agents: [a], wants: () => false, orchWid: '@1', order: [], archived: [], sessions: [],
            view: { status: 'active', env: ['interactive'], activity: 'all', groupBy: 'folder', sort: 'name', showEmpty: false, showPR: true },
            custom: { groups: [], assign: {} }, labels: { '@1': t }, now: Date.now(), collapsed: [],
            ctx: { '@1': 91 }, onWall: [], active: null, altHeld: false,
        });
        const box = sel => document.querySelector(`#sidebar-agents .agent-row ${sel}`).getBoundingClientRect();
        return { name: box('.agent-row-name').width, meta: box('.ar-meta').width, crown: box('.ar-orch').width };
    }, title);
    const long = await measure('A very long session title that would never fit in a sidebar row at all');
    assert.ok(long.name >= 150, `the title got ${long.name}px at the default width`);
    assert.ok(long.meta < 20, `the metadata kept ${long.meta}px beside a title that needs the room`);
    const short = await measure('Hi');
    assert.ok(short.crown >= 10, `with a short title the crown shows (${short.crown}px)`);
    assert.ok(short.meta > long.meta, 'the metadata has room once the title does not need it');
    assert.deepEqual(errors, []);
    await page.close();
});

// CMX-278: the width handle on the real page. A drag widens the column (the canvas gives way),
// the release saves it to the SERVER layout, and a fresh page (another device) opens at that
// width. The phone drawer and the collapsed rail ignore it.
test('dragging the sidebar edge resizes it, the width persists via the layout API, clamped', { skip: why }, async () => {
    if (failed) throw failed;
    const open = async viewport => {
        const page = await browser.newPage({ viewport });
        const errors = [];
        page.on('pageerror', e => errors.push(String(e)));
        await page.goto(`${base}/`);
        await page.waitForFunction(() => window.chelaBridge?.sidebarLayout.get().loaded === true, null, { timeout: 15_000 });
        return { page, errors };
    };
    const width = page => page.evaluate(() => Math.round(document.querySelector('aside.sidebar').getBoundingClientRect().width));
    const saved = async () => (await (await fetch(`${base}/api/sidebar/layout`)).json()).layout?.width ?? null;

    const { page, errors } = await open({ width: 1440, height: 900 });
    assert.equal(await width(page), 288, 'the default width');
    const h = await page.locator('.sidebar-resize').boundingBox();
    const y = h.y + h.height / 2, x = h.x + h.width / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 60, y, { steps: 4 });
    await page.mouse.move(x + 100, y, { steps: 4 });
    assert.equal(await width(page), 388, 'the column follows the drag');
    await page.mouse.up();
    await page.waitForFunction(() => window.chelaBridge.sidebarLayout.get().width === 388, null, { timeout: 5_000 });
    for (let i = 0; i < 50 && await saved() !== 388; i++) await new Promise(r => setTimeout(r, 100));
    assert.equal(await saved(), 388, 'the width is not in the server layout');
    const canvas = await page.evaluate(() => Math.round(document.getElementById('canvas').getBoundingClientRect().left));
    assert.equal(canvas, 388, 'the canvas did not give way to the wider column');

    // a drag far past the edge stops at the bounds
    await page.mouse.move(x + 100, y);
    await page.mouse.down();
    await page.mouse.move(x + 900, y, { steps: 4 });
    await page.mouse.up();
    assert.equal(await width(page), 480);
    for (let i = 0; i < 50 && await saved() !== 480; i++) await new Promise(r => setTimeout(r, 100));
    assert.equal(await saved(), 480);
    assert.deepEqual(errors, []);
    await page.close();

    // another device opens at the saved width; its collapsed rail hides the handle
    const other = await open({ width: 1280, height: 800 });
    await other.page.waitForFunction(() => Math.round(document.querySelector('aside.sidebar').getBoundingClientRect().width) === 480,
        null, { timeout: 5_000 });
    await other.page.evaluate(() => window.chela.toggleSidebar());
    assert.equal(await width(other.page), 60, 'the collapsed rail is not 60px');
    assert.equal(await other.page.locator('.sidebar-resize').isVisible(), false, 'the rail shows the handle');
    await other.page.evaluate(() => window.chela.toggleSidebar());
    await other.page.close();

    // the phone drawer keeps its own width, and has no handle
    const phone = await open({ width: 390, height: 844 });
    await phone.page.evaluate(() => window.chela.toggleSidebar(true));
    assert.equal(await width(phone.page), 264);
    assert.equal(await phone.page.locator('.sidebar-resize').isVisible(), false, 'the phone drawer shows the handle');
    assert.deepEqual(phone.errors, []);
    await phone.page.close();
});
