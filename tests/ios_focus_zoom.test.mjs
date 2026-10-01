// CMX-409 — iPhone Safari zoomed the dashboard in whenever an input got focus
// (Settings search, the Jump palette, the new-session form, a terminal), and
// the user had to pinch back out. iOS auto-zooms on focus of any input /
// textarea / select whose COMPUTED font-size is under 16px — including xterm's
// invisible `.xterm-helper-textarea`, which takes the terminal's keyboard focus.
//
// The fix is CSS scoped to `@media (pointer: coarse)`, so these guards resolve
// the REAL stylesheets through cssForViewport at a 390px phone (coarse pointer)
// and a 1440px desktop (fine pointer) — jsdom alone ignores every @media rule.
//
//   1. 🔴 PHONE: every input/textarea/select in the REAL dashboard — the whole
//      index.html shell, plus the Settings drawer as nav.js actually renders it,
//      plus the JS-built controls (pane rename, share dialog, decisions chip) —
//      computes to >= 16px. A control with no font-size at all FAILS (the UA
//      default is ~13.3px, which zooms).
//   2. 🔴 PHONE: the xterm helper textarea computes >= 16px in every page that
//      hosts a terminal: the ttyd page (static/term-touch.css, injected by
//      app.py term_http — tests/test_ios_focus_zoom_shim.py proves it is served)
//      and the collab relay's joiner page.
//   3. ✅ DESKTOP: the controls keep their smaller sizes (the fix must not leak
//      out of the coarse-pointer scope).
//   4. 🔴 NO viewport-meta shortcut: `maximum-scale=1` / `user-scalable=no` also
//      stops the focus zoom, but kills pinch-zoom (Android too) — an
//      accessibility regression. Both viewport metas must leave zoom enabled.
//
// Run: node --test tests/ios_focus_zoom.test.mjs (pytest runs it via
// tests/test_js_suites.py; needs `pnpm install` for jsdom).
import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { bootDashboardDom, flush } from './js_helpers/dashboard_dom.mjs';
import { cssForViewport, PHONE, DESKTOP } from './js_helpers/css_viewport.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(here, '..');
const read = p => fs.readFileSync(path.join(ROOT, p), 'utf8');
const DASH_HTML = read('chela/dashboard/templates/index.html');
const DASH_CSS = read('chela/dashboard/static/style.css');
const TERM_TOUCH_CSS = read('chela/dashboard/static/term-touch.css');
const RELAY_HTML = read('chela/collab-relay/public/index.html');
const XTERM_CSS = read('chela/collab-relay/public/vendor/xterm.css');

const MIN_PX = 16;
const CONTROLS = 'input, textarea, select';

// Controls built by JS outside the Settings drawer, in the markup shape the
// modules emit (terminals.js renamePane / share dialog, decisions.js chip).
const JS_BUILT = `
<div class="term-stage"><div class="term-pane"><div class="pane-head">
  <input class="pane-title-edit" value="agent-1"></div></div></div>
<div class="share-dialog">
  <label class="sd-opt"><input type="radio" name="sd-mode" value="view" checked></label>
  <input id="sd-confirm-in" class="tsp-in">
  <div class="tsp-row"><input class="tsp-in" readonly value="code"></div>
</div>
<div class="decisions-chip"><select class="decisions-chip-rereg-select" id="decisions-reregister-wid">
  <option>@1</option></select></div>`;

// Enough of each settings endpoint for renderSettings() to build its rows.
const TIMING = { knobs: [{ key: 'poll_s', env: 'CHELA_POLL', label: 'Poll', unit: 's',
    default: 5, stored: '', effective: 5, source: 'default', restart_required: false }] };
const DISPATCH = { knobs: [
    { key: 'max_reworks', env: 'CHELA_MAX_REWORKS', label: 'Rework cap', unit: '',
        default: 2, stored: '', effective: 2, source: 'default', restart_required: false },
    { key: 'merge_base', env: 'CHELA_MERGE_BASE', label: 'Base', unit: '',
        default: 'dev', stored: '', effective: 'dev', source: 'default', restart_required: false },
    { key: 'judge_enabled', env: 'CHELA_JUDGE', label: 'Judge', unit: '',
        default: true, stored: '', effective: true, source: 'default', restart_required: false },
] };
function fetchImpl(url) {
    const u = String(url);
    const body = u.endsWith('/api/config/timing') ? TIMING
        : u.endsWith('/api/config/dispatch') ? DISPATCH
        : u.includes('/api/agents') ? [] : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

// The REAL <body> of index.html with its Jinja tags dropped (terminals on).
function templateBody() {
    const m = DASH_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/);
    assert.ok(m, 'index.html has no <body>');
    return m[1].replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '');
}

let win, doc, sheet;
before(async () => {
    const { dom } = await bootDashboardDom({ body: templateBody() + JS_BUILT, fetchImpl, canvasStub: true });
    win = dom.window;
    doc = win.document;
    win.chela.toggleSettings();
    for (let i = 0; i < 4; i++) await flush();   // _loadTimingSettings/_loadDispatchSettings are async
    sheet = doc.createElement('style');
    doc.head.appendChild(sheet);
});

// A control's computed font-size in px. Unset (jsdom reports its initial value,
// `medium`) ⇒ a browser's UA default for form controls, 13.333px — exactly what
// iOS zooms on, so never a pass.
function fontPx(w, el) {
    const v = w.getComputedStyle(el).fontSize;
    if (!v || v === 'medium') return 13.333;
    const m = String(v).match(/^(\d+(?:\.\d+)?)px$/);
    assert.ok(m, `cannot evaluate font-size ${JSON.stringify(v)} on ${describe(el)}`);
    return Number(m[1]);
}
function describe(el) {
    return `<${el.tagName.toLowerCase()}${el.id ? ` id="${el.id}"` : ''}${el.className ? ` class="${el.className}"` : ''}>`;
}
function controls() {
    const all = [...doc.querySelectorAll(CONTROLS)];
    // Vacuity guard: the shell + drawer + JS-built set must actually be here.
    for (const sel of ['#settings-search', '#palette-input', '#sidebar-jump-input', '#init-path',
        '#sched-prompt', '#modal-msg-text', '#sched-type', '.s-input', '.s-select',
        '.s-timing-input', '.s-dispatch-input', '.pane-title-edit', '#sd-confirm-in',
        '.decisions-chip-rereg-select']) {
        assert.ok(doc.querySelector(sel), `fixture lost ${sel} — the guard would pass vacuously`);
    }
    assert.ok(all.length >= 25, `only ${all.length} controls mounted — the settings drawer did not render`);
    return all;
}

test('phone (coarse pointer): every dashboard input/textarea/select computes >= 16px', () => {
    sheet.textContent = cssForViewport(DASH_CSS, PHONE);
    const small = controls().map(el => [describe(el), fontPx(win, el)]).filter(([, px]) => px < MIN_PX);
    assert.deepEqual(small, [], 'these controls would make iOS Safari zoom the page on focus');
});

test('desktop (fine pointer): the controls keep their smaller sizes', () => {
    sheet.textContent = cssForViewport(DASH_CSS, DESKTOP);
    for (const sel of ['#settings-search', '#palette-input', '#sidebar-jump-input', '.s-select', '#sched-prompt']) {
        const px = fontPx(win, doc.querySelector(sel));
        assert.ok(px < MIN_PX, `${sel} is ${px}px on desktop — the 16px fix leaked out of @media (pointer: coarse)`);
    }
});

// A fresh jsdom page for a stylesheet that hosts an xterm.
function helperTextareaPx(css, vp, extraBody = '') {
    const dom = new JSDOM(`<!doctype html><html><head><style>${cssForViewport(css, vp)}</style></head><body>
        <div class="xterm"><textarea class="xterm-helper-textarea"></textarea></div>${extraBody}</body></html>`);
    const w = dom.window;
    return { px: fontPx(w, w.document.querySelector('.xterm-helper-textarea')), win: w };
}

test('phone: the ttyd terminal page (term-touch.css) gives the xterm helper textarea >= 16px', () => {
    // xterm.css first, exactly as the ttyd page loads it before our injected <style>.
    const { px } = helperTextareaPx(XTERM_CSS + '\n' + TERM_TOUCH_CSS, PHONE);
    assert.ok(px >= MIN_PX, `.xterm-helper-textarea is ${px}px in the ttyd page — tapping a terminal zooms iOS`);
});

test('phone: the dashboard stylesheet also covers an in-document xterm helper textarea', () => {
    const { px } = helperTextareaPx(XTERM_CSS + '\n' + DASH_CSS, PHONE);
    assert.ok(px >= MIN_PX, `.xterm-helper-textarea is ${px}px under style.css`);
});

test('phone: the collab relay joiner page — helper textarea and gate inputs >= 16px', () => {
    const styles = [...RELAY_HTML.matchAll(/<style>([\s\S]*?)<\/style>/g)].map(m => m[1]).join('\n');
    const gate = RELAY_HTML.match(/<div id="gate">[\s\S]*?<\/div>\s*<\/div>/);
    assert.ok(gate, 'relay #gate markup not found');
    const { px, win: w } = helperTextareaPx(XTERM_CSS + '\n' + styles, PHONE, gate[0]);
    assert.ok(px >= MIN_PX, `relay .xterm-helper-textarea is ${px}px`);
    const inputs = [...w.document.querySelectorAll(CONTROLS)];
    assert.ok(inputs.length >= 2, 'relay gate inputs not mounted');
    for (const el of inputs) assert.ok(fontPx(w, el) >= MIN_PX, `relay ${describe(el)} is ${fontPx(w, el)}px`);
});

test('desktop: the terminal pages leave the helper textarea alone', () => {
    const { px } = helperTextareaPx(XTERM_CSS + '\n' + TERM_TOUCH_CSS, DESKTOP);
    assert.ok(px < MIN_PX, `term-touch.css applied ${px}px on desktop — it must be coarse-pointer only`);
});

// --- 4. no pinch-zoom kill switch -------------------------------------------
function viewportMeta(html, where) {
    const m = html.match(/<meta\s+name=["']viewport["']\s+content=["']([^"']*)["']/i);
    assert.ok(m, `${where}: no viewport meta`);
    return Object.fromEntries(m[1].split(',').map(kv => kv.split('=').map(s => s.trim().toLowerCase())));
}
for (const [where, html] of [['dashboard index.html', DASH_HTML], ['collab relay index.html', RELAY_HTML]]) {
    test(`${where}: viewport meta leaves pinch-zoom enabled (no maximum-scale=1 / user-scalable=no)`, () => {
        const vp = viewportMeta(html, where);
        assert.ok(!('maximum-scale' in vp) || Number(vp['maximum-scale']) > 1,
            `${where}: maximum-scale=${vp['maximum-scale']} disables zoom — fix the font-size instead`);
        assert.ok(!['no', '0'].includes(vp['user-scalable']),
            `${where}: user-scalable=${vp['user-scalable']} disables pinch-zoom`);
    });
}
