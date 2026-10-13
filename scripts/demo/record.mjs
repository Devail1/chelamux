// Record the README / landing demos from the SYNTHETIC demo fleet (fleet.py).
//
//   node scripts/demo/record.mjs <dashboard-url> <out-dir>
//
// Writes <out-dir>/{desktop,mobile}.frames/ — PNG frames captured over the
// Chrome DevTools screencast, plus a concat list with each frame's real
// duration — for record.sh to encode into MP4 + GIF. The screencast (not
// Playwright's recordVideo) is on purpose: recordVideo is a low-bitrate VP8
// that smears terminal text, and text is the whole picture here.
//
// ⛔ Point it ONLY at a dashboard fleet.py started. It refuses anything that is
// not a loopback URL AND the exact URL a live demo fleet recorded in its state
// file (whose root carries fleet.py's marker) — the operator's REAL dashboard is
// on loopback too, so loopback alone would let it on camera.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync, rmSync, readFileSync, existsSync } from 'node:fs';
import { join, isAbsolute } from 'node:path';
import { tmpdir } from 'node:os';

const [url, outDir] = process.argv.slice(2);
if (!url || !outDir) {
    console.error('usage: record.mjs <dashboard-url> <out-dir>');
    process.exit(2);
}
if (!/^http:\/\/127\.0\.0\.1:\d+\/?$/.test(url)) {
    console.error(`record.mjs: refusing ${url} — only a loopback demo dashboard (fleet.py) may be recorded`);
    process.exit(2);
}
// Same path fleet.py writes (its STATE_FILE: $CHELA_DEMO_STATE, else <tmpdir>/chela-demo-fleet.json).
const statePath = process.env.CHELA_DEMO_STATE || join(tmpdir(), 'chela-demo-fleet.json');
let state = null;
try { state = JSON.parse(readFileSync(statePath, 'utf8')); } catch { state = null; }
const slash = (u) => String(u).replace(/\/?$/, '/');
if (!state || typeof state.url !== 'string' || typeof state.root !== 'string'
        || !isAbsolute(state.root) || !existsSync(join(state.root, '.chela-demo-fleet'))
        || slash(state.url) !== slash(url)) {
    console.error(`record.mjs: refusing ${url} — it is not the dashboard of a running demo fleet `
        + `(${statePath}); start one with fleet.py up (record.sh does)`);
    process.exit(2);
}
if (process.env.CHELA_DEMO_CHECK_ONLY === '1') process.exit(0);   // tests: the fence, no browser

const THEME = 'warm';   // CMX-381's theme; any theme is fine for the media (Liav)

// A visible pointer + tap ripple + key-chord caption, drawn by the PAGE for the
// recording only: a headless screencast has no cursor, and "Ctrl+," is
// otherwise invisible. Nothing here touches the dashboard's own code.
function overlay() {
    const css = `
      #demo-cursor{position:fixed;z-index:2147483647;left:0;top:0;width:18px;height:18px;
        pointer-events:none;transform:translate(-100px,-100px);transition:transform .35s cubic-bezier(.2,.7,.2,1)}
      .demo-tap{position:fixed;z-index:2147483646;width:36px;height:36px;margin:-18px 0 0 -18px;
        border-radius:50%;background:rgba(255,255,255,.35);pointer-events:none;
        animation:demo-tap .5s ease-out forwards}
      @keyframes demo-tap{from{transform:scale(.4);opacity:1}to{transform:scale(1.6);opacity:0}}
      #demo-keys{position:fixed;z-index:2147483647;left:50%;bottom:28px;transform:translateX(-50%);
        padding:10px 18px;border-radius:10px;background:rgba(20,20,20,.88);color:#fff;
        font:600 22px/1 system-ui,sans-serif;letter-spacing:.02em;pointer-events:none;opacity:0;transition:opacity .2s}
      #demo-keys kbd{display:inline-block;padding:4px 9px;margin:0 3px;border-radius:6px;
        border:1px solid rgba(255,255,255,.35);font:inherit}`;
    const install = () => {
        if (document.getElementById('demo-cursor')) return;
        const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
        const cur = document.createElement('div'); cur.id = 'demo-cursor';
        cur.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24"><path d="M3 2l7.5 19 2.6-7.9L21 10.5z" fill="#fff" stroke="#111" stroke-width="1.5" stroke-linejoin="round"/></svg>';
        document.body.appendChild(cur);
        const keys = document.createElement('div'); keys.id = 'demo-keys'; document.body.appendChild(keys);
    };
    window.__demo = {
        move(x, y) { install(); document.getElementById('demo-cursor').style.transform = `translate(${x - 3}px,${y - 2}px)`; },
        hideCursor() { install(); document.getElementById('demo-cursor').style.transform = 'translate(-100px,-100px)'; },
        tap(x, y) {
            install();
            const t = document.createElement('div'); t.className = 'demo-tap';
            t.style.left = x + 'px'; t.style.top = y + 'px';
            document.body.appendChild(t); setTimeout(() => t.remove(), 600);
        },
        keys(html, ms) {
            install();
            const k = document.getElementById('demo-keys'); k.innerHTML = html; k.style.opacity = '1';
            setTimeout(() => { k.style.opacity = '0'; }, ms);
        },
    };
}

async function center(page, sel) {
    const el = page.locator(sel).first();
    await el.waitFor({ state: 'visible', timeout: 10000 });
    const b = await el.boundingBox();
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

// Move the drawn pointer to `sel`, let it land, then click it for real.
async function point(page, sel, { tap = false } = {}) {
    const { x, y } = await center(page, sel);
    if (tap) {
        await page.evaluate(([x, y]) => window.__demo.tap(x, y), [x, y]);
        await page.waitForTimeout(150);
        await page.locator(sel).first().tap();
        return;
    }
    await page.evaluate(([x, y]) => window.__demo.move(x, y), [x, y]);
    await page.waitForTimeout(450);
    await page.evaluate(([x, y]) => window.__demo.tap(x, y), [x, y]);
    await page.mouse.click(x, y);
}

// How long the last frame before a cut stays up, in seconds.
const CUT_HOLD = 0.15;

// Screencast frames with their real timestamps → an ffmpeg concat list.
async function screencast(page, dir) {
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    const cdp = await page.context().newCDPSession(page);
    const frames = [];
    let recording = false;
    cdp.on('Page.screencastFrame', async ({ data, metadata, sessionId }) => {
        cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {});
        if (!recording) return;
        const file = `f${String(frames.length).padStart(5, '0')}.png`;
        writeFileSync(join(dir, file), Buffer.from(data, 'base64'));
        frames.push({ file, ts: metadata.timestamp });
    });
    const vp = page.viewportSize();
    await cdp.send('Page.startScreencast', { format: 'png', maxWidth: vp.width, maxHeight: vp.height, everyNthFrame: 1 });
    return {
        start() { recording = true; },
        // Drop frames while paused: a jump cut over a transient the clip should not show.
        // The frame on screen when it paused is marked, so it does not last the whole gap.
        pause() {
            recording = false;
            if (frames.length) frames[frames.length - 1].cut = true;
        },
        async stop() {
            recording = false;
            await cdp.send('Page.stopScreencast');
            // Each frame lasts until the next one arrived — except the last before a cut,
            // which gets a beat (CUT_HOLD); the very last one gets 1s.
            const lines = [];
            frames.forEach((f, i) => {
                const next = frames[i + 1];
                const d = !next ? 1 : f.cut ? CUT_HOLD : Math.max(0.001, next.ts - f.ts);
                lines.push(`file '${f.file}'`, `duration ${d.toFixed(3)}`);
            });
            if (frames.length) lines.push(`file '${frames[frames.length - 1].file}'`);
            writeFileSync(join(dir, 'frames.txt'), lines.join('\n') + '\n');
            return frames.length;
        },
    };
}

async function newPage(browser, viewport, mobile) {
    const context = await browser.newContext({
        viewport, deviceScaleFactor: 1, isMobile: mobile, hasTouch: mobile,
    });
    await context.addInitScript(theme => {
        try {
            localStorage.setItem('chela_theme', theme);
            localStorage.setItem('pc_term_mode', 'wall');
            localStorage.setItem('pc_wall_preset', JSON.stringify({ cols: 2, rows: 2 }));
            localStorage.setItem('chela_work_segment', 'board');
        } catch (e) { /* storage unavailable — defaults still boot */ }
    }, THEME);
    await context.addInitScript(overlay);
    const page = await context.newPage();
    await page.goto(url);
    // The terminals need a few seconds to connect and paint; none of it is recorded.
    await page.waitForSelector('.gs-head', { timeout: 20000 });
    await page.waitForTimeout(8000);
    await page.evaluate(() => window.__demo.hideCursor());
    return { context, page };
}

// The sidebar beats address a row by its window NAME (fleet.py's AGENTS), never by its
// label: the label is Claude's title, and an archived row reads its window name instead.
const row = (name) => `#sidebar-agents .agent-row[data-agent="${name}"]`;

async function desktop(browser) {
    const { context, page } = await newPage(browser, { width: 1440, height: 900 }, false);
    const rec = await screencast(page, join(outDir, 'desktop.frames'));
    rec.start();
    // The Wall: four panes, four states. The sidebar beside it groups them by folder, the
    // blocked one on top under "Needs you", the orchestrator's row badged (CMX-76).
    await page.waitForTimeout(3200);
    // Hold Alt: every row shows its number (CMX-85); Alt+1 jumps to the first.
    await page.evaluate(() => { window.__demo.hideCursor(); window.__demo.keys('hold <kbd>Alt</kbd>', 1500); });
    await page.keyboard.down('Alt');
    await page.waitForTimeout(1500);
    await page.evaluate(() => window.__demo.keys('<kbd>Alt</kbd> + <kbd>1</kbd>', 1200));
    await page.keyboard.press('Digit1');
    await page.waitForTimeout(1200);
    await page.keyboard.up('Alt');
    await page.waitForTimeout(800);
    // Archive the finished session: it CLOSES (CMX-75) — its pane leaves the Wall...
    await point(page, `${row('worker')} .row-more`);
    await page.waitForTimeout(700);
    await point(page, '#row-menu [data-act="archive"]');
    await page.waitForTimeout(900);
    // Right after a window closes, the sidebar briefly labels the live rows by their bare
    // window id ("@2") until the next agents poll brings their titles back. Cut over it.
    rec.pause();
    await page.waitForFunction(() => {
        const names = [...document.querySelectorAll('#sidebar-agents .agent-row:not(.archived) .agent-row-name')];
        return names.length > 0 && names.every(n => !/^@\d+$/.test(n.textContent.trim()));
    }, null, { timeout: 20000 });
    rec.start();
    await page.waitForTimeout(1300);
    // ...and Unarchive resumes it in a new window.
    await point(page, '.side-archived-toggle');
    await page.waitForTimeout(800);
    await point(page, '.side-archived .group-head');
    await page.waitForTimeout(900);
    await point(page, '.side-archived .row-unarchive');
    await page.waitForTimeout(3000);                      // its pane comes back on the Wall
    // The resumed row flips between "Other" and its folder group until chela has settled
    // its cwd (~10s) — cut to it settled: in its own folder for 3s straight.
    rec.pause();
    await page.waitForFunction(() => {
        const home = document.querySelector('#sidebar-agents .side-group[data-g$="/worker"] .agent-row[data-agent="worker"]');
        const now = Date.now();
        if (!home) { window.__demoSince = 0; return false; }
        window.__demoSince = window.__demoSince || now;
        return now - window.__demoSince >= 3000;
    }, null, { timeout: 40000, polling: 100 });
    rec.start();
    await page.waitForTimeout(1200);
    await point(page, '.side-archived-toggle');           // Hide archived — back as it was
    await page.waitForTimeout(900);
    await point(page, '.side-item[data-view="work"]');    // → Work (board)
    await page.waitForTimeout(2600);
    await point(page, '#work-seg .work-seg-btn[data-seg="runs"]');
    await page.waitForTimeout(2000);
    await point(page, '#work-seg .work-seg-btn[data-seg="schedules"]');
    await page.waitForTimeout(2000);
    await point(page, '.side-item[data-view="terminals"], .side-item[data-view="wall"]');
    await page.waitForTimeout(1200);
    await page.evaluate(() => { window.__demo.hideCursor(); window.__demo.keys('<kbd>Ctrl</kbd> + <kbd>,</kbd>', 1400); });
    await page.waitForTimeout(350);
    await page.keyboard.press('Control+Comma');           // CMX-385
    await page.waitForTimeout(1500);
    await page.locator('#settings-search').pressSequentially('theme', { delay: 90 });
    await page.waitForTimeout(2200);
    const n = await rec.stop();
    await context.close();
    return n;
}

// A phone pill is keyed by its window id and labelled by Claude's title, so find it by
// window NAME through the dashboard's own agent list (the desktop clip's unarchive gave
// `worker` a new window id).
async function pills(page) {
    const wids = await page.evaluate(async () => {
        const r = await fetch('/api/agents');
        const d = await r.json();
        const out = {};
        for (const a of (Array.isArray(d) ? d : d.agents || [])) if (a && a.window_id) out[a.name] = a.window_id;
        return out;
    });
    return (name) => {
        if (!wids[name]) throw new Error(`record.mjs: no window named ${name} in the demo fleet`);
        return `.term-pill[data-wid="${wids[name]}"]`;
    };
}

async function mobile(browser) {
    const { context, page } = await newPage(browser, { width: 390, height: 844 }, true);
    const pill = await pills(page);
    // Visit every pane once, off the record: a pane's first connect is a blank
    // iframe plus ttyd's size overlay, and that is not what switching looks like.
    for (const name of ['docs-site', 'worker', 'infra', 'api-server']) {
        await page.locator(pill(name)).first().tap();
        await page.waitForTimeout(2500);
    }
    const rec = await screencast(page, join(outDir, 'mobile.frames'));
    rec.start();
    await page.waitForTimeout(1600);
    await point(page, pill('docs-site'), { tap: true });
    await page.waitForTimeout(1600);
    await point(page, pill('worker'), { tap: true });
    await page.waitForTimeout(1600);
    await point(page, '#btn-new-mobile', { tap: true });  // CMX-386
    await page.waitForTimeout(2000);
    await page.keyboard.press('Escape');
    await page.mouse.click(200, 600).catch(() => {});
    await page.waitForTimeout(500);
    await point(page, pill('api-server'), { tap: true });
    await page.waitForTimeout(700);
    await page.locator('#term-keybar').evaluate(el => el.scrollTo({ left: el.scrollWidth, behavior: 'smooth' }));
    await page.waitForTimeout(1300);
    await page.locator('#term-keybar').evaluate(el => el.scrollTo({ left: 0, behavior: 'smooth' }));
    await page.waitForTimeout(1200);
    const n = await rec.stop();
    await context.close();
    return n;
}

const browser = await chromium.launch();
try {
    const which = process.env.DEMO_ONLY;
    if (!which || which === 'desktop') console.log(`desktop: ${await desktop(browser)} frames`);
    if (!which || which === 'mobile') console.log(`mobile: ${await mobile(browser)} frames`);
} finally {
    await browser.close();
}
