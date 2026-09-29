// CMX-397 — boots the REAL terminals.js against the terminals panel cut from the
// REAL templates/index.html, for the collapsible-toolbar suites.
//
// terminals.js reads the toolbar's resting state ONCE, the first time it builds
// the picker, so one Node process can only ever observe ONE stored
// expand/collapse choice. Each suite that needs a different starting
// localStorage is its own *.test.mjs (its own process) calling bootWall().
import { JSDOM } from 'jsdom';   // needs `pnpm install` — tests/test_js_suites.py enforces it
import { renderShell } from './browser/fixture.mjs';

// The terminals panel exactly as the real template ships it.
const PANEL = new JSDOM(renderShell()).window.document.getElementById('panel-terminals').outerHTML;

const AGENTS = ['@1', '@2', '@3'].map((wid, i) => ({
    name: `a${i}`, window_id: wid, online: true, session_status: 'idle', claude_running: true,
}));

function fakeFetch(url) {
    const path = String(url);
    const body = path.endsWith('/api/agents') ? AGENTS
        : path.endsWith('/api/rooms') ? { rooms: {}, pending: [] }
            : path.startsWith('/api/term/ready') ? { ready: true } : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {},
        removeWidget(el, removeDOM) { if (removeDOM !== false) el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

// storage: the localStorage the page loads with (null value = key absent).
export async function bootWall(storage) {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        // runScripts so the template's INLINE onclick attributes really fire — a
        // preset click must reach chela.applyGridLayout the way a browser's does.
        // The panel carries no <script>, so nothing else is evaluated.
        { url: 'http://localhost:5005/', pretendToBeVisual: true, runScripts: 'dangerously' });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    localStorage.setItem('pc_term_mode', 'wall');
    for (const [k, v] of Object.entries(storage)) {
        if (v === null) localStorage.removeItem(k);
        else localStorage.setItem(k, v);
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
        get: (_t, k) => (k === 'canvas' ? null : () => {}),
    });
    dom.window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    await import('../chela/dashboard/static/js/main.js');
    const util = await import('../chela/dashboard/static/js/util.js');
    const terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
    return dom;
}
