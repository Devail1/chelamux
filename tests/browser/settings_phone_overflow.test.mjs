// CMX-57 — Settings at phone width: nothing may scroll sideways.
//
// Liav, on a phone (2026-10-09): the tab strip (General · Timing · Dispatch ·
// Notifications · Cost · A…) ran off the screen, and the Update card's
// `pm2 restart chela-agent-terminals chela-daemon chela-telegram` broke
// mid-word with a ragged indent. jsdom has no layout engine, so a "no
// horizontal overflow" claim is only measurable in a real browser: this suite
// opens every Settings tab (and the Cost tab's Usage view) at 390 and 360 CSS px,
// as a touch device (CMX-409's 16px controls apply), in two themes, and reads scrollWidth vs clientWidth on
//   - the document and the modal itself (no page or dialog scrolls sideways),
//   - the modal's own box (inside the viewport),
//   - the tab panel and EVERY row-shaped element in it (.s-row, .s-status-row,
//     .s-ex, .s-kv, .settings-section, …): a row whose content is wider than the
//     row is a field poking out of its card,
//   - the tab strip: it may scroll sideways ON ITS OWN, but then it must say so
//     (a visible edge-fade affordance), and its last tab must stay reachable
//     inside it rather than past the modal's edge.
//
// The payloads are realistic on purpose: the real knob labels the server sends
// (config.timing_snapshot / dispatch_snapshot), a stale-services Update status
// carrying the exact pm2 command from the report, a long ntfy/Telegram example,
// a usage table with long session labels. A fixture of short strings could never
// overflow, and so could never fail.
//
// Harness: tests/browser/fixture.mjs. Local: `pnpm run test:browser`.
// Screenshots for a PR: CMX57_SHOTS=<dir> pnpm exec node --test tests/browser/settings_phone_overflow.test.mjs
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const SHOTS = process.env.CMX57_SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// --- realistic payloads ------------------------------------------------------

const knob = (key, label, unit, dflt, extra = {}) => ({
    key, env: 'CHELA_' + key.toUpperCase(), label, unit, default: dflt,
    stored: '', effective: dflt, source: 'default', restart_required: false, ...extra,
});
const TIMING = [
    knob('scheduler_poll_interval_seconds', 'Daemon tick', 's', 30),
    knob('capture_interval_seconds', 'Context-snapshot capture cadence', 's', 300),
    knob('cache_stale_seconds', 'Stale statusLine cache cutoff', 's', 7200),
    knob('context_retention_days', 'Context-snapshot retention', 'd', 30),
    knob('dispatch_tick_interval_seconds', 'Dispatcher tick (workflow default)', 's', 60),
    knob('status_cmd_timeout_seconds', 'Status-feed subprocess timeout', 's', 45, { restart_required: true }),
    knob('status_ttl_seconds', 'Status-feed cache TTL', 's', 30, { restart_required: true, source: 'env', effective: 15 }),
    knob('doctor_check_interval_seconds', 'Doctor self-audit cadence', 's', 3600),
    knob('default_context_window', 'Fallback context-window size', 'tokens', 200000),
];
const num = (key, label, unit, dflt, extra = {}) => knob(key, label, unit, dflt, { kind: 'number', ...extra });
const DISPATCH = [
    knob('dispatch_workflows', 'Dispatch workflows (colon-separated WORKFLOW.md paths)', '', '',
        { kind: 'text', restart_required: true, stored: '/srv/repos/some-long-project-name/WORKFLOW.md:/srv/repos/another-project/WORKFLOW.md' }),
    num('max_reworks', 'Max reworks before escalation (ceiling, every risk level)', '', 5),
    num('max_reworks_high', 'Max reworks — risk: high', '', 5),
    num('judge_experiments_normal', 'Judge experiments — risk: normal', '', 8),
    knob('judge_enabled', 'Judge (adversarial review)', '', true, { kind: 'bool', restart_required: true }),
    num('judge_outage_backoff_seconds', 'Judge classifier-outage backoff', 's', 600),
    num('judge_wall_per_experiment_seconds', 'Judge wall: per experiment', 's', 360),
    knob('worktree_disk_budget_bytes', 'Worktree disk budget', 'bytes', 0, { kind: 'size' }),
    knob('merge_base', 'Autonomous merge base branch', '', 'dev', { kind: 'text', restart_required: true }),
    num('gate_max_waits', 'Concurrent gate-wait slots', '', 8, { source: 'env', effective: 12 }),
];
const STALE = ['chela-agent-terminals', 'chela-daemon', 'chela-telegram'];
const SETTINGS = {
    sections: [
        { title: 'Connections', items: [
            { label: 'Telegram bridge', on: true, state: 'Connected', detail: 'chela telegram (pm2: chela-telegram) — 14 topics bound' },
            { label: 'Needs-input notify', on: true, state: 'Configured', detail: 'ntfy.sh' },
            { label: 'Collab relay', on: false, state: 'Off', detail: 'wss://collab-relay.example-account.workers.dev' },
        ] },
        { title: 'Features', items: [
            { label: 'Work dispatcher', on: true, state: 'Running', detail: '2 workflows: /srv/repos/some-long-project-name/WORKFLOW.md, /srv/repos/another-project/WORKFLOW.md' },
            { label: 'Tool-call relay', on: false, state: 'Hidden', detail: 'text + interactive prompts only (CHELA_SHOW_TOOL_CALLS)' },
        ] },
    ],
    update: { ok: true, behind: 0, branch: 'dev', stale_services: STALE },
};
const CONFIG = {
    projects_dir: '~/projects/a-rather-long-folder-name/with-nesting',
    agent_permission_modes: ['default', 'acceptEdits', 'bypassPermissions', 'plan'],
    agent_permission_mode: 'bypassPermissions', agent_permission_mode_source: 'dashboard',
    agent_models: ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5'],
    agent_model: 'claude-sonnet-5-5', agent_model_source: 'default',
    remote_control: false, share_typing: false, file_drop: true, file_drop_max_mb: 25,
    collab_relay: 'wss://chela-collab-relay.example-account.workers.dev',
};
const NOW = Math.floor(Date.now() / 1000);
const UROW = (o) => ({
    session_id: 'x', requests: 20, input: 100, cache_write: 0, cache_read: 0, output: 50,
    weighted: 1000, cache_hit: 0.95, cache_broken: false, model: 'claude-opus-5-5', spark: [0, 1, 3, 2, 5], ...o,
});
const USAGE = {
    limits: {
        five_hour: { used_pct: 63, resets_at: NOW + 2 * 3600, burn_pct_per_h: 12.5, projected_pct: 88, hits_100_before_reset: false },
        seven_day: { used_pct: 42, resets_at: NOW + 3 * 86400, burn_pct_per_h: 1.5, projected_pct: 150, hits_100_before_reset: true },
    },
    windows: {
        '30m': { rows: [
            UROW({ label: 'cmx-57-settings-on-mobile-fields-and-text-overflow · 1a2b3c4d', weighted: 9_000_000, cache_hit: 0.04,
                cache_broken: true, ai_title: 'Settings on mobile: fields and text overflow horizontally at phone width' }),
            UROW({ label: 'review-west', weighted: 40_000 }),
        ] },
        today: { rows: [UROW({ label: 'night-shift', weighted: 77 })] },
    },
    roots: { default: '/home/user/.claude/projects', extra: ['/mnt/c/Users/*/.claude/projects'], source: 'config',
        scanned: ['/home/user/.claude/projects', '/mnt/c/Users/someone-with-a-long-name/.claude/projects'] },
    thresholds: { hit_rate: 0.5, min_requests: 10, min_tokens: 100000 },
};
const COST = [
    { name: 'cmx-57-settings-on-mobile-fields-and-text-overflow', model: 'claude-opus-5-5', cost_usd: 12.34 },
    { name: 'orchestrator', model: 'claude-sonnet-5-5', cost_usd: 1.5 },
];
const API = {
    '/api/settings': SETTINGS,
    '/api/config': CONFIG,
    '/api/config/timing': { knobs: TIMING },
    '/api/config/dispatch': { knobs: DISPATCH },
    '/api/cost': COST,
    '/api/usage': USAGE,
};

// Every Settings view a phone user can land on. Cost has two (the CMX-38 Usage
// view is a second pane inside the Cost tab).
const VIEWS = [
    { tab: 'general' }, { tab: 'timing' }, { tab: 'dispatch' }, { tab: 'notifications' },
    { tab: 'cost' }, { tab: 'cost', usage: true }, { tab: 'appearance' }, { tab: 'collaboration' },
];
const viewName = v => v.usage ? 'cost/usage' : v.tab;

// What the panel holds that must fit its own box. Every element matching one of
// these, inside the active panel, is measured.
const ROWS = '.settings-section, .s-row, .s-status-row, .s-status-group, .s-ex, .s-examples, .s-kv, .s-desc, '
    + '.work-toolbar, .usage-limits, .usage-roots, #cost-table, #usage-table, #timing-rows, #dispatch-rows';

// Measure inside the page: every offender, named, with its numbers.
function measure(page) {
    return page.evaluate(rowsSel => {
        const name = el => el.id ? '#' + el.id
            : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
                ? '.' + el.className.trim().split(/\s+/).join('.') : '');
        const over = el => el.scrollWidth - el.clientWidth;
        const out = { bad: [], vw: window.innerWidth };
        const de = document.documentElement;
        if (de.scrollWidth > de.clientWidth) out.bad.push(`document scrolls sideways: ${de.scrollWidth} > ${de.clientWidth}`);
        const modal = document.getElementById('settings-drawer');
        if (over(modal) > 0) out.bad.push(`modal scrolls sideways: ${modal.scrollWidth} > ${modal.clientWidth}`);
        const r = modal.getBoundingClientRect();
        if (r.left < -0.5 || r.right > window.innerWidth + 0.5) {
            out.bad.push(`modal box ${Math.round(r.left)}..${Math.round(r.right)} leaves the ${window.innerWidth}px viewport`);
        }
        for (const id of ['drawer-body']) {
            const el = document.getElementById(id);
            if (over(el) > 0) out.bad.push(`#${id} scrolls sideways: ${el.scrollWidth} > ${el.clientWidth}`);
        }
        const panel = modal.querySelector('.settings-tabpanel.active');
        if (!panel) { out.bad.push('no active tab panel'); return out; }
        if (over(panel) > 0) out.bad.push(`panel ${panel.dataset.tab} overflows: ${panel.scrollWidth} > ${panel.clientWidth}`);
        const pr = panel.getBoundingClientRect();
        let rows = 0;
        for (const el of panel.querySelectorAll(rowsSel)) {
            if (!el.getClientRects().length) continue;   // hidden (the other Cost pane, …)
            rows++;
            // A <table> inside its own scroll box (#cost-table is one) may scroll —
            // that is containment, not overflow. Everything else must fit.
            if (getComputedStyle(el).overflowX === 'auto' || getComputedStyle(el).overflowX === 'scroll') continue;
            if (over(el) > 0) out.bad.push(`${name(el)} content ${el.scrollWidth}px > its ${el.clientWidth}px box`);
            const b = el.getBoundingClientRect();
            if (b.right > pr.right + 0.5) out.bad.push(`${name(el)} right edge ${Math.round(b.right)} past the panel's ${Math.round(pr.right)}`);
        }
        // Every form control: inside the panel, never wider than it.
        for (const el of panel.querySelectorAll('input, select, button, textarea, code')) {
            if (!el.getClientRects().length) continue;
            const b = el.getBoundingClientRect();
            if (b.right > pr.right + 0.5 || b.left < pr.left - 0.5) {
                out.bad.push(`${name(el)} ${Math.round(b.left)}..${Math.round(b.right)} outside the panel ${Math.round(pr.left)}..${Math.round(pr.right)}`);
            }
        }
        // Text that wraps must wrap like text: (a) never split a word that would
        // fit on a line of its own (`word-break: break-all` does exactly that —
        // "pm2 re|start", "WORKFL|OW.md"); a break right after `-` or `/` is a
        // normal line-break opportunity, and a token wider than its box may break
        // anywhere (that is what overflow-wrap: anywhere is for). (b) A wrapped
        // status detail keeps one left edge — right-aligned wrapped lines are the
        // "ragged indent" in the report.
        const lineOf = (node, i) => {
            const rg = document.createRange();
            rg.setStart(node, i); rg.setEnd(node, i + 1);
            const rs = rg.getClientRects();
            return rs.length ? rs[0] : null;
        };
        const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const host = node.parentElement;
            if (!host || !host.getClientRects().length) continue;
            // The line box a word wraps in is its nearest non-inline ancestor.
            let box = host;
            while (box !== panel && getComputedStyle(box).display.startsWith('inline')) box = box.parentElement;
            const room = box.clientWidth;
            for (const m of node.data.matchAll(/\S+/g)) {
                let top = null, width = 0, split = -1;
                for (let i = m.index; i < m.index + m[0].length; i++) {
                    const r = lineOf(node, i);
                    if (!r) continue;
                    width += r.width;
                    if (top === null) top = r.top;
                    else if (split < 0 && r.top > top + r.height / 2) split = i;
                }
                if (split < 0 || width > room) continue;
                if ('-/'.includes(node.data[split - 1])) continue;
                out.bad.push(`"${m[0]}" (${Math.round(width)}px, fits its ${room}px box) is split mid-word as "${node.data.slice(m.index, split)}|${node.data.slice(split, m.index + m[0].length)}"`);
            }
        }
        for (const el of panel.querySelectorAll('.s-status-detail')) {
            if (!el.getClientRects().length) continue;
            // The prose's own glyphs only (a block <code> under it has its own
            // padding edge): the left of each line = its leftmost glyph.
            const lines = [];
            const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            for (let t = tw.nextNode(); t; t = tw.nextNode()) {
                if (t.parentElement.closest('code.s-cmd')) continue;
                for (let i = 0; i < t.data.length; i++) {
                    if (/\s/.test(t.data[i])) continue;
                    const r = lineOf(t, i);
                    if (!r) continue;
                    const line = lines.find(l => Math.abs(l.top - r.top) < r.height / 2);
                    if (line) line.left = Math.min(line.left, r.left);
                    else lines.push({ top: r.top, left: r.left });
                }
            }
            const lefts = lines.map(l => l.left);
            if (lefts.length > 1 && Math.max(...lefts) - Math.min(...lefts) > 2) {
                out.bad.push(`wrapped ${name(el)} has a ragged left edge (line starts ${lefts.map(Math.round).join(', ')}): "${el.textContent.trim().slice(0, 60)}…"`);
            }
        }
        out.rows = rows;
        return out;
    }, ROWS);
}

// The tab strip: either it wraps (no sideways scroll at all), or it scrolls on
// ITS OWN — inside the modal, every tab reachable within it, with a visible
// affordance (the edge fade) on whichever side has more tabs.
function measureTabs(page) {
    return page.evaluate(() => {
        const strip = document.getElementById('settings-tabs');
        const modal = document.getElementById('settings-drawer');
        const s = strip.getBoundingClientRect();
        const m = modal.getBoundingClientRect();
        const tabs = [...strip.querySelectorAll('.settings-tab')];
        const scrolls = strip.scrollWidth > strip.clientWidth + 1;
        const ox = getComputedStyle(strip).overflowX;
        const mask = getComputedStyle(strip).maskImage || getComputedStyle(strip).webkitMaskImage || 'none';
        return {
            stripInModal: s.left >= m.left - 0.5 && s.right <= m.right + 0.5,
            scrolls, ox, mask,
            fadeEnd: strip.classList.contains('fade-end'),
            fadeStart: strip.classList.contains('fade-start'),
            count: tabs.length,
            maxTabWidth: Math.max(...tabs.map(t => t.getBoundingClientRect().width)),
            stripWidth: s.width,
        };
    });
}

async function openView(page, v) {
    await page.evaluate(({ tab, usage }) => {
        window.chela.selectSettingsTab(tab);
        if (tab === 'cost') window.chela.setCostView(usage ? 'usage' : 'cost');
    }, v);
    // Let the async loaders (/api/settings, timing, dispatch, cost, usage) land.
    await page.waitForFunction(() => !document.querySelector('.settings-tabpanel.active .s-desc')
        || ![...document.querySelectorAll('.settings-tabpanel.active #timing-rows, .settings-tabpanel.active #dispatch-rows, .settings-tabpanel.active #usage-table')]
            .some(el => el.getClientRects().length && /Loading…/.test(el.textContent)), null, { timeout: 5000 });
    await page.waitForTimeout(150);
}

for (const [width, height] of [[390, 844], [360, 740]]) {
    // chela has no light theme — all eight are dark palettes (nav.js
    // THEME_LABELS) — so the second pass is a different theme, not a light one.
    for (const theme of ['dark', 'warm']) {
        describe(`CMX-57 ${width}×${height} ${theme}: Settings never scrolls sideways`, { skip: why }, () => {
            let page, context;
            const ready = setup(async () => {
                ({ page, context } = await openDashboard(browser, {
                    width, height, api: API, storage: { chela_theme: theme }, touch: true,
                }));
                await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
                await page.evaluate(() => window.chela.toggleSettings());
                await page.waitForFunction(() => {
                    const m = document.getElementById('settings-drawer');
                    return m.classList.contains('open') && getComputedStyle(m).opacity === '1';
                }, null, { timeout: 5000 });
                // The Update card's stale-services detail is what Liav saw break.
                await page.waitForFunction(() => /pm2 restart/.test(document.getElementById('update-status-row').textContent),
                    null, { timeout: 5000 });
            });
            after(() => context && context.close());

            for (const v of VIEWS) {
                test(`${viewName(v)}: modal, panel and every row fit — no horizontal scroll`, async () => {
                    ready();
                    await openView(page, v);
                    if (SHOTS) {
                        await page.screenshot({ path: join(SHOTS, `${width}-${theme}-${viewName(v).replace('/', '-')}.png`) });
                        if (v.tab === 'general') {
                            await page.locator('#settings-update').screenshot({ path: join(SHOTS, `${width}-${theme}-update-card.png`) });
                        }
                    }
                    const m = await measure(page);
                    assert.ok(m.rows > 0, `${viewName(v)}: measured no rows — the selector list no longer matches the panel`);
                    assert.deepEqual(m.bad, [], `${viewName(v)} at ${width}px overflows horizontally:\n  ${m.bad.join('\n  ')}`);
                });
            }

            test('Update card: the pm2 restart command is its own <code> block, inside the card', async () => {
                ready();
                await openView(page, { tab: 'general' });
                const c = await page.evaluate(() => {
                    const code = [...document.querySelectorAll('#update-status-row code')]
                        .find(el => /pm2 restart/.test(el.textContent));
                    if (!code) return null;
                    const card = document.getElementById('settings-update').getBoundingClientRect();
                    const r = code.getBoundingClientRect();
                    return { text: code.textContent.replace(/\s+/g, ' ').trim(), display: getComputedStyle(code).display,
                        inside: r.left >= card.left - 0.5 && r.right <= card.right + 0.5,
                        fits: code.scrollWidth <= code.clientWidth };
                });
                assert.ok(c, 'the restart command is not in a <code> element');
                assert.equal(c.text, `pm2 restart ${STALE.join(' ')}`);
                assert.equal(c.display, 'block', 'the command runs inline with the sentence instead of on its own block');
                assert.ok(c.inside, 'the command block pokes out of the Update card');
                assert.ok(c.fits, 'the command block scrolls sideways at phone width');
            });

            test('the tab strip scrolls on its own, inside the modal, with a visible affordance', async () => {
                ready();
                await page.evaluate(() => window.chela.selectSettingsTab('general'));
                await page.evaluate(() => { document.getElementById('settings-tabs').scrollLeft = 0; });
                await page.waitForTimeout(100);
                const t = await measureTabs(page);
                assert.equal(t.count, 7, 'expected the seven Settings tabs');
                assert.ok(t.stripInModal, 'the tab strip extends past the modal');
                assert.ok(t.maxTabWidth <= t.stripWidth, `a single tab (${t.maxTabWidth}px) is wider than the strip (${t.stripWidth}px)`);
                if (t.scrolls) {
                    assert.ok(t.ox === 'auto' || t.ox === 'scroll', `the strip overflows but cannot scroll (overflow-x: ${t.ox})`);
                    assert.ok(t.fadeEnd, 'the strip has more tabs off its right edge but no .fade-end affordance says so');
                    assert.ok(!t.fadeStart, 'scrolled to the start, yet the strip still fades its left edge');
                    assert.match(t.mask, /gradient/, `.fade-end draws no edge fade (mask-image: ${t.mask})`);
                    // Scroll to the end: the fade flips sides, and the LAST tab is
                    // reachable inside the strip, not past the modal's edge.
                    await page.evaluate(() => {
                        const s = document.getElementById('settings-tabs');
                        s.scrollLeft = s.scrollWidth;
                        s.dispatchEvent(new Event('scroll'));
                    });
                    await page.waitForTimeout(100);
                    const end = await page.evaluate(() => {
                        const s = document.getElementById('settings-tabs');
                        const last = s.querySelector('.settings-tab:last-child').getBoundingClientRect();
                        const r = s.getBoundingClientRect();
                        return { inside: last.right <= r.right + 1 && last.left >= r.left - 1,
                            fadeEnd: s.classList.contains('fade-end'), fadeStart: s.classList.contains('fade-start') };
                    });
                    assert.ok(end.inside, 'scrolled to the end, the last tab is still not inside the strip');
                    assert.ok(!end.fadeEnd, 'scrolled to the end, the strip still fades its right edge');
                    assert.ok(end.fadeStart, 'scrolled to the end, nothing says tabs are hidden off the left edge');
                }
            });
        });
    }
}
