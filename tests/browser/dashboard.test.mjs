// CMX-383 — the dashboard's LAYOUT, STATUS SHAPES and FONT, measured by a real
// browser (Playwright/Chromium), never by re-implementing CSS.
//
// Why a browser: CMX-377 (PR #529) went through five reworks because every
// jsdom guard was a hand-written CSS-cascade emulation, and the judge kept
// finding one more CSS way to break the look while it stayed green —
// `grid-row: 2`, longhand vs shorthand, `@supports`, an inset box-shadow
// filling the idle ring, display:none, opacity:0, transform:scale(0), a
// transparent border, `.app` not being a grid. jsdom has no layout engine, so
// no emulation can enumerate every way CSS hides or moves an element. A
// browser does not need to: these assertions read bounding boxes, painted
// pixels and computed/loaded fonts — what a user SEES — so the next spelling
// of the same break is caught without being predicted.
//
// ⛔ No whole-page pixel baselines: themes and fine-tunes change colours. Shapes
// are compared as greyscale PAINTED MASKS (the pixels the mark itself changes),
// layout as bounding boxes.
//
// Harness: tests/browser/fixture.mjs (real shell, stubbed API, no network, no
// daemon, no tmux, no ttyd). Local: `pnpm run test:browser`. CI runs it inside
// pytest (tests/test_js_suites.py discovers every *.test.mjs), under
// CHELA_REQUIRE_JS_TESTS=1 — a browser that cannot launch is a FAILURE there.
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { AGENTS, STATE_OF, launchChromium, openDashboard } from './fixture.mjs';

const { browser, why } = await launchChromium();
after(() => browser && browser.close());

const px = 1;   // layout tolerance, CSS px

// A describe's before() whose failure every test in it REPORTS. node:test
// otherwise cancels the tests with "did not finish before its parent" and
// never prints the hook's own error.
function setup(fn) {
    let failed = null;
    before(async () => { try { await fn(); } catch (e) { failed = e; } });
    return () => { if (failed) throw failed; };
}

// Resolve once the Wall and the sidebar have both rendered all four agents
// with their status classes applied by the real status tick.
async function settle(page) {
    await page.waitForFunction(n => {
        const rows = document.querySelectorAll('.agent-row .term-status-dot');
        const heads = document.querySelectorAll('.gs-head .term-status-dot[data-status-for]');
        const stated = el => /\b(working|waiting|idle|done)\b/.test(el.className);
        return rows.length >= n && heads.length >= n
            && [...rows].every(stated) && [...heads].every(stated);
    }, AGENTS.length, { timeout: 15000 });
    await stable(page);
}

// The layout has stopped moving: GridStack's preset fit, the dock
// refit and the resize debounce all land AFTER first render, so a busy
// machine can otherwise measure a Wall mid-reflow. Stable = the same
// geometry for 500ms straight.
async function stable(page) {
    await page.waitForFunction(() => {
        const sig = [...document.querySelectorAll('.sidebar, #canvas, #term-stage .grid-stack-item, .safety-float, #btn-menu-mobile')]
            .map(e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join(','); })
            .join('|');
        const now = performance.now();
        if (window.__cmx383Sig !== sig) { window.__cmx383Sig = sig; window.__cmx383At = now; }
        return now - window.__cmx383At >= 500;
    }, null, { timeout: 15000, polling: 100 });
}

async function box(page, selector) {
    const b = await page.locator(selector).first().boundingBox();
    assert.ok(b, `${selector} has no layout box (not rendered)`);
    return b;
}

describe('desktop 1440×900: the Wall sits beside the sidebar and fills the canvas', { skip: why }, () => {
    let page, context, misses;
    const ready = setup(async () => {
        ({ page, context, misses } = await openDashboard(browser, { width: 1440, height: 900 }));
        await settle(page);
    });
    after(() => context && context.close());

    test('#canvas starts at the sidebar\'s right edge, same top, and fills the rest of the width', async () => {
        ready();
        const side = await box(page, '.sidebar');
        const canvas = await box(page, '#canvas');
        assert.ok(side.width > 0 && side.height > 0, `sidebar has an empty box: ${JSON.stringify(side)}`);
        assert.ok(canvas.x >= side.x + side.width - px,
            `#canvas (left ${canvas.x}) is not right of the sidebar (right ${side.x + side.width})`);
        assert.ok(Math.abs(canvas.x - (side.x + side.width)) <= px,
            `#canvas left ${canvas.x} does not start at the sidebar's right edge ${side.x + side.width}`);
        assert.ok(Math.abs(canvas.y - side.y) <= px, `#canvas top ${canvas.y} != sidebar top ${side.y}`);
        assert.ok(Math.abs(canvas.x + canvas.width - 1440) <= px,
            `#canvas right edge ${canvas.x + canvas.width} does not reach the viewport's 1440`);
        assert.ok(canvas.y < 900 && canvas.height > 0, `#canvas starts off-screen (top ${canvas.y})`);
    });

    test('2×2 preset: every pane is inside the viewport and inside the canvas', async () => {
        ready();
        const canvas = await box(page, '#canvas');
        const panes = await page.locator('#term-stage .grid-stack-item').all();
        assert.equal(panes.length, AGENTS.length, 'expected one Wall pane per fixture agent');
        const tops = [], lefts = [];
        for (const [i, pane] of panes.entries()) {
            const b = await pane.boundingBox();
            assert.ok(b && b.width > 0 && b.height > 0, `pane ${i} has no layout box`);
            const where = `pane ${i} ${JSON.stringify(b)}`;
            assert.ok(b.x >= -px && b.y >= -px && b.x + b.width <= 1440 + px && b.y + b.height <= 900 + px,
                `${where} is not inside the 1440×900 viewport`);
            assert.ok(b.x >= canvas.x - px && b.y >= canvas.y - px
                && b.x + b.width <= canvas.x + canvas.width + px
                && b.y + b.height <= canvas.y + canvas.height + px,
                `${where} is not inside #canvas ${JSON.stringify(canvas)}`);
            tops.push(b.y); lefts.push(b.x);
        }
        // Distinct edges, up to sub-pixel rounding between panes.
        const distinct = xs => xs.sort((a, b) => a - b).filter((x, i, s) => i === 0 || x - s[i - 1] > 2).length;
        assert.equal(distinct(tops), 2, `2×2 preset should lay out 2 rows, got tops ${tops}`);
        assert.equal(distinct(lefts), 2, `2×2 preset should lay out 2 columns, got lefts ${lefts}`);
    });

    test('the Wall grid adds no outer padding: outer pane edges touch the canvas content edges', async () => {
        ready();
        // The canvas's OWN padding is the app gutter; what must not exist is any
        // extra inset between it and the Wall (a margin/padding on #term-stage
        // or .grid-stack, a max-width cap, …). Measured, whatever it is spelt as.
        const edge = await page.evaluate(() => {
            const c = document.getElementById('canvas');
            const cb = c.getBoundingClientRect();
            const cs = getComputedStyle(c);
            const items = [...document.querySelectorAll('#term-stage .grid-stack-item')]
                .map(e => e.getBoundingClientRect());
            return {
                contentLeft: cb.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft),
                contentRight: cb.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight)
                    - (c.offsetWidth - c.clientWidth - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth)),
                paneLeft: Math.min(...items.map(r => r.left)),
                paneRight: Math.max(...items.map(r => r.right)),
            };
        });
        assert.ok(Math.abs(edge.paneLeft - edge.contentLeft) <= px,
            `outer pane left edge ${edge.paneLeft} is inset from the canvas content edge ${edge.contentLeft}`);
        assert.ok(Math.abs(edge.paneRight - edge.contentRight) <= px,
            `outer pane right edge ${edge.paneRight} is inset from the canvas content edge ${edge.contentRight}`);
    });

    test('the page made no request the fixture could not serve', () => {
        ready();
        assert.deepEqual(misses, []);
    });
});

// ---- painted masks ---------------------------------------------------------

const DSF = 4;          // device pixels per CSS px: a 10px mark → a 40px crop
const GRID = 32;        // masks are resampled onto one grid so sizes compare
const INK = 20;         // greyscale delta (0-255) that counts as "the mark painted here"

// The mark's PAINTED MASK: screenshot exactly its box twice — as rendered, and
// with the mark alone made invisible — greyscale both, and keep the pixels
// that differ. That is the silhouette the mark itself contributes, regardless
// of its colour or the surface behind it (sidebar vs pane header), and it is
// empty for every way of not painting: opacity:0, scale(0), display:none, a
// transparent border, a zero box… with no list of those ways anywhere.
//
// The mark is hidden by an injected SELECTOR rule, never an inline style on one
// node, and the measurement is checked for staleness: the sidebar re-renders
// (and re-orders) its rows on its own poll, so the node is stamped before the
// shots and must still be the SAME node in the SAME box after them. A stale
// pair is re-shot; a mark that genuinely does not paint is stable, so this
// never retries a real failure away.
async function paintedMask(page, selector) {
    for (let attempt = 0; ; attempt++) {
        const r = await paintedMaskOnce(page, selector);
        if (!r.stale || attempt >= 5) return r;
    }
}

async function paintedMaskOnce(page, selector) {
    const locator = page.locator(selector).first();
    await locator.scrollIntoViewIfNeeded().catch(() => {});
    const probe = stamp => page.evaluate(([sel, stamp]) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        if (stamp) el.__cmx383 = stamp;
        const r = el.getBoundingClientRect();
        return { stamp: el.__cmx383, x: r.x, y: r.y, width: r.width, height: r.height };
    }, [selector, stamp]);
    const b = await probe(String(Math.random()));
    if (!b) return { stale: true, area: 0, box: null, grid: null };   // mid re-render
    if (b.width < 0.5 || b.height < 0.5) return { area: 0, box: b, grid: null };
    const clip = { x: b.x, y: b.y, width: b.width, height: b.height };
    const shot = () => page.screenshot({ clip, animations: 'disabled', scale: 'device' });
    const painted = await shot();
    const hide = await page.addStyleTag({ content: `${selector} { visibility: hidden !important; }` });
    const bare = await shot();
    await hide.evaluate(el => el.remove());
    const after = await probe(null);
    if (JSON.stringify(after) !== JSON.stringify(b)) return { stale: true };
    const m = await page.evaluate(async ([a, z, grid, ink]) => {
        const grey = async b64 => {
            const bmp = await createImageBitmap(new Blob([Uint8Array.from(atob(b64), c => c.charCodeAt(0))]));
            const cv = new OffscreenCanvas(bmp.width, bmp.height);
            const cx = cv.getContext('2d');
            cx.drawImage(bmp, 0, 0);
            const d = cx.getImageData(0, 0, bmp.width, bmp.height).data;
            const g = new Float32Array(bmp.width * bmp.height);
            for (let i = 0; i < g.length; i++) g[i] = 0.299 * d[4 * i] + 0.587 * d[4 * i + 1] + 0.114 * d[4 * i + 2];
            return { w: bmp.width, h: bmp.height, g };
        };
        const A = await grey(a), Z = await grey(z);
        const on = new Uint8Array(A.w * A.h);
        let area = 0;
        for (let i = 0; i < on.length; i++) if (Math.abs(A.g[i] - Z.g[i]) > ink) { on[i] = 1; area++; }
        const out = [];
        for (let y = 0; y < grid; y++) for (let x = 0; x < grid; x++) {
            out.push(on[Math.floor((y + 0.5) * A.h / grid) * A.w + Math.floor((x + 0.5) * A.w / grid)]);
        }
        return { area, grid: out, w: A.w, h: A.h };
    }, [painted.toString('base64'), bare.toString('base64'), GRID, INK]);
    return { ...m, box: b };
}

describe('phone 390×844: off-canvas drawer, menu FAB, always-visible safety cluster', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 390, height: 844, deviceScaleFactor: DSF }));
        await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
        // The kill-switch pill is unhidden by the status tick, after first paint.
        await page.waitForSelector('#btn-shares:not([hidden])', { state: 'attached', timeout: 15000 });
        await stable(page);
    });
    after(() => context && context.close());

    // Visible = a non-empty box inside the viewport, and it is what the
    // browser hit-tests at its own centre (not covered, not transparent to it).
    async function assertOnScreen(selector) {
        const b = await box(page, selector);
        assert.ok(b.width > 0 && b.height > 0, `${selector} has an empty box`);
        assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.width <= 390 && b.y + b.height <= 844,
            `${selector} ${JSON.stringify(b)} is not inside the 390×844 viewport`);
        const hit = await page.evaluate(([sel, x, y]) => {
            const el = document.elementFromPoint(x, y);
            return !!(el && el.closest(sel));
        }, [selector, b.x + b.width / 2, b.y + b.height / 2]);
        assert.ok(hit, `${selector} is covered or not hit-testable at its centre`);
        // …and it actually PAINTS: opacity, visibility, filters and colours all
        // leave hit-testing alone, so ask the pixels.
        const m = await paintedMask(page, selector);
        const share = m.grid ? m.area / (m.w * m.h) : 0;
        assert.ok(share >= 0.05, `${selector} paints only ${(share * 100).toFixed(1)}% of its box — not visibly drawn`);
    }

    test('the closed sidebar is entirely off-canvas', async () => {
        ready();
        const side = await box(page, '.sidebar');
        assert.ok(side.x + side.width <= px || side.x >= 390 - px,
            `closed sidebar ${JSON.stringify(side)} overlaps the 390px viewport`);
    });

    test('.safety-float is on screen with the drawer closed', async () => {
        ready();
        await assertOnScreen('.safety-float');
        await assertOnScreen('#btn-shares');   // the fixture's shared @2 lights the kill-switch
    });

    test('the menu FAB is visible and opens the drawer', async () => {
        ready();
        await assertOnScreen('#btn-menu-mobile');
        await page.locator('#btn-menu-mobile').click();
        await page.waitForFunction(() => {
            const r = document.querySelector('.sidebar').getBoundingClientRect();
            return r.left >= -1 && r.width > 0 && r.left < window.innerWidth;
        }, null, { timeout: 5000 });
        const side = await box(page, '.sidebar');
        assert.ok(side.x >= -px && side.width > 0 && side.x + side.width <= 390 + px,
            `opened drawer ${JSON.stringify(side)} is not on screen`);
    });
});

// Share of the union the two silhouettes disagree on: 0 = identical shape.
function mismatch(a, b) {
    let union = 0, xor = 0;
    for (let i = 0; i < a.length; i++) { if (a[i] || b[i]) union++; if (a[i] !== b[i]) xor++; }
    return union ? xor / union : 0;
}

// Calibrated on dev (2026-09-28): same state across surfaces 0-20% (the idle
// ring is the high end — its 1.5px border is absolute, and the sidebar mark is
// 10px while the pane header's is 8px), different states 55-90%.
const SAME_SHAPE = 0.35;      // sidebar vs pane header, same state: at most this mismatch
const DIFFERENT_SHAPE = 0.45; // two different states: at least this mismatch
const MIN_AREA = 0.15;        // painted share of the mark's own box, at the least

describe('status marks: one shape per state, the same in the sidebar row and the pane header', { skip: why }, () => {
    let page, context;
    const masks = {};   // state -> { row, head }
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900, deviceScaleFactor: DSF }));
        await settle(page);
        for (const a of AGENTS) {
            const state = STATE_OF[a.window_id];
            const rowSel = `.agent-row[data-agent="${a.name}"] .term-status-dot`;
            const headSel = `.gs-head .term-status-dot[data-status-for="${a.window_id}"]`;
            // Both surfaces must be in the fixture's intended state — otherwise
            // the shape comparison below would be between the wrong marks.
            for (const [where, sel] of [['sidebar row', rowSel], ['pane header', headSel]]) {
                const ok = await page.waitForFunction(([sel, st]) => {
                    const el = document.querySelector(sel);
                    return !!el && el.classList.contains(st);
                }, [sel, state], { timeout: 10000 }).then(() => true, () => false);
                const cls = await page.locator(sel).first().getAttribute('class', { timeout: 1000 }).catch(() => null);
                assert.ok(ok, `${a.name}'s ${where} mark is "${cls}", not ${state}`);
            }
            masks[state] = { row: await paintedMask(page, rowSel), head: await paintedMask(page, headSel) };
        }
    });
    after(() => context && context.close());

    for (const state of Object.values(STATE_OF)) {
        test(`${state}: both marks paint a non-empty area`, () => {
            ready();
            for (const where of ['row', 'head']) {
                const m = masks[state][where];
                const share = m.grid ? m.area / (m.w * m.h) : 0;
                assert.ok(share >= MIN_AREA,
                    `${state} mark in the ${where === 'row' ? 'sidebar row' : 'pane header'} paints ` +
                    `${(share * 100).toFixed(1)}% of its box (${JSON.stringify(m.box)}) — it is not visibly drawn`);
            }
        });

        test(`${state}: the sidebar row and the pane header draw the same shape`, () => {
            ready();
            const { row, head } = masks[state];
            assert.ok(row.grid && head.grid, `${state}: a mark has no box to compare`);
            const d = mismatch(row.grid, head.grid);
            assert.ok(d <= SAME_SHAPE,
                `${state}: sidebar vs pane-header silhouettes differ by ${(d * 100).toFixed(0)}% (max ${SAME_SHAPE * 100}%)`);
        });
    }

    test('the four states are four different shapes, on both surfaces', () => {
        ready();
        const states = Object.values(STATE_OF);
        for (const where of ['row', 'head']) {
            for (let i = 0; i < states.length; i++) for (let j = i + 1; j < states.length; j++) {
                const a = masks[states[i]][where], b = masks[states[j]][where];
                assert.ok(a.grid && b.grid, `${where}: ${states[i]}/${states[j]} mark has no box`);
                const d = mismatch(a.grid, b.grid);
                assert.ok(d >= DIFFERENT_SHAPE,
                    `${where}: ${states[i]} and ${states[j]} are only ${(d * 100).toFixed(0)}% different ` +
                    `(min ${DIFFERENT_SHAPE * 100}%) — two states read as the same shape`);
            }
        }
    });
});

describe('fonts: Geist chrome, monospace terminal', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900 }));
        await settle(page);
    });
    after(() => context && context.close());

    test('a sidebar row\'s computed font-family resolves to the Geist stack', async () => {
        ready();
        const family = await page.locator('.agent-row .agent-row-name').first()
            .evaluate(el => getComputedStyle(el).fontFamily);
        assert.match(family, /^\s*["']?Geist["']?\s*,/, `sidebar row font-family is ${family}`);
    });

    test('the Geist face actually LOADED (not just named)', async () => {
        ready();
        const r = await page.evaluate(async () => {
            await document.fonts.load('14px Geist');
            await document.fonts.ready;
            const faces = [...document.fonts].filter(f => f.family.replace(/["']/g, '') === 'Geist');
            return { check: document.fonts.check('14px Geist'), statuses: faces.map(f => f.status) };
        });
        assert.ok(r.statuses.length > 0, 'no Geist @font-face is registered');
        assert.ok(r.statuses.every(s => s === 'loaded'), `Geist face status: ${r.statuses}`);
        assert.equal(r.check, true, "document.fonts.check('14px Geist') is false after load");
    });

    test('a pane terminal frame is monospace, never the chrome font', async () => {
        ready();
        const family = await page.locator('#term-stage iframe.term-frame').first()
            .evaluate(el => getComputedStyle(el).fontFamily);
        assert.match(family, /\bmonospace\b/, `terminal frame font-family is ${family}`);
        assert.doesNotMatch(family, /Geist/, `terminal frame font-family is ${family}`);
    });
});

// ---- CMX-393: restyle fine-tunes -------------------------------------------
// Liav, 2026-09-28, on the live restyle: the sidebar foot was two rows (stats +
// inbox, then a ⋮ alone and left-aligned), and every pane title bar drew its
// state TWICE (a shape, then a pill repeating the same glyph before the word).

const ROW = 4;   // "the same row": vertical centres within this many CSS px

// Every rendered control in the sidebar foot, with its box. A control that is
// display:none/hidden has no box and is left out; the caller asserts the count.
function footControls(page) {
    return page.evaluate(() => [...document.querySelectorAll('.sidebar-foot .res-item, .sidebar-foot button')]
        .map(e => ({ id: e.id, r: e.getBoundingClientRect() }))
        .filter(c => c.r.width > 0 && c.r.height > 0)
        .map(c => ({ id: c.id, x: c.r.x, y: c.r.y, w: c.r.width, h: c.r.height, cy: c.r.y + c.r.height / 2 })));
}

// One row = every control's vertical centre within ROW px of every other's,
// AND every pair's boxes overlap vertically (a centre test alone would pass two
// rows of very tall controls). Centre rather than top: a 12px readout and a
// 38px button that share a centred row have tops ~10px apart by design.
function assertOneRow(controls, where) {
    const cys = controls.map(c => c.cy);
    const spread = Math.max(...cys) - Math.min(...cys);
    assert.ok(spread <= ROW, `${where}: controls span ${spread.toFixed(1)}px vertically — not one row: ` +
        JSON.stringify(controls.map(c => [c.id, Math.round(c.y), Math.round(c.h)])));
    for (const a of controls) for (const b of controls) {
        assert.ok(a.y < b.y + b.h && b.y < a.y + a.h, `${where}: ${a.id} and ${b.id} do not share a row`);
    }
}

async function waitForReadouts(page) {
    await page.waitForFunction(() => document.querySelectorAll('.sidebar-foot .res-item:not([hidden])').length === 3,
        null, { timeout: 15000 });
}

describe('CMX-393 desktop 1440: one-row sidebar foot, inbox in the head, one state shape per pane header', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900 }));
        await settle(page);
        await waitForReadouts(page);
        await stable(page);
    });
    after(() => context && context.close());

    test('the sidebar foot is ONE row: the three readouts on the left, the one menu control on the right', async () => {
        ready();
        const controls = await footControls(page);
        const ids = controls.map(c => c.id);
        assert.deepEqual(ids, ['res-cpu', 'res-mem', 'res-disk', 'btn-primary-menu'],
            `the foot's rendered controls are ${ids.join(', ')}`);
        assertOneRow(controls, 'sidebar foot');
        // Stats left, menu right: the menu is the rightmost control and sits at
        // the foot's right content edge, not tucked beside the readouts.
        const foot = await page.evaluate(() => {
            const f = document.querySelector('.sidebar-foot');
            const r = f.getBoundingClientRect(), cs = getComputedStyle(f);
            return { left: r.left + parseFloat(cs.paddingLeft), right: r.right - parseFloat(cs.paddingRight) };
        });
        const menu = controls.at(-1), disk = controls.at(-2);
        assert.ok(Math.abs(menu.x + menu.w - foot.right) <= 2,
            `the menu control's right edge ${menu.x + menu.w} is not at the foot's right edge ${foot.right}`);
        assert.ok(Math.abs(controls[0].x - foot.left) <= 6,
            `the readouts start at ${controls[0].x}, not at the foot's left edge ${foot.left}`);
        assert.ok(menu.x - (disk.x + disk.w) >= 12,
            'the menu control sits right against the readouts instead of at the far right');
    });

    test('the Decisions inbox button is inside the sidebar HEAD, on screen, not in the foot', async () => {
        ready();
        const r = await page.evaluate(() => {
            const b = document.getElementById('btn-decisions');
            const head = document.querySelector('.sidebar-head');
            const bb = b.getBoundingClientRect(), hb = head.getBoundingClientRect();
            const hit = document.elementFromPoint(bb.x + bb.width / 2, bb.y + bb.height / 2);
            return {
                inHeadDom: !!b.closest('.sidebar-head'), inFootDom: !!b.closest('.sidebar-foot'),
                b: [bb.x, bb.y, bb.width, bb.height], h: [hb.x, hb.y, hb.width, hb.height],
                hit: !!(hit && hit.closest('#btn-decisions')),
            };
        });
        assert.ok(r.inHeadDom && !r.inFootDom, '#btn-decisions is not a descendant of .sidebar-head');
        const [bx, by, bw, bh] = r.b, [hx, hy, hw, hh] = r.h;
        assert.ok(bw > 0 && bh > 0, '#btn-decisions has no layout box');
        assert.ok(bx >= hx - px && by >= hy - px && bx + bw <= hx + hw + px && by + bh <= hy + hh + px,
            `#btn-decisions ${JSON.stringify(r.b)} is not drawn inside .sidebar-head ${JSON.stringify(r.h)}`);
        assert.ok(r.hit, '#btn-decisions is covered or not hit-testable at its centre');
        // …and it shares the head's row with the sidebar toggle.
        const toggle = await box(page, '#btn-menu');
        assert.ok(Math.abs((toggle.y + toggle.height / 2) - (by + bh / 2)) <= ROW,
            '#btn-decisions is not on the same row as the sidebar toggle');
    });

    // "Inside the head" is containment, and containment passes wherever in the
    // head the button lands — `.sidebar-head-actions { margin-left: 0 }` parked
    // it right after the wordmark and stayed green (judge on #545, round 1).
    // The claim is POSITION: the head's right content edge, with the toggle
    // and the brand to its left and clear space between them.
    test('the Decisions inbox sits at the sidebar head\'s RIGHT end, clear of the wordmark', async () => {
        ready();
        const g = await page.evaluate(() => {
            const head = document.querySelector('.sidebar-head');
            const hr = head.getBoundingClientRect(), cs = getComputedStyle(head);
            const r = s => { const b = document.querySelector(s).getBoundingClientRect(); return { x: b.x, w: b.width }; };
            return { right: hr.right - parseFloat(cs.paddingRight), inbox: r('#btn-decisions'),
                toggle: r('#btn-menu'), brand: r('.sidebar-head .brand') };
        });
        const inboxRight = g.inbox.x + g.inbox.w;
        assert.ok(Math.abs(inboxRight - g.right) <= 2,
            `#btn-decisions ends at ${inboxRight}, not at the head's right content edge ${g.right}`);
        assert.ok(g.toggle.x < g.brand.x && g.brand.x < g.inbox.x,
            `head order is not toggle · brand · inbox: ${JSON.stringify(g)}`);
        assert.ok(g.inbox.x - (g.brand.x + g.brand.w) >= 24,
            `#btn-decisions sits ${(g.inbox.x - (g.brand.x + g.brand.w)).toFixed(1)}px after the wordmark — tucked beside it, not at the far right`);
    });

    test('every pane header draws its state ONCE: one status shape, and a pill with the WORD only', async () => {
        ready();
        const heads = await page.evaluate(() => [...document.querySelectorAll('#term-stage .gs-head')].map(h => {
            const pill = h.querySelector('.gs-state');
            return {
                wid: (h.querySelector('[data-status-for]') || {}).dataset?.statusFor,
                shapes: h.querySelectorAll('.term-status-dot, .gs-state-glyph').length,
                shapeCls: (h.querySelector('.term-status-dot') || {}).className || '',
                pill: pill ? pill.innerText.trim() : null,
                pillKids: pill ? pill.querySelectorAll('.term-status-dot, .gs-state-glyph').length : -1,
            };
        }));
        assert.equal(heads.length, AGENTS.length, 'expected one pane header per fixture agent');
        const WORD = { working: 'working', waiting: 'needs you', idle: 'idle', done: 'PR open' };  // CMX-67: the wall's word (its dot shape stays done)
        for (const h of heads) {
            const state = STATE_OF[h.wid];
            assert.equal(h.shapes, 1, `${h.wid}: the header carries ${h.shapes} status-shape elements, want exactly 1`);
            assert.match(h.shapeCls, new RegExp(`\\b${state}\\b`), `${h.wid}: its one shape is "${h.shapeCls}", not ${state}`);
            assert.equal(h.pillKids, 0, `${h.wid}: the pill carries its own shape element`);
            assert.equal(h.pill, WORD[state], `${h.wid}: the pill reads "${h.pill}", want the word "${WORD[state]}" alone`);
            assert.doesNotMatch(h.pill, /[○●◆✓▲?]/, `${h.wid}: the pill repeats a status glyph: "${h.pill}"`);
        }
    });

    // The subtitle is IN the claimed order, and on the title's line: the fixture
    // gives every agent an ai_title so it renders (without one the header has no
    // subtitle, and flex-direction:column on .gs-grip survived — judge on #545).
    test('pane header order is the mockup\'s: shape · title · dim subtitle · pill · icon buttons, on ONE line', async () => {
        ready();
        const order = await page.evaluate(() => [...document.querySelectorAll('#term-stage .gs-head')].map(h => {
            const b = s => { const e = h.querySelector(s); const r = e && e.getBoundingClientRect();
                return r && r.width && r.height ? { x: r.x, w: r.width, y: r.y, h: r.height, cy: r.y + r.height / 2 } : null; };
            return { dot: b('.gs-dot'), title: b('.pane-title'), sub: b('.pane-subtitle'), pill: b('.gs-state'),
                menu: b('.gs-menu-btn'), max: b('.gs-max-btn') };
        }));
        assert.equal(order.length, AGENTS.length, 'expected one pane header per fixture agent');
        for (const [i, o] of order.entries()) {
            for (const k of Object.keys(o)) assert.ok(o[k] !== null, `pane ${i}: ${k} is not rendered`);
            const seq = [o.dot, o.title, o.sub, o.pill, o.menu, o.max];
            for (let j = 1; j < seq.length; j++) {
                assert.ok(seq[j - 1].x < seq[j].x,
                    `pane ${i}: header order is not shape · title · subtitle · pill · buttons: ${JSON.stringify(o)}`);
            }
            // Title and subtitle: side by side (the subtitle starts after the
            // title ends) and on one line (centres within ROW, boxes overlap).
            assert.ok(o.sub.x >= o.title.x + o.title.w - px,
                `pane ${i}: the subtitle starts at ${o.sub.x}, inside the title (ends ${o.title.x + o.title.w}) — stacked, not side by side`);
            assert.ok(Math.abs(o.sub.cy - o.title.cy) <= ROW && o.sub.y < o.title.y + o.title.h && o.title.y < o.sub.y + o.sub.h,
                `pane ${i}: title (y ${o.title.y}, h ${o.title.h}) and subtitle (y ${o.sub.y}, h ${o.sub.h}) are not on one line`);
        }
    });
});

// The judge on #529: only the sidebar's font was guarded, while the pane
// headers, the pane footers and the modals were CLAIMED sans. Computed family,
// read off the live elements (and a modal really opened by its real button).
describe('CMX-393 fonts: pane headers, pane footers and modals are the Geist chrome font', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 1440, height: 900 }));
        await settle(page);
    });
    after(() => context && context.close());
    const GEIST = /^\s*["']?Geist["']?\s*,/;

    async function familyOf(selector) {
        const loc = page.locator(selector).first();
        assert.ok(await loc.count(), `${selector} is not in the page`);
        return loc.evaluate(el => getComputedStyle(el).fontFamily);
    }

    for (const sel of ['.gs-head .pane-title', '.gs-head .gs-state', '.term-ctx-bar']) {
        test(`${sel} resolves to the Geist stack`, async () => {
            ready();
            const f = await familyOf(`#term-stage ${sel}`);
            assert.match(f, GEIST, `${sel} font-family is ${f}`);
        });
    }

    test('the Decisions modal, opened from the head\'s inbox button, is the Geist stack', async () => {
        ready();
        await page.locator('#btn-decisions').click();
        await page.waitForSelector('#decisions-menu.open .modal-sheet', { state: 'visible', timeout: 5000 });
        for (const sel of ['#decisions-menu .modal-sheet-head', '#decisions-menu .modal-sheet-body']) {
            const f = await familyOf(sel);
            assert.match(f, GEIST, `${sel} font-family is ${f}`);
        }
        await page.keyboard.press('Escape');
    });

    test('the settings/menu popover, opened from the foot\'s gear, is the Geist stack', async () => {
        ready();
        await page.evaluate(() => window.chela.hideDecisionsMenu && window.chela.hideDecisionsMenu());
        await page.locator('#btn-primary-menu').click();
        await page.waitForSelector('#primary-menu', { state: 'visible', timeout: 5000 });
        const f = await familyOf('#primary-menu .popover-item');
        assert.match(f, GEIST, `#primary-menu font-family is ${f}`);
    });
});

describe('CMX-393 phone 390×844: the drawer foot is one row, the "+" opens the launch menu with the drawer closed', { skip: why }, () => {
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, { width: 390, height: 844 }));
        await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
        await waitForReadouts(page);
        await stable(page);
    });
    after(() => context && context.close());

    test('the phone "+" is on screen with the drawer closed and opens the launch menu', async () => {
        ready();
        const side = await box(page, '.sidebar');
        assert.ok(side.x + side.width <= px, 'the drawer is not closed at the start of this test');
        const plus = await box(page, '#btn-new-mobile');
        assert.ok(plus.x >= 0 && plus.y >= 0 && plus.x + plus.width <= 390 && plus.y + plus.height <= 844,
            `#btn-new-mobile ${JSON.stringify(plus)} is not inside the viewport`);
        await page.locator('#btn-new-mobile').click();
        await page.waitForSelector('#new-menu', { state: 'visible', timeout: 5000 });
        const menu = await box(page, '#new-menu');
        assert.ok(menu.width > 0 && menu.x >= 0 && menu.x + menu.width <= 390 + px,
            `#new-menu ${JSON.stringify(menu)} is not on screen`);
        const closed = await box(page, '.sidebar');
        assert.ok(closed.x + closed.width <= px, 'opening the launch menu also opened the drawer');
        // openNewMenu arms its light-dismiss listener in a setTimeout(0). CDP input
        // can outrun that timer on a loaded runner (CI 3.11, PR #555), so the click
        // landed before the listener existed and the menu never closed. A later
        // setTimeout(0) fires after the earlier one, so awaiting one means armed.
        await page.evaluate(() => new Promise(r => setTimeout(r, 0)));
        await page.mouse.click(380, 830);   // light-dismiss
        await page.waitForSelector('#new-menu', { state: 'hidden', timeout: 5000 });
    });

    test('.safety-float is still on screen with the drawer closed', async () => {
        ready();
        const b = await box(page, '.safety-float');
        assert.ok(b.width > 0 && b.height > 0 && b.x >= 0 && b.y >= 0 && b.x + b.width <= 390 && b.y + b.height <= 844,
            `.safety-float ${JSON.stringify(b)} is not inside the 390×844 viewport`);
    });

    test('opened drawer: its foot is one row, and the inbox sits in its head', async () => {
        ready();
        await page.locator('#btn-menu-mobile').click();
        await page.waitForFunction(() => {
            const r = document.querySelector('.sidebar').getBoundingClientRect();
            return r.left >= -1 && r.width > 0;
        }, null, { timeout: 5000 });
        await stable(page);
        const controls = await footControls(page);
        assert.deepEqual(controls.map(c => c.id), ['res-cpu', 'res-mem', 'res-disk', 'btn-primary-menu']);
        assertOneRow(controls, 'drawer foot');
        const side = await box(page, '.sidebar');
        for (const c of controls) {
            assert.ok(c.x >= side.x - px && c.x + c.w <= side.x + side.width + px,
                `${c.id} overflows the drawer (${c.x}..${c.x + c.w} vs ${side.x}..${side.x + side.width})`);
        }
        const inbox = await box(page, '#btn-decisions');
        const head = await box(page, '.sidebar-head');
        assert.ok(inbox.y >= head.y - px && inbox.y + inbox.height <= head.y + head.height + px
            && inbox.x + inbox.width <= side.x + side.width + px,
            `#btn-decisions ${JSON.stringify(inbox)} is not inside the drawer head ${JSON.stringify(head)}`);
    });
});

// CMX-398 — the Settings/menu popover opened from the sidebar FOOT. The old
// topbar math (`top = r.bottom + 6`) opened it below a button at the bottom of
// the screen: measured live at 1920×916, top=912 for a 324px menu. The whole
// menu must be inside the viewport — above the gear, since there is no room
// below it — and so must #new-menu reopened from its "New…" row.
async function assertInViewport(page, selector, vp) {
    const b = await box(page, selector);
    assert.ok(b.width > 0 && b.height > 0 && b.x >= -px && b.y >= -px
        && b.x + b.width <= vp.width + px && b.y + b.height <= vp.height + px,
        `${selector} ${JSON.stringify(b)} is not wholly inside the ${vp.width}×${vp.height} viewport`);
    return b;
}

for (const vp of [{ width: 1920, height: 916 }, { width: 1440, height: 900 }]) {
    describe(`CMX-398 desktop ${vp.width}×${vp.height}: the foot's menu opens wholly on screen`, { skip: why }, () => {
        let page, context;
        const ready = setup(async () => {
            ({ page, context } = await openDashboard(browser, vp));
            await settle(page);
            await waitForReadouts(page);
            await stable(page);
        });
        after(() => context && context.close());

        test('#primary-menu is inside the viewport, above the gear', async () => {
            ready();
            await page.locator('#btn-primary-menu').click();
            await page.waitForSelector('#primary-menu', { state: 'visible', timeout: 5000 });
            const menu = await assertInViewport(page, '#primary-menu', vp);
            const gear = await box(page, '#btn-primary-menu');
            assert.ok(menu.y + menu.height <= gear.y + px,
                `#primary-menu ${JSON.stringify(menu)} is not above the foot gear ${JSON.stringify(gear)}`);
        });

        test('New… from that menu opens #new-menu inside the viewport too', async () => {
            ready();
            if (!await page.locator('#primary-menu').isVisible()) await page.locator('#btn-primary-menu').click();
            await page.locator('#primary-menu .popover-item', { hasText: 'New…' }).click({ timeout: 5000 });
            await page.waitForSelector('#new-menu', { state: 'visible', timeout: 5000 });
            await assertInViewport(page, '#new-menu', vp);
            await page.evaluate(() => window.chela.hideNewMenu());
        });
    });
}

describe('CMX-398 phone 390×844: the "+" New-session menu is still wholly on screen', { skip: why }, () => {
    const vp = { width: 390, height: 844 };
    let page, context;
    const ready = setup(async () => {
        ({ page, context } = await openDashboard(browser, vp));
        await page.waitForSelector('.agent-row', { state: 'attached', timeout: 15000 });
        await stable(page);
    });
    after(() => context && context.close());

    test('#new-menu from #btn-new-mobile is inside the 390×844 viewport, below the "+"', async () => {
        ready();
        await page.locator('#btn-new-mobile').click();
        await page.waitForSelector('#new-menu', { state: 'visible', timeout: 5000 });
        const menu = await assertInViewport(page, '#new-menu', vp);
        const plus = await box(page, '#btn-new-mobile');
        assert.ok(menu.y >= plus.y + plus.height - px, `#new-menu ${JSON.stringify(menu)} is not below the "+"`);
    });
});
