// CMX-417 — the pane footer's tmux window id (`@N`), MEASURED in a real
// Chromium (jsdom has no layout engine: "does not wrap" is not a DOM fact).
//
// ⭐ The case that must be ACCEPTED: a window with a very long name AND a very
// long branch, its footer filled with model + PR + cost + context, still shows
// its id — whole, on the footer's one line, inside the footer — and the footer
// itself does not wrap, at both 1440×900 (the Wall) and 390×844 (the phone's
// single pane).
//
// Harness: tests/browser/fixture.mjs — never a live dashboard or daemon.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const LONG = 'a-very-long-session-name-that-keeps-going-' + 'x'.repeat(60);
const BRANCH = 'feature/an-extremely-long-branch-name-that-would-never-fit-' + 'y'.repeat(40);
const AGENTS = ['@1', '@2', '@3', '@32'].map((wid, i) => ({
    name: i === 3 ? LONG : `agent-${i}`, window_id: wid, online: true, session_status: 'busy',
    cwd: '/p/x', ai_title: 'Refactor the flux capacitor',
    pr: { url: 'https://example.invalid/pr/1234', number: 1234 },
}));
const CONTEXT = AGENTS.map(a => ({
    window_id: a.window_id, used_pct: 74, used: '147.5K', total: '1M',
    model: 'claude-opus-5-5[1m]', cost_usd: 12.34, branch: BRANCH,
}));
const API = { '/api/agents': AGENTS, '/api/agents/context': CONTEXT, '/api/summary': { windows_total: AGENTS.length } };

function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// Every visible footer: its height, and the id chip's box against the bar's.
const measure = page => page.evaluate(() => {
    const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).display !== 'none';
    return [...document.querySelectorAll('#panel-terminals .term-ctx-bar')].filter(shown).map(bar => {
        const b = bar.getBoundingClientRect();
        const chip = bar.querySelector('.gs-wid');
        const c = chip && shown(chip) ? chip.getBoundingClientRect() : null;
        // Any visible chip whose box leaves the bar's single row = the bar wrapped.
        const offRow = [...bar.children].filter(shown).filter(k => !k.classList.contains('term-ctx-fill'))
            .filter(k => { const r = k.getBoundingClientRect(); return r.top < b.top - 0.5 || r.bottom > b.bottom + 0.5; })
            .map(k => k.className);
        return {
            for: bar.dataset.ctxFor, barH: b.height, scrollH: bar.scrollHeight, clientH: bar.clientHeight,
            chip: c && { text: chip.textContent.trim(), left: c.left, right: c.right, top: c.top, bottom: c.bottom, h: c.height,
                         scrollW: chip.scrollWidth, clientW: chip.clientWidth },
            bar: { left: b.left, right: b.right, top: b.top, bottom: b.bottom },
            offRow,
            filled: !!bar.querySelector('.gs-ctx:not([hidden])'),
        };
    });
});

function assertFooters(bars, expectWids) {
    assert.deepEqual(bars.map(b => b.for).sort(), [...expectWids].sort(), 'the footers on screen');
    for (const b of bars) {
        assert.ok(b.filled, `${b.for}: footer never got its context — the worst case was not exercised`);
        assert.ok(b.chip, `${b.for}: no visible @N chip in the footer`);
        assert.equal(b.chip.text, b.for, `${b.for}: footer shows ${b.chip.text}`);
        assert.ok(b.chip.left >= b.bar.left - 0.5 && b.chip.right <= b.bar.right + 0.5,
            `${b.for}: id chip [${b.chip.left},${b.chip.right}] leaves the bar [${b.bar.left},${b.bar.right}]`);
        assert.ok(b.chip.top >= b.bar.top - 0.5 && b.chip.bottom <= b.bar.bottom + 0.5, `${b.for}: id chip leaves the bar vertically`);
        assert.ok(b.chip.scrollW <= b.chip.clientW + 0.5, `${b.for}: id chip text is clipped (${b.chip.scrollW} > ${b.chip.clientW})`);
        assert.ok(b.chip.h <= 16, `${b.for}: id chip is ${b.chip.h}px tall — wrapped onto two lines`);
        assert.equal(b.barH, 30, `${b.for}: footer is ${b.barH}px, not its one 30px row`);
        assert.ok(b.scrollH <= b.clientH, `${b.for}: footer content overflows its row (${b.scrollH} > ${b.clientH})`);
        assert.deepEqual(b.offRow, [], `${b.for}: chips wrapped off the footer's row`);
    }
}

describe('CMX-417 1440×900: every Wall footer shows its own @N on one line', { skip: why }, () => {
    let context, page;
    const ready = setup(async () => {
        ({ context, page } = await openDashboard(browser, { width: 1440, height: 900, api: API }));
        await page.waitForFunction(() => document.querySelectorAll('#term-stage .term-ctx-bar .gs-ctx:not([hidden])').length >= 4,
            null, { timeout: 15000 });
    });
    after(() => context && context.close());

    test('⭐ long name + long branch + a full footer: the id is whole, inside, and nothing wraps', async () => {
        ready();
        assertFooters(await measure(page), ['@1', '@2', '@3', '@32']);
    });
});

describe('CMX-417 390×844: the phone pane footer shows its @N on one line', { skip: why }, () => {
    let context, page;
    const ready = setup(async () => {
        ({ context, page } = await openDashboard(browser, { width: 390, height: 844, api: API,
            storage: { pc_term_mode: 'single' } }));
        await page.waitForFunction(() => document.querySelectorAll('#panel-terminals .term-ctx-bar .gs-ctx:not([hidden])').length >= 1,
            null, { timeout: 15000 });
    });
    after(() => context && context.close());

    test('⭐ the single pane footer: id whole, inside, and nothing wraps', async () => {
        ready();
        const bars = await measure(page);
        assert.equal(bars.length, 1, `expected the one phone pane footer, got ${bars.length}`);
        assertFooters(bars, [bars[0].for]);
    });
});
