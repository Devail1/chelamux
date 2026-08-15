// CMX-290 — the shared "boot the REAL dashboard in a REAL DOM" bootstrap.
//
// Three separate 2026-08-14 PRs (CMX-279's six rework rounds, plus the
// duplicated boot block this file replaces) all found the same shape: a
// dashboard test that calls a render function directly with hand-picked
// arguments proves the function's OWN logic, but not the boundary a user's
// browser actually crosses — a real click, on a real DOM built from the real
// `index.html`, running the real `main.js` module graph. Getting that
// boundary right meant ~20 lines of jsdom plumbing (globalThis property
// defines — see the `navigator`-is-getter-only note below — matchMedia/canvas
// stubs, a fetch stub, the browser-faithful `main.js`-first import order) that
// had drifted into 8+ near-but-not-quite-identical copies across
// tests/*.test.mjs before this file existed (verified via
// `grep -rc 'new JSDOM(' tests/`). Each copy was a chance to get one line
// subtly wrong — e.g. forgetting `TERMINALS_ENABLED`, or a `fetch` stub that
// doesn't resolve `/api/agents/context` to an array — and silently fall back
// to testing component internals instead of the real boundary. This is the
// one copy; everything else imports it.
//
// Two exports:
//   - bootDashboardDom(opts): the jsdom + module-graph boot itself.
//   - sliceTemplate(startMarker, endMarker): pulls a fragment straight out of
//     the REAL templates/index.html (byte-identical to what Flask serves),
//     for callers that want their fixture to be the shipped markup instead of
//     a hand-typed copy that can silently drift from it — the other half of
//     the same "boundary a user reaches" gap (see tests/kanban_task_modal_wiring.test.mjs).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

const HELPERS_DIR = dirname(fileURLToPath(import.meta.url));
const DASHBOARD_ROOT = join(HELPERS_DIR, '..', '..', 'chela', 'dashboard');
const TEMPLATE_HTML = readFileSync(join(DASHBOARD_ROOT, 'templates', 'index.html'), 'utf8');

/** Pull a fragment out of the REAL index.html between two literal markers
 * (inclusive of both). Throws — loudly, at fixture-build time, not as a
 * quiet empty string — if either marker has moved, exactly like
 * tests/dashboard_default_view.test.mjs's original inline version did. */
export function sliceTemplate(startMarker, endMarker) {
    const start = TEMPLATE_HTML.indexOf(startMarker);
    if (start < 0) {
        throw new Error(`sliceTemplate: start marker not found in templates/index.html: ${JSON.stringify(startMarker)}`);
    }
    const endAt = TEMPLATE_HTML.indexOf(endMarker, start);
    if (endAt < 0) {
        throw new Error(`sliceTemplate: end marker not found (after the start marker) in templates/index.html: ${JSON.stringify(endMarker)}`);
    }
    return TEMPLATE_HTML.slice(start, endAt + endMarker.length);
}

const GLOBAL_WINDOW_PROPS = ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
    'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
    'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame'];

/** Flush one microtask turn — e.g. a fetch().then(json).then(render) chain
 * queued by a poll that fires unawaited on boot — without advancing a timer. */
export function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

/**
 * Boot the real dashboard module graph (main.js as the entry — nav.js/main.js
 * is a cycle, so anything imported first would see its own `let`s in their
 * TDZ) against a real jsdom `window`, wired the same way every hand-rolled
 * copy of this bootstrap did it.
 *
 * @param {string} body - inner HTML for `<body>`. Pass a fixture, or a real
 *   fragment from `sliceTemplate()`.
 * @param {boolean} [terminalsEnabled=true] - `window.TERMINALS_ENABLED`, read
 *   by util.js as `!== false` (so EXPLICITLY false is required to reproduce a
 *   terminals-off deployment — merely leaving it unset defaults true).
 * @param {boolean|() => boolean} [phone=false] - matchMedia's
 *   `(max-width: 768px)` answer. Pass a function (not a plain boolean) if a
 *   test needs to flip phone/desktop mode AFTER boot without a re-import —
 *   the closure below calls it live, on every matchMedia() call.
 * @param {boolean} [canvasStub=false] - jsdom ships no <canvas>; set this for
 *   any fixture that reaches code painting one (e.g. util.js's favicon badge).
 * @param {(url) => Promise<Response>} [fetchImpl] - defaults to a blanket
 *   `{}` 200 for every URL.
 * @param {Record<string,string>} [seedLocalStorage] - written to
 *   `localStorage` BEFORE the module graph loads, for module-scope restores
 *   (nav.js's collapsed-sidebar read, for one) that only run once at import
 *   time, not on demand.
 * @param {string[]} [extraModules] - dashboard module paths (relative to
 *   `chela/dashboard/static/js/`, e.g. `'kanban.js'`) to import AFTER
 *   main.js and return, keyed by their filename minus `.js`.
 * @returns {Promise<{dom: JSDOM, chela: object, modules: Record<string,object>}>}
 */
export async function bootDashboardDom({
    body = '',
    terminalsEnabled = true,
    phone = false,
    canvasStub = false,
    fetchImpl = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    seedLocalStorage,
    extraModules = [],
} = {}) {
    const dom = new JSDOM(`<!doctype html><html><body>${body}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    for (const k of GLOBAL_WINDOW_PROPS) {
        // defineProperty, NOT assignment: from node 21 `globalThis.navigator` has
        // only a getter, and plain assignment throws.
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    const phoneMatches = typeof phone === 'function' ? phone : () => phone;
    dom.window.matchMedia = q => ({
        media: q, matches: phoneMatches() && /max-width:\s*768px/.test(q),
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    });
    if (canvasStub) {
        dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
            get: (_t, k) => (k === 'canvas' ? null : () => {}),
        });
        dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    }
    if (seedLocalStorage) {
        for (const [k, v] of Object.entries(seedLocalStorage)) dom.window.localStorage.setItem(k, v);
    }
    globalThis.fetch = fetchImpl;
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;   // main.js arms poll timers a test has no use for
    globalThis.TERMINALS_ENABLED = dom.window.TERMINALS_ENABLED = terminalsEnabled;

    await import('../../chela/dashboard/static/js/main.js');
    const modules = {};
    for (const path of extraModules) {
        const name = path.replace(/\.js$/, '');
        modules[name] = await import(`../../chela/dashboard/static/js/${path}`);
    }
    return { dom, chela: globalThis.window.chela, modules };
}

/** Compile a REAL rendered element's `onclick="..."` attribute and run it
 * against the REAL `window.chela` — the same two hops
 * (attribute -> window.chela -> function) a real browser click takes, just
 * compiled by this helper instead of jsdom's HTML parser (jsdom never
 * executes inline `onclick=` attributes on a dispatched click event without
 * `runScripts:"dangerously"`, which this bootstrap deliberately does not set
 * — see tests/decisions.test.mjs's WIRING GUARD note). `this` inside the
 * handler is bound to `el`, matching `_navItemHtml`'s `this.dataset.view`-style
 * attributes. Throws if there is no onclick attribute at all, or if the
 * element is `disabled` (a real click never reaches a disabled control's
 * handler either). */
export function clickOnclick(el) {
    if (!el) throw new Error('clickOnclick: element is missing');
    if (el.disabled) throw new Error('clickOnclick: element is DISABLED — a real click would not reach the handler');
    const onclick = el.getAttribute('onclick');
    if (!onclick) throw new Error('clickOnclick: element has no onclick attribute');
    return new Function('chela', onclick).call(el, globalThis.window.chela);
}
