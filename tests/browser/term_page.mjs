// The ttyd page a wall pane loads, for the browser suites that drive one (term_rtl,
// term_boot_reveal): the --client-option set agent-terminals.sh launches ttyd with, the
// shims term_http injects into its <head>, and a real ttyd when one is installed.
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
export const FONTS = join(ROOT, 'chela', 'dashboard', 'static', 'fonts');
export const VENDOR = join(ROOT, 'chela', 'collab-relay', 'public', 'vendor');
const SCRIPT = join(ROOT, 'scripts', 'agent-terminals.sh');

// Every `--client-option` spawn() hands ttyd, with the script's own defaults expanded by
// bash (no CHELA_TERM_* override leaks in from this environment).
export function ttydClientOptions() {
    const sh = readFileSync(SCRIPT, 'utf8');
    const spawnBody = sh.slice(sh.indexOf('\nspawn() {'), sh.indexOf('\ndespawn() {'));
    const opts = spawnBody.split('\n')
        .map(l => l.match(/^\s*--client-option\s+(.+?)\s*\\$/))
        .filter(Boolean).map(m => m[1]);
    const defs = sh.split('\n').filter(l => /^(FONT_FAMILY|FONT_SIZE|TERM_THEME|TERM_RENDERER)=/.test(l));
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CHELA_TERM_')));
    const out = execFileSync('bash', ['-c', `${defs.join('\n')}\nprintf '%s\\0' ${opts.join(' ')}`], { env });
    return Object.fromEntries(out.toString('utf8').split('\0').filter(Boolean).map(kv => {
        const i = kv.indexOf('=');
        return [kv.slice(0, i), kv.slice(i + 1)];
    }));
}

// term_http's real terminal shims, from app.py (the interpreter agent-terminals.sh would
// pick: $PYTHON, else the repo venv, else python3).
export function termShims() {
    const python = process.env.PYTHON
        || (existsSync(join(ROOT, '.venv', 'bin', 'python')) ? join(ROOT, '.venv', 'bin', 'python') : 'python3');
    const out = execFileSync(python, ['-c',
        'import sys\nfrom chela.dashboard import app\n'
        + 'sys.stdout.write("\\0".join([app._TERM_FONT_CSS, app._TERM_FONT_PREF_SHIM,'
        + ' app._TERM_BOOT_CSS, app._term_theme_shim(), str(app.TERM_BOOT_FALLBACK_MS)]))'], { cwd: ROOT });
    const [fontCss, prefShim, bootCss, themeShim, fallbackMs] = out.toString('utf8').split('\0');
    return { fontCss, prefShim, bootCss, themeShim, fallbackMs: Number(fallbackMs) };
}

export const TTYD = process.env.TTYD || 'ttyd';
export const ttydMissing = () => (spawnSync(TTYD, ['--version']).status === 0 ? false
    : 'ttyd is not installed: the real-ttyd check DID NOT RUN (set TTYD=<path> to run it)');

function freePort() {
    return new Promise((res, rej) => {
        const srv = createServer();
        srv.on('error', rej);
        srv.listen(0, '127.0.0.1', () => { const { port } = srv.address(); srv.close(() => res(port)); });
    });
}

/** A ttyd launched with agent-terminals.sh's client options, running `command` (a bash
 * script). Resolves once it serves its page; `stop()` takes its process group down. */
export async function startTtyd(command, options = ttydClientOptions()) {
    const port = await freePort();
    const args = ['--interface', '127.0.0.1', '--port', String(port), '--writable'];
    for (const [k, v] of Object.entries(options)) args.push('--client-option', `${k}=${v}`);
    // Its own process group, so stop() takes the shell's children down with it.
    const proc = spawn(TTYD, [...args, 'bash', '-c', command],
        { stdio: 'ignore', detached: true, env: { ...process.env, LANG: 'C.UTF-8' } });
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 50; i++) {
        try { if ((await fetch(base + '/')).ok) break; } catch { /* not up yet */ }
        await new Promise(r => setTimeout(r, 100));
    }
    return { base, stop: () => { try { process.kill(-proc.pid, 'SIGTERM'); } catch { /* already gone */ } } };
}
