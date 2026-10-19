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

test('the version island renders the real version beside the legacy sidebar and wall', { skip: why }, async () => {
    if (failed) throw failed;
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(`${base}/`);
    const badge = page.locator('#version-island .version-badge');
    await badge.waitFor({ timeout: 15_000 });
    assert.equal(await badge.textContent(), `build ${version}`);
    assert.ok(await badge.isVisible(), 'the badge is on screen');
    // Legacy ran: nav.js loaded the sidebar layout and published it through the bridge, and
    // terminals.js drew the wall's preset picker.
    await page.waitForFunction(() => window.chelaBridge?.sidebarLayout.get().loaded === true, null, { timeout: 15_000 });
    assert.ok(await page.locator('aside.sidebar').isVisible(), 'the legacy sidebar is on screen');
    assert.ok(await page.locator('#panel-terminals').isVisible(), 'the wall is on screen');
    await page.locator('#term-grid-presets > *').first().waitFor({ timeout: 15_000 });
    assert.deepEqual(errors, []);
    await page.close();
});
