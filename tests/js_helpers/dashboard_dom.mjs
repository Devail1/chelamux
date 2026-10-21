// CMX-290 — the shared "boot the REAL dashboard in a REAL DOM" bootstrap.
//
// Three separate 2026-08-14 PRs (CMX-279's six rework rounds, plus the
// duplicated boot block this file replaces) all found the same shape: a
// dashboard test that calls a render function directly with hand-picked
// arguments proves the function's OWN logic, but not the boundary a user's
// browser actually crosses — a real click, on a real DOM built from the real
// `index.html`, running the real legacy module graph. Getting that
// boundary right meant ~20 lines of jsdom plumbing (globalThis property
// defines — see the `navigator`-is-getter-only note below — matchMedia/canvas
// stubs, a fetch stub, the browser-faithful import order) that
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
//     the same "boundary a user reaches" gap (see tests/new_task.test.mjs).
import { cpSync, readFileSync, symlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { renderTemplateIfs } from './template_ifs.mjs';

const HELPERS_DIR = dirname(fileURLToPath(import.meta.url));
const DASHBOARD_ROOT = join(HELPERS_DIR, '..', '..', 'chela', 'dashboard');
const TEMPLATE_HTML = readFileSync(join(DASHBOARD_ROOT, 'templates', 'index.html'), 'utf8');
// CMX-215: the page's one module script is the Vite build of this entry (the islands, then the
// legacy graph booted by boot.ts), named by the manifest Flask reads (app.py app_entry). Vitest compiles
// the .tsx, so the boot below runs that entry's source, in that order.
const MANIFEST = JSON.parse(readFileSync(join(DASHBOARD_ROOT, 'static', 'app', 'manifest.json'), 'utf8'));
export const ENTRY_URL = new URL(
    `../../chela/dashboard/frontend/${Object.keys(MANIFEST).find(k => MANIFEST[k].name === 'main')}`, import.meta.url).href;
const DEFAULT_JS_ROOT = new URL('../../chela/dashboard/static/js/', import.meta.url).href;
/** CMX-278: the sidebar's state and commands (they left nav.js), for `extraModules`; it is
 * returned as `modules.sidebar`. */
export const SIDEBAR = ['sidebar', '../../frontend/src/features/sidebar/controller.ts'];
const SRC_URL = new URL('../../chela/dashboard/frontend/src/', import.meta.url).href;

/** CMX-217: index.html's boot config block (frontend/src/shared/api readBoot), for a fixture
 * page. Fields left out take readBoot's defaults, as on a page without the block. */
export function bootBlock(cfg = {}) {
    return `<script type="application/json" id="chela-boot">${JSON.stringify(cfg)}</script>`;
}

/** A private copy of the dashboard's front end under `dir` (static/js + static/collab +
 * frontend/src, laid out as in the repo so their relative imports resolve), for a suite that
 * needs pages with their own module instances. Returns the copy's `jsRoot` for
 * bootDashboardDom. node_modules is linked, not copied: React is the same either way. */
export function copyDashboard(dir) {
    for (const d of ['static/js', 'static/collab', 'frontend/src']) {
        cpSync(join(DASHBOARD_ROOT, d), join(dir, d), { recursive: true });
    }
    // CMX-228: its tsconfig too, so the copy's .tsx compiles with the same JSX runtime.
    cpSync(join(DASHBOARD_ROOT, 'frontend', 'tsconfig.json'), join(dir, 'frontend', 'tsconfig.json'));
    symlinkSync(join(DASHBOARD_ROOT, 'frontend', 'node_modules'), join(dir, 'frontend', 'node_modules'), 'dir');
    // its .tsx compile with the frontend's JSX settings (the automatic runtime), as in the repo
    cpSync(join(DASHBOARD_ROOT, 'frontend', 'tsconfig.json'), join(dir, 'frontend', 'tsconfig.json'));
    return pathToFileURL(join(dir, 'static', 'js') + '/').href;
}

/** extraModules' entry for shared/orchestrator (static/js/orchestrator.js until CMX-228). */
export const ORCHESTRATOR = ['orchestrator', '../../frontend/src/shared/orchestrator/index.ts'];

/** Pull a fragment out of the REAL index.html between two literal markers
 * (inclusive of both). Throws — loudly, at fixture-build time, not as a
 * quiet empty string — if either marker has moved, exactly like
 * tests/dashboard_default_view.test.mjs's original inline version did. */
export function sliceTemplate(startMarker, endMarker, ctx) {
    // CMX-178: a slice never carries the page's <script> tags, so the build's entry is off.
    const html = ctx ? renderTemplateIfs(TEMPLATE_HTML, { app_src: false, ...ctx }) : TEMPLATE_HTML;
    const start = html.indexOf(startMarker);
    if (start < 0) {
        throw new Error(`sliceTemplate: start marker not found in templates/index.html: ${JSON.stringify(startMarker)}`);
    }
    const endAt = html.indexOf(endMarker, start);
    if (endAt < 0) {
        throw new Error(`sliceTemplate: end marker not found (after the start marker) in templates/index.html: ${JSON.stringify(endMarker)}`);
    }
    return html.slice(start, endAt + endMarker.length);
}

const GLOBAL_WINDOW_PROPS = ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
    'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
    'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame',
    // CMX-277: the sidebar's kit menus (Radix) read these as globals
    'MutationObserver', 'ResizeObserver', 'HTMLButtonElement'];

/** Point the page globals (`window`, `document`, `localStorage`, …) at `dom`'s window —
 * what bootDashboardDom does before it loads the modules, and what a two-page suite calls
 * to switch which page the globals (and so the next call into its modules) act on. */
export function activateDom(dom) {
    // CMX-237: plus the DOM classes React and Radix reach for as globals (the diff modal
    // island's Dialog: `instanceof HTMLInputElement`, MutationObserver, a NodeFilter walk;
    // CMX-231: the kit's Select, `new DocumentFragment()`).
    const domClasses = Object.getOwnPropertyNames(dom.window)
        .filter(k => /^(HTML\w*Element|SVGElement|NodeFilter|MutationObserver|FocusEvent|PointerEvent|DocumentFragment)$/.test(k));
    for (const k of [...GLOBAL_WINDOW_PROPS, ...domClasses]) {
        // defineProperty, NOT assignment: from node 21 `globalThis.navigator` has
        // only a getter, and plain assignment throws.
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
}

/** CMX-239: a suite that hand-rolls its globals and opens React UI on Radix (the share
 * dialog) also needs the DOM constructors Radix and React reach for as globals
 * (HTMLFormElement, MutationObserver, NodeFilter, ...). Defines each of `win`'s capitalised
 * names that node lacks; never replaces one node has (Event, URL, ...). */
export function exposeDomConstructors(win) {
    for (const k of Object.getOwnPropertyNames(win)) {
        if (!/^[A-Z]/.test(k) || k in globalThis) continue;
        Object.defineProperty(globalThis, k, { value: win[k], writable: true, configurable: true });
    }
}

/** CMX-276: what the page's React islands do before the legacy graph boots (index.html loads
 * them first): a bridge on `win`, the sidebar island drawing its #sidebar-agents, the diff
 * modal and keybar islands (CMX-237), the Work view and its toasts (CMX-233), the Decisions and
 * task modal islands (CMX-228) and the chrome (CMX-219: src/chrome.tsx). From
 * src/, so a mutation of the TypeScript reaches every suite that boots the page. Each page
 * (window) gets its own bridge and root. A suite that hand-rolls its jsdom boot calls this
 * before it imports boot.ts / nav.js. `src` (a URL ending in `/`) is the frontend/src the
 * islands that register lanes are mounted from: a copied page's own (copyDashboard), so their
 * lanes (resources, approvals, the decisions log, ...) run on that page's scheduler. */
export async function mountIslands(win, src = SRC_URL) {
    const doc = win.document;
    // The page's bridge first: the chrome's stores attach to it (shared/store/agents).
    const sidebar = !!doc.getElementById('sidebar-agents');
    if (sidebar) {
        const { createBridge } = await import('../../chela/dashboard/frontend/src/shared/bridge/index.ts');
        win.chelaBridge = createBridge();
    }
    // CMX-237: the diff modal and keybar islands, where the page has their slots.
    if (doc.getElementById('diff-modal-island')) {
        const { DiffModal } = await import('../../chela/dashboard/frontend/src/features/diffmodal/index.ts');
        const { mountIsland } = await import('../../chela/dashboard/frontend/src/shared/islands/index.tsx');
        mountIsland('diff-modal-island', DiffModal, {}, doc);
    }
    // CMX-233: the Work view (Board, Runs, Schedules) and its toasts; CMX-231: the New-task dialog.
    for (const [id, feature, name] of [['panel-work', 'work', 'WorkView'], ['work-toasts', 'work', 'WorkToasts'],
        ['newtask-island', 'newtask', 'NewTask']]) {
        if (!doc.getElementById(id)) continue;
        const mod = await import(`../../chela/dashboard/frontend/src/features/${feature}/index.ts`);
        const { mountIsland } = await import('../../chela/dashboard/frontend/src/shared/islands/index.tsx');
        mountIsland(id, mod[name], {}, doc);
    }
    // CMX-219: the chrome islands (safety pills, gear menu, launcher, agent detail, overlays),
    // into whichever of their slots the page has.
    const { mountChrome } = await import(new URL('chrome.tsx', src).href);
    mountChrome(doc);
    if (doc.getElementById('keybar-island')) {
        const { mountKeybar } = await import('../../chela/dashboard/frontend/src/features/keybar/index.ts');
        mountKeybar(win);
        await flush();   // React commits the bars (and the keybar binds its pin) on its next turn
    }
    // CMX-228: the Decisions inbox (its button's slot) and the task modal islands.
    if (doc.getElementById('decisions-island')) {
        // From the page's own source tree (a copyDashboard copy's, for a suite of several pages):
        // the inbox registers its log lane with THAT tree's scheduler, the one its boot.ts runs.
        const { mountDecisions } = await import(new URL('features/decisions/index.ts', src).href);
        mountDecisions(win);
        await flush();
    }
    if (doc.getElementById('task-modal-island')) {
        const { TaskModal } = await import(new URL('features/taskmodal/index.ts', src).href);
        const { mountIsland } = await import('../../chela/dashboard/frontend/src/shared/islands/index.tsx');
        mountIsland('task-modal-island', TaskModal, {}, doc);
    }
    // A page with no sidebar has no sidebar island to mount.
    if (!sidebar) return;
    const { mountSidebar } = await import('../../chela/dashboard/frontend/src/features/sidebar/index.ts');
    mountSidebar(win, win.chelaBridge);
}

/** Flush one microtask turn — e.g. a fetch().then(json).then(render) chain
 * queued by a poll that fires unawaited on boot — without advancing a timer. */
export function flush() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

/**
 * Boot the real dashboard module graph against a real jsdom `window`, wired the
 * same way every hand-rolled copy of this bootstrap did it. The entry is what
 * Flask serves (ENTRY_URL: the islands, then boot.ts, which imports the legacy graph in the
 * order its import cycles need).
 *
 * @param {string} body - inner HTML for `<body>`. Pass a fixture, or a real
 *   fragment from `sliceTemplate()`.
 * @param {boolean} [terminalsEnabled=true] - the boot block's `terminalsEnabled`
 *   (bootBlock), what Flask renders from CHELA_TERMINALS_ENABLED.
 * @param {object} [boot] - more boot block fields (`assetVersion`, `wallTileDispatched`).
 * @param {Function} [EventSource] - the page's `EventSource` (jsdom has none, so by default
 *   the boot opens no stream).
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
 * @param {(string|[string, string])[]} [extraModules] - dashboard module paths (relative to
 *   `chela/dashboard/static/js/`, e.g. `'launcher.js'`) to import AFTER
 *   the boot and return, keyed by their filename minus `.js`; or a `[name, path]` pair for a
 *   module that moved into frontend/src (SIDEBAR, ORCHESTRATOR).
 * @param {string} [jsRoot] - a URL (ending in `/`) of the `static/js/` directory to
 *   import from. Defaults to the repo's. A suite that needs TWO independent pages (two
 *   devices — tests/sidebar_layout_shared.test.mjs) boots each from its own copy
 *   (copyDashboard), so each gets its own module instances; it switches between them with
 *   activateDom(). A copy boots its own frontend/src/boot.ts after mountIslands() (the
 *   islands import the repo's).
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
    jsRoot = DEFAULT_JS_ROOT,
    boot = {},
    EventSource,
} = {}) {
    const dom = new JSDOM(
        `<!doctype html><html><body>${bootBlock({ terminalsEnabled, ...boot })}${body}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    // jsdom has no ResizeObserver; Radix's popper only needs it to exist
    dom.window.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
    activateDom(dom);
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
    if (EventSource) dom.window.EventSource = EventSource;
    globalThis.setInterval = () => 0;   // the boot arms poll timers a test has no use for

    if (jsRoot === DEFAULT_JS_ROOT) {
        await import(ENTRY_URL);
        // A second boot in one suite meets the entry cached: its islands mounted on the FIRST
        // page only, so this page mounts its own (CMX-276).
        if (!dom.window.chelaBridge) await mountIslands(dom.window);
    } else {
        await mountIslands(dom.window, new URL('../../frontend/src/', jsRoot).href);
        await import(new URL('../../frontend/src/boot.ts', jsRoot).href);
    }
    const modules = {};
    for (const entry of extraModules) {
        const [name, path] = Array.isArray(entry) ? entry : [entry.replace(/\.js$/, ''), entry];
        modules[name] = await import(new URL(path, jsRoot).href);
    }
    return { dom, chela: globalThis.window.chela, modules };
}

/** CMX-276: click an element a React island rendered (it has no `onclick` attribute: React
 * listens at its root), with a REAL bubbling click, as a user's lands. */
export function clickIsland(el) {
    if (!el) throw new Error('clickIsland: element is missing');
    if (el.disabled) throw new Error('clickIsland: element is DISABLED — a real click would not reach the handler');
    el.dispatchEvent(new el.ownerDocument.defaultView.MouseEvent('click', { bubbles: true, cancelable: true }));
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

/** Same idiom as clickOnclick(), for an `oninput=`/`onfocus=`/... attribute —
 * compiles the REAL attribute text off the REAL element and runs it, so a
 * mutation that blanks the attribute (PR #529 round 3, mutation 4: typing
 * into #sidebar-jump-input stayed "wired" only because the test called
 * `chela.sidebarJumpInput(el)` directly, never reading the attribute the
 * browser actually fires) fails here by construction — there is nothing to
 * compile once the attribute is empty. `this`/`event` inside the handler are
 * bound the same way a real DOM event dispatch would bind them. */
export function fireInlineHandler(el, attr) {
    if (!el) throw new Error(`fireInlineHandler: element is missing (attr=${attr})`);
    const code = el.getAttribute(attr);
    if (!code) throw new Error(`fireInlineHandler: element has no ${attr} attribute`);
    return new Function('chela', 'event', code).call(el, globalThis.window.chela, { target: el, currentTarget: el });
}
